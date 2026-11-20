import json

import pytest
from fastapi.testclient import TestClient

from jarvis import conversation_tools
from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.provider import Completion, ProviderError


class FakeEngine:
    def __init__(self, capabilities):
        self.available = capabilities
        self.calls = []

    def capabilities(self):
        return self.available

    def start(self, name, args):
        self.calls.append((name, args))
        return {"id": "run-1", "status": "succeeded",
                "result_json": json.dumps({"notes": ["Morgen 15 Uhr Zahnarzt"]})}


class FakeRunDb:
    def get_run(self, run_id):
        assert run_id == "prior-project-run"
        return {"tool_name": "context_lookup__search_personal_context",
                "status": "succeeded", "effect": "read",
                "args_json": '{"query":"Projekt Orion","kind":"project"}'}


READ_TOOL = {"name": "calendar__list_today", "scope": "local_module", "effect": "read",
             "description": "Liest die heutigen Termine",
             "schema": {"type": "object", "properties": {}, "required": []}}
WRITE_TOOL = {"name": "mail__send", "scope": "local_module", "effect": "write",
              "schema": {"type": "object", "properties": {}}}
BROWSER_TOOL = {"name": "browser_read", "scope": "browser", "effect": "read",
                "schema": {"type": "object", "properties": {}}}


@pytest.mark.asyncio
async def test_chat_selects_local_module_read_and_keeps_result_as_data(tmp_path, monkeypatch):
    prompts = []

    async def choose(_db, settings, purpose, messages, *, ollama_format):
        assert settings.provider == "ollama" and purpose == "chat_tool_select"
        assert ollama_format == "json"
        prompts.append(messages)
        return Completion('{"tools":[{"tool":"calendar__list_today","args":{}}]}',
                          5, 5, 0.0, "local_no_api_charge")

    monkeypatch.setattr(conversation_tools, "generate_text", choose)
    engine = FakeEngine([READ_TOOL, WRITE_TOOL, BROWSER_TOOL])
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    result = await conversation_tools.gather_tool_context(
        None, settings, engine, "Welche Termine habe ich?", [])
    assert engine.calls == [("calendar__list_today", {})]
    assert result.uses == [{"name": "calendar__list_today", "run_id": "run-1",
                            "status": "succeeded"}]
    assert "Zahnarzt" in result.prompt and "unvertrauenswürdige" in result.prompt
    assert '"result": {"notes":' in result.prompt
    assert "fehlgeschlagenes" not in result.prompt
    assert "mail__send" not in prompts[0][1]["content"]
    assert "browser_read" not in prompts[0][1]["content"]
    assert "Liest die heutigen Termine" in prompts[0][1]["content"]
    assert len(prompts) == 1


@pytest.mark.asyncio
async def test_no_tool_choice_preserves_normal_general_chat(tmp_path, monkeypatch):
    async def choose(_db, _settings, _purpose, _messages, *, ollama_format):
        return Completion('{"tools":[]}', None, None, 0.0, "local_no_api_charge")

    monkeypatch.setattr(conversation_tools, "generate_text", choose)
    engine = FakeEngine([READ_TOOL])
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    result = await conversation_tools.gather_tool_context(
        None, settings, engine, "Was bedeutet Hypothese?", [])
    assert result.state == "not_needed" and result.prompt == ""
    assert result.uses == [] and engine.calls == []


@pytest.mark.asyncio
async def test_general_explanation_without_personal_data_skips_planner(tmp_path, monkeypatch):
    async def choose(*_args, **_kwargs):
        raise AssertionError("Begriffserklärung braucht keine lokalen Werkzeugdaten")

    monkeypatch.setattr(conversation_tools, "generate_text", choose)
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    engine = FakeEngine([READ_TOOL])
    previous_topic = [
        {"role": "user", "content": "Was weißt du über Projekt Orion?", "status": "complete"},
        {"role": "assistant", "content": "Bericht lesen", "status": "complete",
         "tool_uses": [{"name": READ_TOOL["name"], "status": "succeeded",
                        "run_id": "prior-project-run"}]},
    ]
    for question in ("Was bedeutet das Wort Hypothese?",
                     "Was ist der Unterschied zwischen Wunsch und Annahme?",
                     "Erkläre den Unterschied einfach."):
        result = await conversation_tools.gather_tool_context(
            None, settings, engine, question, previous_topic)
        assert result.state == "not_needed" and result.uses == []


