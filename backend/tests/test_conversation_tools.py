import json

import pytest
from fastapi.testclient import TestClient

from jarvis import conversation_tools
from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.provider import Completion


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
    assert "mail__send" not in prompts[0][1]["content"]
    assert "browser_read" not in prompts[0][1]["content"]
    assert "Liest die heutigen Termine" in prompts[0][1]["content"]
    assert len(prompts) == 1


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
