"""Free-language action proposals never execute tools before review."""

import json

from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from jarvis.action_proposals import available_actions, parse_action, parse_clarification
from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.engine import ToolDefinition
from jarvis.provider import Completion


class DraftArgs(BaseModel):
    recipient: str = Field(min_length=3)
    body: str = Field(min_length=1)


class NoArgs(BaseModel):
    pass


class OpenArgs(BaseModel):
    item_id: str = Field(min_length=1)


def prepared(tmp_path, question="Schreibe einen Entwurf an Ada mit dem Text Hallo."):
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    chat = db.create_chat()
    db.add_message(chat["id"], "user", question)
    answer = db.add_message(chat["id"], "assistant", "Ich kann den Entwurf vorbereiten.")
    app = create_app(Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture"))
    effects = []
    app.state.engine.register(ToolDefinition(
        "create_draft", DraftArgs,
        lambda args: effects.append(args.model_dump()) or {"draft": "created"},
        scope="local_module", effect="write", description="Lokalen Entwurf erstellen",
    ))
    return app, db, chat["id"], answer["id"], effects


def csrf(client):
    return {"x-jarvis-csrf": client.get("/session").json()["csrf"]}


def test_proposal_is_reviewable_and_requires_separate_run_approval(tmp_path, monkeypatch):
    app, _db, chat_id, answer_id, effects = prepared(tmp_path)
    seen = []

    async def generate(_db, settings, purpose, messages, **kwargs):
        seen.append((settings.provider, purpose, messages, kwargs))
        return Completion(json.dumps({"action": {"tool": "create_draft", "args": {
            "recipient": "Ada", "body": "Hallo"}}}), 10, 10, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    with TestClient(app) as client:
        headers = csrf(client)
        url = f"/chats/{chat_id}/action-proposal"
        assert client.post(url, json={"message_id": answer_id}).status_code == 403
        result = client.post(url, headers=headers, json={"message_id": answer_id})
        assert result.status_code == 200, result.text
        action = result.json()["action"]
        assert action["tool_name"] == "create_draft"
        assert action["args"] == {"recipient": "Ada", "body": "Hallo"}
        assert action["effect"] == "write"
        assert action["source_uses"] == []
        assert len(action["args_hash"]) == 64
        assert effects == []
        assert client.get("/runs").json() == []
        assert seen[0][0:2] == ("ollama", "chat_action_proposal")
        assert seen[0][3] == {"ollama_format": "json"}
        assert "create_draft" in seen[0][2][-1]["content"]
        request = {"tool_name": action["tool_name"], "args": action["args"],
                   "client_request_id": "chat-action-test-001"}
        run = client.post("/runs", headers=headers, json=request).json()
        assert run["status"] == "waiting_approval"
        duplicate = client.post("/runs", headers=headers, json=request)
        assert duplicate.status_code == 200
        assert duplicate.json()["id"] == run["id"]
        changed = client.post("/runs", headers=headers, json={
            **request, "args": {"recipient": "Eve", "body": "Hallo"}})
        assert changed.status_code == 422
        wrong = client.post(f"/runs/{run['id']}/decision", headers=headers, json={
            "args_hash": "0" * 64, "approved": True})
        assert wrong.status_code == 422
        assert effects == []
        approved = client.post(f"/runs/{run['id']}/decision", headers=headers, json={
            "args_hash": run["args_hash"], "approved": True})
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "succeeded"
        assert effects == [{"recipient": "Ada", "body": "Hallo"}]
        assert client.post(f"/runs/{run['id']}/decision", headers=headers, json={
            "args_hash": run["args_hash"], "approved": True}).status_code == 422
        assert effects == [{"recipient": "Ada", "body": "Hallo"}]


def test_null_unknown_tool_and_invalid_args_do_not_create_runs(tmp_path, monkeypatch):
    app, _db, chat_id, answer_id, effects = prepared(tmp_path, "Was bedeutet Entwurf?")
    output = {"action": None}

    async def generate(*_args, **_kwargs):
        return Completion(json.dumps(output), 1, 1, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    with TestClient(app) as client:
        headers = csrf(client)
        url = f"/chats/{chat_id}/action-proposal"
        neutral = client.post(url, headers=headers,
                              json={"message_id": answer_id}).json()
        assert neutral["action"] is None and neutral["clarification"] is None
        output = {"action": None, "clarification": "  Welcher   Empfänger ist gemeint?  "}
        clarification = client.post(url, headers=headers,
                                    json={"message_id": answer_id}).json()
        assert clarification["action"] is None
        assert clarification["clarification"] == "Welcher Empfänger ist gemeint?"
        output = {"action": None, "clarification": "Was bedeutet Entwurf?"}
        echoed = client.post(url, headers=headers,
                             json={"message_id": answer_id}).json()
        assert echoed["action"] is None and echoed["clarification"] is None
        output = {"action": {"tool": "delete_all", "args": {}}}
        assert client.post(url, headers=headers, json={"message_id": answer_id}).status_code == 502
        output = {"action": {"tool": "create_draft", "args": {"recipient": "Ada"}}}
        assert client.post(url, headers=headers, json={"message_id": answer_id}).status_code == 502
        assert effects == []
        assert client.get("/runs").json() == []
    assert parse_clarification('{"action":null,"clarification":"Schreib etwas."}') is None
    assert parse_clarification('{"action":null,"clarification":17}') is None
    assert parse_clarification('{"action":null,"clarification":"Was ist eine Notiz?"}',
                               "Was ist eine Notiz?") is None


def test_stale_or_archived_chat_and_disabled_tool_are_rejected(tmp_path, monkeypatch):
    app, db, chat_id, answer_id, effects = prepared(tmp_path)
    with TestClient(app) as client:
        headers = csrf(client)
        url = f"/chats/{chat_id}/action-proposal"
        assert client.post(url, headers=headers,
                           json={"message_id": "stale"}).status_code == 409
        db.set_chat_archived(chat_id, True)
        assert client.post(url, headers=headers,
                           json={"message_id": answer_id}).status_code == 409
        db.set_chat_archived(chat_id, False)

        async def changed(*_args, **_kwargs):
            db.add_message(chat_id, "user", "Neue Bitte")
            return Completion('{"action":null}', 1, 1, 0, "local_no_api_charge")

        monkeypatch.setattr("jarvis.app.generate_text", changed)
        assert client.post(url, headers=headers,
                           json={"message_id": answer_id}).status_code == 409
        assert effects == []
        assert client.get("/runs").json() == []
    offered = available_actions(app.state.engine)
    app.state.engine.definitions["create_draft"] = ToolDefinition(
        "create_draft", DraftArgs, lambda _args: {}, scope="local_module", effect="write",
        enabled=lambda: False)
    try:
        parse_action('{"action":{"tool":"create_draft","args":'
                     '{"recipient":"Ada","body":"Hallo"}}}', app.state.engine, offered)
    except ValueError as exc:
        assert "aktiv" in str(exc)
    else:
        raise AssertionError("Deaktiviertes Modul wurde vorgeschlagen")


def test_waiting_write_survives_restart_without_running(tmp_path):
    app, db, _chat_id, _answer_id, effects = prepared(tmp_path)
    with TestClient(app) as client:
        headers = csrf(client)
        run = client.post("/runs", headers=headers, json={
            "tool_name": "create_draft", "args": {"recipient": "Ada", "body": "Hallo"},
            "client_request_id": "chat-action-restart-001"}).json()
        assert run["status"] == "waiting_approval"
        assert effects == []
    restarted = create_app(Settings(JARVIS_DATA_DIR=tmp_path / "data",
                                    JARVIS_OLLAMA_MODEL="fixture"))
    restarted.state.engine.register(ToolDefinition(
        "create_draft", DraftArgs,
        lambda args: effects.append(args.model_dump()) or {"draft": "created"},
        scope="local_module", effect="write",
    ))
    with TestClient(restarted) as client:
        headers = csrf(client)
        assert client.get(f"/runs/{run['id']}").json()["status"] == "waiting_approval"
        assert effects == []
        decided = client.post(f"/runs/{run['id']}/decision", headers=headers, json={
            "args_hash": run["args_hash"], "approved": False})
        assert decided.status_code == 200, decided.text
        assert decided.json()["status"] == "cancelled"
        assert effects == []
        assert db.get_run(run["id"])["status"] == "cancelled"


def test_proposal_displays_only_verified_read_sources(tmp_path, monkeypatch):
    app, db, _chat_id, _answer_id, _effects = prepared(tmp_path)
    app.state.engine.register(ToolDefinition(
        "lookup_context", NoArgs, lambda _args: {"fact": "Ada"},
        scope="local_module", effect="read",
    ))

    async def generate(*_args, **_kwargs):
        return Completion('{"action":{"tool":"create_draft","args":'
                          '{"recipient":"Ada","body":"Hallo"}}}',
                          1, 1, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    with TestClient(app) as client:
        headers = csrf(client)
        read = client.post("/runs", headers=headers,
                           json={"tool_name": "lookup_context", "args": {}}).json()
        assert read["status"] == "succeeded"
        chat = db.create_chat()
        db.add_message(chat["id"], "user", "Erstelle einen Entwurf an Ada: Hallo")
        answer = db.add_message(chat["id"], "assistant", "Ada ist im lokalen Kontext.",
                                tool_uses=[{"name": "lookup_context", "run_id": read["id"],
                                            "status": "succeeded"},
                                           {"name": "create_draft", "run_id": "fake",
                                            "status": "succeeded"}])
        result = client.post(f"/chats/{chat['id']}/action-proposal", headers=headers,
                             json={"message_id": answer["id"]})
        assert result.status_code == 200, result.text
        assert result.json()["action"]["source_uses"] == [
            {"name": "lookup_context", "run_id": read["id"]}]


def test_local_open_is_only_invoked_after_user_starts_run(tmp_path, monkeypatch):
    app, db, _chat_id, _answer_id, _effects = prepared(tmp_path)
    opened = []
    app.state.engine.register(ToolDefinition(
        "open_item", OpenArgs,
        lambda args: opened.append(args.item_id) or {"opened": args.item_id},
        scope="local_module", effect="local_open", description="Lokalen Eintrag öffnen",
    ))

    async def generate(*_args, **_kwargs):
        return Completion('{"action":{"tool":"open_item","args":'
                          '{"item_id":"alpha"}},"clarification":null}',
                          1, 1, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    chat = db.create_chat()
    db.add_message(chat["id"], "user", "Öffne Eintrag alpha lokal")
    answer = db.add_message(chat["id"], "assistant", "Ich kann alpha öffnen.")
    with TestClient(app) as client:
        headers = csrf(client)
        result = client.post(f"/chats/{chat['id']}/action-proposal", headers=headers,
                             json={"message_id": answer["id"]})
        assert result.status_code == 200, result.text
        action = result.json()["action"]
        assert action["tool_name"] == "open_item" and action["effect"] == "local_open"
        assert opened == [] and client.get("/runs").json() == []
        run = client.post("/runs", headers=headers, json={
            "tool_name": action["tool_name"], "args": action["args"],
            "client_request_id": "chat-open-test-001"}).json()
        assert run["status"] == "succeeded" and opened == ["alpha"]
        repeated = client.post("/runs", headers=headers, json={
            "tool_name": action["tool_name"], "args": action["args"],
            "client_request_id": "chat-open-test-001"}).json()
        assert repeated["id"] == run["id"] and opened == ["alpha"]