@pytest.mark.asyncio
async def test_general_wording_with_personal_data_still_uses_planner(tmp_path, monkeypatch):
    async def choose(_db, _settings, _purpose, _messages, *, ollama_format):
        return Completion('{"tools":[{"tool":"calendar__list_today","args":{}}]}',
                          None, None, 0.0, "local_no_api_charge")

    monkeypatch.setattr(conversation_tools, "generate_text", choose)
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    result = await conversation_tools.gather_tool_context(
        None, settings, FakeEngine([READ_TOOL]),
        "Erkläre mir meine Termine heute.", [])
    assert result.uses[0]["name"] == "calendar__list_today"


@pytest.mark.asyncio
async def test_explain_ambiguous_name_still_checks_local_sources(tmp_path, monkeypatch):
    calls = []

    async def choose(_db, _settings, _purpose, _messages, *, ollama_format):
        calls.append(ollama_format)
        return Completion('{"tools":[]}', None, None, 0.0, "local_no_api_charge")

    monkeypatch.setattr(conversation_tools, "generate_text", choose)
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    result = await conversation_tools.gather_tool_context(
        None, settings, FakeEngine([READ_TOOL]), "Erkläre Orion", [])
    assert calls == ["json"] and result.state == "not_needed"


@pytest.mark.asyncio
async def test_optional_local_planner_model_is_used_for_selection_only(tmp_path, monkeypatch):
    seen = []

    async def choose(_db, planner_settings, purpose, _messages, *, ollama_format):
        seen.append((planner_settings.ollama_model, purpose, ollama_format))
        return Completion('{"tools":[{"tool":"calendar__list_today","args":{}}]}',
                          None, None, 0.0, "local_no_api_charge")

    monkeypatch.setattr(conversation_tools, "generate_text", choose)
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="answer-model",
                        JARVIS_OLLAMA_PLANNER_MODEL="small-planner")
    engine = FakeEngine([READ_TOOL])
    result = await conversation_tools.gather_tool_context(
        None, settings, engine, "Welche Termine habe ich?", [])
    assert seen == [("small-planner", "chat_tool_select", "json")]
    assert settings.ollama_model == "answer-model"
    assert result.uses[0]["name"] == "calendar__list_today"


@pytest.mark.asyncio
async def test_missing_optional_planner_retries_default_locally(tmp_path, monkeypatch):
    seen = []

    async def choose(_db, planner_settings, _purpose, _messages, *, ollama_format):
        seen.append(planner_settings.ollama_model)
        if len(seen) == 1:
            raise ProviderError("unavailable", "Planermodell nicht installiert")
        return Completion('{"tools":[{"tool":"calendar__list_today","args":{}}]}',
                          None, None, 0.0, "local_no_api_charge")

    monkeypatch.setattr(conversation_tools, "generate_text", choose)
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="answer-model",
                        JARVIS_OLLAMA_PLANNER_MODEL="missing-planner")
    engine = FakeEngine([READ_TOOL])
    result = await conversation_tools.gather_tool_context(
        None, settings, engine, "Welche Termine habe ich?", [])
    assert seen == ["missing-planner", "answer-model"]
    assert result.state == "used"


@pytest.mark.asyncio
async def test_broken_optional_planner_does_not_repeat_slow_failure(tmp_path, monkeypatch):
    seen = []

    async def choose(_db, planner_settings, _purpose, _messages, *, ollama_format):
        seen.append(planner_settings.ollama_model)
        raise ProviderError("timeout", "Planer zu langsam")

    monkeypatch.setattr(conversation_tools, "generate_text", choose)
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="answer-model",
                        JARVIS_OLLAMA_PLANNER_MODEL="slow-planner")
    result = await conversation_tools.gather_tool_context(
        None, settings, FakeEngine([READ_TOOL]), "Welche Termine habe ich?", [])
    assert seen == ["slow-planner"]
    assert result.state == "failed"


