"""Folgevorschläge bleiben lokal, begrenzt und ohne Seiteneffekt."""

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.chat_suggestions import parse_suggestions, prompt
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.provider import Completion


def csrf(client: TestClient) -> dict:
    return {"x-jarvis-csrf": client.get("/session").json()["csrf"]}


def conversation(db: Database) -> tuple[str, str]:
    chat = db.create_chat()
    db.add_message(chat["id"], "user", "Was muss ich für das Projekt prüfen?")
    answer = db.add_message(chat["id"], "assistant", "Prüfe die Quellen und halte die Zusage fest.")
    return chat["id"], answer["id"]


def test_local_suggestions_only_prepare_known_actions(tmp_path, monkeypatch):
    seen = []

    async def generate(_db, settings, purpose, messages):
        seen.append((purpose, messages, settings.provider, settings.max_output_tokens))
        return Completion('Vorwort\n[{"kind":"task","text":"  Zusage im Projekt  festhalten  "},'
                          '{"kind":"delete_file","text":"Alles entfernen"},'
                          '{"kind":"research","text":"Welche Quellen belegen die Zusage?"},'
                          '{"kind":"task","text":"Weitere Aufgabe"}]', 30, 20, 0,
                          "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    chat_id, message_id = conversation(db)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = csrf(client)
        url = f"/chats/{chat_id}/suggestions"
        assert client.post(url, json={"message_id": message_id}).status_code == 403
        assert client.post(url, headers=headers,
                           json={"message_id": message_id, "extra": 1}).status_code == 422
        result = client.post(url, headers=headers, json={"message_id": message_id})
        assert result.status_code == 200
        assert result.json() == {"message_id": message_id, "suggestions": [
            {"kind": "task", "text": "Zusage im Projekt festhalten"},
            {"kind": "research", "text": "Welche Quellen belegen die Zusage?"}]}
        assert seen[0][0] == "chat_suggestion"
        assert seen[0][2:] == ("ollama", 256)
        assert "Was muss ich" in seen[0][1][-1]["content"]
        assert "Prüfe die Quellen" in seen[0][1][-1]["content"]
        assert client.get("/tasks").json() == []
        assert client.get("/research").json() == []
        assert len(client.get(f"/chats/{chat_id}").json()["messages"]) == 2
        assert client.post(url, headers=headers,
                           json={"message_id": "outdated"}).status_code == 409
        db.add_message(chat_id, "user", "Neue Frage")
        assert client.post(url, headers=headers,
                           json={"message_id": message_id}).status_code == 422
        assert len(seen) == 1


def test_invalid_model_output_and_stale_during_generation(tmp_path, monkeypatch):
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    chat_id, message_id = conversation(db)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = csrf(client)
        url = f"/chats/{chat_id}/suggestions"

        async def invalid(*_args):
            return Completion("keine Liste", 2, 2, 0, "local_no_api_charge")

        monkeypatch.setattr("jarvis.app.generate_text", invalid)
        assert client.post(url, headers=headers,
                           json={"message_id": message_id}).status_code == 502

        async def changing(*_args):
            db.add_message(chat_id, "user", "Neuer Stand")
            return Completion('[{"kind":"task","text":"Alte Aufgabe"}]', 2, 2, 0,
                              "local_no_api_charge")

        monkeypatch.setattr("jarvis.app.generate_text", changing)
        assert client.post(url, headers=headers,
                           json={"message_id": message_id}).status_code == 409
        assert client.get("/tasks").json() == []


def test_no_answer_archived_and_cloud_provider_are_blocked(tmp_path, monkeypatch):
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    empty = db.create_chat()["id"]
    chat_id, message_id = conversation(db)

    async def unexpected(*_args):
        raise AssertionError("Kein Modellaufruf erlaubt")

    monkeypatch.setattr("jarvis.app.generate_text", unexpected)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = csrf(client)
        assert client.post(f"/chats/{empty}/suggestions", headers=headers,
                           json={"message_id": "missing"}).status_code == 422
        assert client.post("/chats/missing/suggestions", headers=headers,
                           json={"message_id": message_id}).status_code == 404
        db.set_chat_archived(chat_id, True)
        assert client.post(f"/chats/{chat_id}/suggestions", headers=headers,
                           json={"message_id": message_id}).status_code == 409
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root, JARVIS_PROVIDER="openrouter",
                                        JARVIS_OPENROUTER_API_KEY="fixture",
                                        JARVIS_OPENROUTER_MODEL="fixture"))) as client:
        assert client.post(f"/chats/{chat_id}/suggestions", headers=csrf(client),
                           json={"message_id": message_id}).status_code == 422


def test_parser_filters_unsupported_duplicate_and_long_values():
    assert parse_suggestions('[{"kind":"task","text":"Kurze Aufgabe"},'
                             '{"kind":"task","text":"kurze aufgabe"},'
                             '{"kind":"research","text":"Frage zu Quellen?"},'
                             '{"kind":"task","text":"Noch mehr"}]') == [
        {"kind": "task", "text": "Kurze Aufgabe"},
        {"kind": "research", "text": "Frage zu Quellen?"}]
    assert parse_suggestions('[{"kind":"task","text":"' + "X" * 161 + '"}]') == []
    assert len(prompt("Q" * 5000, "A" * 9000)[-1]["content"]) < 5000
