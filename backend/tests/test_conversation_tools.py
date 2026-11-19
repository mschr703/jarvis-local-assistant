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
             "schema": {"type": "object", "properties": {}, "required": []}}
WRITE_TOOL = {"name": "mail__send", "scope": "local_module", "effect": "write",
              "schema": {"type": "object", "properties": {}}}
BROWSER_TOOL = {"name": "browser_read", "scope": "browser", "effect": "read",
                "schema": {"type": "object", "properties": {}}}


@pytest.mark.asyncio
async def test_chat_selects_local_module_read_and_keeps_result_as_data(tmp_path, monkeypatch):
    answers = iter(['{"tool":"calendar__list_today","args":{}}',
                    '{"tool":"none","args":{}}'])
    prompts = []

    async def choose(_db, settings, purpose, messages):
        assert settings.provider == "ollama" and purpose == "chat_tool_select"
        prompts.append(messages)
        return Completion(next(answers), 5, 5, 0.0, "local_no_api_charge")

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
    assert "Zahnarzt" in prompts[1][1]["content"]


@pytest.mark.asyncio
async def test_chat_never_executes_unoffered_or_write_tool(tmp_path, monkeypatch):
    async def choose(_db, _settings, _purpose, _messages):
        return Completion('{"tool":"mail__send","args":{}}', None, None, 0.0,
                          "local_no_api_charge")

    monkeypatch.setattr(conversation_tools, "generate_text", choose)
    engine = FakeEngine([READ_TOOL, WRITE_TOOL])
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    result = await conversation_tools.gather_tool_context(None, settings, engine,
                                                           "Schick die Mail", [])
    assert engine.calls == [] and result.uses == []
    assert "Behaupte keine aktuellen Termine" in result.prompt
    cloud = settings.model_copy(update={"provider": "openrouter"})
    with pytest.raises(ValueError, match="nur mit Ollama"):
        await conversation_tools.gather_tool_context(None, cloud, engine, "Hallo", [])


def test_tool_choice_rejects_invalid_arguments_and_hallucinated_names():
    allowed = {"calendar__list_today"}
    assert conversation_tools.parse_choice('{"tool":"none","args":{}}', allowed) is None
    assert conversation_tools.parse_choice(
        '```json\n{"tool":"calendar__list_today","args":{}}\n```', allowed
    ) == ("calendar__list_today", {})
    for value in ('{"tool":"mail__send","args":{}}',
                  '{"tool":"calendar__list_today","args":[]}',
                  '{"tool":"none","args":{"force":true}}'):
        with pytest.raises(ValueError):
            conversation_tools.parse_choice(value, allowed)


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