@pytest.mark.asyncio
async def test_hypothetical_new_scenario_does_not_inherit_previous_tool_topic(
        tmp_path, monkeypatch):
    async def choose(*_args, **_kwargs):
        raise AssertionError("Selbstständige hypothetische Frage braucht keinen Datenplaner")

    monkeypatch.setattr(conversation_tools, "generate_text", choose)
    source = "day_context__find_free_window"
    history = [
        {"role": "user", "content": "Wann habe ich heute frei?", "status": "complete"},
        {"role": "assistant", "content": "Ab 19:14 Uhr frei.", "status": "complete",
         "tool_uses": [{"name": source, "status": "succeeded", "run_id": "run-1"}]},
    ]
    engine = FakeEngine([{**READ_TOOL, "name": source}])
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    result = await conversation_tools.gather_tool_context(
        None, settings, engine,
        "Angenommen, ich muss um 14:00 ankommen und der Weg dauert 20 Minuten. Wann los?",
        history)
    assert result.state == "not_needed" and engine.calls == []


@pytest.mark.asyncio
async def test_hypothetical_question_with_personal_calendar_still_uses_planner(
        tmp_path, monkeypatch):
    async def choose(_db, _settings, _purpose, messages, *, ollama_format):
        assert "heute" in json.loads(messages[1]["content"])["question"]
        return Completion('{"tools":[{"tool":"calendar__list_today","args":{}}]}',
                          None, None, 0.0, "local_no_api_charge")

    monkeypatch.setattr(conversation_tools, "generate_text", choose)
    engine = FakeEngine([READ_TOOL])
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    result = await conversation_tools.gather_tool_context(
        None, settings, engine,
        "Angenommen, mein Termin heute verschiebt sich: Welche Termine habe ich?", [])
    assert result.uses[0]["name"] == "calendar__list_today"


@pytest.mark.asyncio
async def test_planner_keeps_more_context_when_tool_descriptions_are_long(tmp_path, monkeypatch):
    async def choose(_db, planner_settings, _purpose, _messages, *, ollama_format):
        assert 4096 < planner_settings.ollama_context_tokens <= 8192
        return Completion('{"tools":[]}', None, None, 0.0, "local_no_api_charge")

    monkeypatch.setattr(conversation_tools, "generate_text", choose)
    engine = FakeEngine([{**READ_TOOL, "description": "lange Beschreibung " * 500}])
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    result = await conversation_tools.gather_tool_context(db=None, settings=settings,
                                                           engine=engine, question="Hallo", history=[])
    assert result.state == "not_needed"


@pytest.mark.asyncio
async def test_chat_never_executes_unoffered_or_write_tool(tmp_path, monkeypatch):
    async def choose(_db, _settings, _purpose, _messages, *, ollama_format):
        assert "mail__send" not in str(ollama_format)
        return Completion('{"tools":[{"tool":"mail__send","args":{}}]}', None, None, 0.0,
                          "local_no_api_charge")

    monkeypatch.setattr(conversation_tools, "generate_text", choose)
    engine = FakeEngine([READ_TOOL, WRITE_TOOL])
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    result = await conversation_tools.gather_tool_context(None, settings, engine,
                                                           "Schick die Mail", [])
    assert engine.calls == [] and result.uses == []
    assert "Werkzeugauswahl fehlgeschlagen" in result.prompt
    cloud = settings.model_copy(update={"provider": "openrouter"})
    with pytest.raises(ValueError, match="nur mit Ollama"):
        await conversation_tools.gather_tool_context(None, cloud, engine, "Hallo", [])


def test_tool_choice_rejects_invalid_arguments_and_hallucinated_names():
    allowed = {"calendar__list_today"}
    assert conversation_tools.parse_choices('{"tools":[]}', allowed) == []
    assert conversation_tools.parse_choices(
        '{"tools":[{"tool":"calendar__list_today","args":{}}]}', allowed
    ) == [("calendar__list_today", {})]
    for value in ('{"tools":[{"tool":"mail__send","args":{}}]}',
                  '{"tools":[{"tool":"calendar__list_today","args":[]}]}',
                  ('{"tools":[{"tool":"calendar__list_today","args":{}},'
                   '{"tool":"calendar__list_today","args":{}}]}'),
                  '{"tools":[{"tool":"none","args":{}}]}'):
        with pytest.raises(ValueError):
            conversation_tools.parse_choices(value, allowed)


def test_follow_up_planner_sees_previous_read_source_and_subject():
    history = [
        {"role": "user", "content": "Was weißt du über Projekt Orion?", "status": "complete"},
        {"role": "assistant", "content": "Projekt Orion: Bericht lesen.",
         "status": "complete", "tool_uses": [
             {"name": "context_lookup__search_personal_context", "run_id": "run-1",
              "status": "succeeded"}]},
    ]
    messages = conversation_tools.selection_messages(
        "Und was muss ich dafür tun?", history,
        [{"name": "context_lookup__search_personal_context", "schema": {}}],
        "Europe/Berlin")
    payload = json.loads(messages[1]["content"])
    assert "Projekt Orion" in payload["recent_conversation"][0]["content"]
    assert payload["recent_conversation"][1]["read_tools_used"] == [
        "context_lookup__search_personal_context"]
    assert payload["previous_questions"] == ["Was weißt du über Projekt Orion?"]
    assert payload["last_read_sources"] == ["context_lookup__search_personal_context"]
    assert "Anschlussfragen" in messages[0]["content"]


def test_planner_keeps_two_previous_turns_bounded_and_only_active_successful_sources():
    source = "context_lookup__search_personal_context"
    history = [
        {"role": "user", "content": "Älteres Thema", "status": "complete"},
        {"role": "assistant", "content": "Ältere Antwort", "status": "complete"},
        {"role": "user", "content": "Was weißt du über Projekt Orion?", "status": "complete"},
        {"role": "assistant", "content": "Orion: Bericht lesen. " + "X" * 500,
         "status": "complete", "tool_uses": [
             {"name": source, "status": "succeeded"},
             {"name": "disabled__secret", "status": "succeeded"}]},
        {"role": "user", "content": "Und was muss ich dafür tun?", "status": "complete"},
        {"role": "assistant", "content": "Bericht für Orion lesen.",
         "status": "complete", "tool_uses": [{"name": source, "status": "failed"}]},
        {"role": "assistant", "content": "unvollständig", "status": "interrupted"},
    ]
    messages = conversation_tools.selection_messages(
        "Und was ist danach offen?", history,
        [{"name": source, "schema": {}}], "Europe/Berlin")
    recent = json.loads(messages[1]["content"])["recent_conversation"]
    assert len(recent) == 4
    assert recent[0]["content"] == "Was weißt du über Projekt Orion?"
    assert len(recent[1]["content"]) == 120
    assert recent[1]["read_tools_used"] == [source]
    assert recent[-1]["read_tools_used"] == []
    assert "Älteres Thema" not in messages[1]["content"]
    assert "disabled__secret" not in messages[1]["content"]


def test_follow_up_does_not_reuse_source_across_an_unrelated_answer():
    source = "context_lookup__search_personal_context"
    history = [
        {"role": "assistant", "content": "Projekt Orion", "status": "complete",
         "tool_uses": [{"name": source, "status": "succeeded",
                        "run_id": "prior-project-run"}]},
        {"role": "user", "content": "Was ist eine Hypothese?", "status": "complete"},
        {"role": "assistant", "content": "Eine prüfbare Annahme.",
         "status": "complete", "tool_uses": []},
    ]
    assert conversation_tools.prior_read_for_follow_up(
        FakeRunDb(), "Was bedeutet das dazu?", history, {source}) is None


def test_follow_up_does_not_skip_an_interrupted_last_answer():
    source = "context_lookup__search_personal_context"
    history = [
        {"role": "assistant", "content": "Projekt Orion", "status": "complete",
         "tool_uses": [{"name": source, "status": "succeeded",
                        "run_id": "prior-project-run"}]},
        {"role": "user", "content": "Anderes Thema", "status": "complete"},
        {"role": "assistant", "content": "", "status": "interrupted", "tool_uses": []},
    ]
    assert conversation_tools.prior_read_for_follow_up(
        FakeRunDb(), "Was ist dazu bekannt?", history, {source}) is None


def test_named_new_topic_does_not_reuse_previous_project_query():
    source = "context_lookup__search_personal_context"
    history = [{"role": "assistant", "content": "Projekt Orion", "status": "complete",
                "tool_uses": [{"name": source, "status": "succeeded",
                               "run_id": "prior-project-run"}]}]
    assert conversation_tools.prior_read_for_follow_up(
        FakeRunDb(), "Und was ist dort bei Projekt Andromeda offen?", history,
        {source}) is None
    assert conversation_tools.prior_read_for_follow_up(
        FakeRunDb(), "Und was ist bei Projekt Orion dazu offen?", history,
        {source}) == (source, {"query": "Projekt Orion", "kind": "project"})
    assert conversation_tools.prior_read_for_follow_up(
        FakeRunDb(), "Und was passiert danach?", history,
        {source}) == (source, {"query": "Projekt Orion", "kind": "project"})


@pytest.mark.asyncio
async def test_simple_follow_up_refreshes_prior_read_without_a_planner(tmp_path, monkeypatch):
    async def choose(*_args, **_kwargs):
        raise AssertionError("Bekannte Themenquelle braucht keine erneute Werkzeugwahl")

    monkeypatch.setattr(conversation_tools, "generate_text", choose)
    source = "context_lookup__search_personal_context"
    engine = FakeEngine([{**READ_TOOL, "name": source}])
    history = [{"role": "assistant", "content": "Projekt Orion", "status": "complete",
                "tool_uses": [{"name": source, "status": "succeeded",
                               "run_id": "prior-project-run"}]}]
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    result = await conversation_tools.gather_tool_context(
        FakeRunDb(), settings, engine, "Was muss ich dafür als Nächstes tun?", history)
    assert engine.calls == [(source, {"query": "Projekt Orion", "kind": "project"})]
    assert result.state == "used"
    assert "weitere Werkzeugwahl" not in result.prompt


@pytest.mark.asyncio
async def test_explicit_follow_up_refreshes_prior_read_even_if_planner_picks_generic_tools(
        tmp_path, monkeypatch):
    async def choose(_db, _settings, _purpose, _messages, *, ollama_format):
        assert _settings.ollama_context_tokens == 4096
        return Completion('{"tools":[{"tool":"day_context__list_open_tasks","args":{}},'
                          '{"tool":"day_context__list_today_blocks","args":{}}]}',
                          None, None, 0.0, "local_no_api_charge")

    monkeypatch.setattr(conversation_tools, "generate_text", choose)
    source = "context_lookup__search_personal_context"
    engine = FakeEngine([
        {**READ_TOOL, "name": source},
        {**READ_TOOL, "name": "day_context__list_open_tasks"},
        {**READ_TOOL, "name": "day_context__list_today_blocks"},
    ])
    history = [
        {"role": "user", "content": "Was weißt du über Projekt Orion?", "status": "complete"},
        {"role": "assistant", "content": "Bericht für Orion lesen.", "status": "complete",
         "tool_uses": [{"name": source, "status": "succeeded",
                        "run_id": "prior-project-run"}]},
    ]
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    result = await conversation_tools.gather_tool_context(
        FakeRunDb(), settings, engine, "Welche Aufgaben habe ich heute dafür?", history)
    assert engine.calls == [(source, {"query": "Projekt Orion", "kind": "project"}),
                            ("day_context__list_open_tasks", {})]
    assert [use["name"] for use in result.uses] == [source, "day_context__list_open_tasks"]


@pytest.mark.asyncio
async def test_follow_up_runs_selected_prior_source_first_without_duplicate(tmp_path, monkeypatch):
    source = "context_lookup__search_personal_context"

    async def choose(_db, _settings, _purpose, _messages, *, ollama_format):
        return Completion('{"tools":[{"tool":"day_context__list_open_tasks","args":{}},'
                          '{"tool":"context_lookup__search_personal_context",'
                          '"args":{"query":"was noch offen bei Projekt Orion",'
                          '"kind":"project"}}]}',
                          None, None, 0.0, "local_no_api_charge")

    monkeypatch.setattr(conversation_tools, "generate_text", choose)
    engine = FakeEngine([{**READ_TOOL, "name": source},
                         {**READ_TOOL, "name": "day_context__list_open_tasks"}])
    history = [{"role": "assistant", "content": "Projekt Orion", "status": "complete",
                "tool_uses": [{"name": source, "status": "succeeded",
                               "run_id": "prior-project-run"}]}]
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    await conversation_tools.gather_tool_context(
        FakeRunDb(), settings, engine, "Welche Aufgaben sind dazu offen?", history)
    assert engine.calls == [(source, {"query": "Projekt Orion", "kind": "project"}),
                            ("day_context__list_open_tasks", {})]


@pytest.mark.asyncio
async def test_explicit_topic_switch_does_not_force_previous_source(tmp_path, monkeypatch):
    async def choose(_db, _settings, _purpose, _messages, *, ollama_format):
        return Completion('{"tools":[{"tool":"day_context__list_today_blocks","args":{}}]}',
                          None, None, 0.0, "local_no_api_charge")

    monkeypatch.setattr(conversation_tools, "generate_text", choose)
    source = "context_lookup__search_personal_context"
    engine = FakeEngine([{**READ_TOOL, "name": source},
                         {**READ_TOOL, "name": "day_context__list_today_blocks"}])
    history = [{"role": "assistant", "content": "Orion", "status": "complete",
                "tool_uses": [{"name": source, "status": "succeeded",
                               "run_id": "prior-project-run"}]}]
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    result = await conversation_tools.gather_tool_context(
        FakeRunDb(), settings, engine, "Welche Termine habe ich heute?", history)
    assert engine.calls == [("day_context__list_today_blocks", {})]
    assert [use["name"] for use in result.uses] == ["day_context__list_today_blocks"]


@pytest.mark.asyncio
async def test_follow_up_read_still_works_when_planner_fails(tmp_path, monkeypatch):
    async def choose(_db, _settings, _purpose, _messages, *, ollama_format):
        raise ProviderError("timeout", "Modellantwort zu spät")

    monkeypatch.setattr(conversation_tools, "generate_text", choose)
    source = "context_lookup__search_personal_context"
    engine = FakeEngine([{**READ_TOOL, "name": source}])
    history = [{"role": "assistant", "content": "Orion", "status": "complete",
                "tool_uses": [{"name": source, "status": "succeeded",
                               "run_id": "prior-project-run"}]}]
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    result = await conversation_tools.gather_tool_context(
        FakeRunDb(), settings, engine, "Welche Termine habe ich heute dazu?", history)
    assert engine.calls == [(source, {"query": "Projekt Orion", "kind": "project"})]
    assert result.uses[0]["name"] == source
    assert "weitere Werkzeugwahl" in result.prompt


@pytest.mark.asyncio
async def test_chat_can_read_two_distinct_modules_in_one_selection(tmp_path, monkeypatch):
    async def choose(_db, _settings, _purpose, _messages, *, ollama_format):
        assert ollama_format == "json"
        return Completion('{"tools":[{"tool":"calendar__list_today","args":{}},'
                          '{"tool":"tasks__list_open","args":{}}]}',
                          10, 10, 0.0, "local_no_api_charge")

    monkeypatch.setattr(conversation_tools, "generate_text", choose)
    tasks_tool = {**READ_TOOL, "name": "tasks__list_open"}
    engine = FakeEngine([READ_TOOL, tasks_tool])
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    result = await conversation_tools.gather_tool_context(
        None, settings, engine, "Termine und Aufgaben heute?", [])
    assert engine.calls == [("calendar__list_today", {}), ("tasks__list_open", {})]
    assert len(result.uses) == 2


@pytest.mark.asyncio
async def test_module_failure_does_not_break_chat_or_claim_empty_data(tmp_path, monkeypatch):
    async def choose(_db, _settings, _purpose, _messages, *, ollama_format):
        assert ollama_format == "json"
        return Completion('{"tools":[{"tool":"calendar__list_today","args":{}}]}',
                          5, 5, 0.0, "local_no_api_charge")

    monkeypatch.setattr(conversation_tools, "generate_text", choose)
    engine = FakeEngine([READ_TOOL])
    engine.start = lambda _name, _args: (_ for _ in ()).throw(RuntimeError("broken module"))
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    result = await conversation_tools.gather_tool_context(
        None, settings, engine, "Welche Termine habe ich?", [])
    assert result.uses == []
    assert "invalid_or_unavailable" in result.prompt
    assert "beweist keine Vollständigkeit" in result.prompt
    assert "broken module" not in result.prompt


def test_chat_local_tools_cannot_be_sent_to_cloud(tmp_path):
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_PROVIDER="openrouter",
                        JARVIS_OPENROUTER_MODEL="fixture/model")
    with TestClient(create_app(settings)) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        chat_id = client.post("/chats", headers=headers).json()["id"]
        response = client.post(f"/chats/{chat_id}/stream", headers=headers,
                               json={"content": "Welche Notizen?", "use_local_tools": True,
                                     "allow_cloud": True})
        assert response.status_code == 422 and "lokales Ollama" in response.text
        assert client.get(f"/chats/{chat_id}").json()["messages"] == []
