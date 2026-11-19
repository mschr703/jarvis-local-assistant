"""Only explicitly selected, current memories enter a local chat answer."""

import sqlite3
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.memory_review import MemoryReviewStore
from jarvis.provider import Chunk, ProviderAdapter


def csrf(client):
    return {"x-jarvis-csrf": client.get("/session").json()["csrf"]}


def test_selected_memory_snapshot_is_used_once_and_restored(tmp_path, monkeypatch):
    data = tmp_path / "data"
    db = Database(data / "jarvis.db")
    review = MemoryReviewStore(db)
    chat = db.create_chat()
    user = db.add_message(chat["id"], "user", "Ich bevorzuge kurze Antworten.")
    latest = db.add_message(chat["id"], "assistant", "Verstanden.")
    created = review.create_batch(db.get_chat(chat["id"]), latest["id"], [
        {"message_id": user["id"], "message_created_at": user["created_at"],
         "source_quote": "Ich bevorzuge kurze Antworten",
         "candidate_text": "Ich bevorzuge kurze Antworten."},
        {"message_id": user["id"], "message_created_at": user["created_at"],
         "source_quote": "kurze Antworten",
         "candidate_text": "Ich mag lange Antworten."},
    ])
    by_text = {item["candidate_text"]: item for item in created}
    accepted = review.decide(by_text["Ich bevorzuge kurze Antworten."]["id"], 1, "accept",
                             "Bitte antworte mir knapp.")
    rejected = review.decide(by_text["Ich mag lange Antworten."]["id"], 1, "reject")
    assert review.selected_context([accepted["id"]])[0]["text"] == "Bitte antworte mir knapp."
    seen = []

    class CaptureProvider(ProviderAdapter):
        async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[Chunk]:
            seen.append(messages)
            yield Chunk("text", text="Kurze Antwort.")
            yield Chunk("usage", input_tokens=12, output_tokens=3,
                        cost_usd=0, cost_kind="local_no_api_charge")

    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: CaptureProvider())
    settings = Settings(JARVIS_DATA_DIR=data, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        token = csrf(client)
        route = f"/chats/{chat['id']}/stream"
        rejected_attempt = client.post(route, headers=token, json={
            "content": "Was weißt du?", "memory_proposal_ids": [rejected["id"]]})
        assert rejected_attempt.status_code == 409 and not seen
        assert len(db.list_messages(chat["id"])) == 2
        selected = client.post(route, headers=token, json={
            "content": "Antworte bitte.", "memory_proposal_ids": [accepted["id"]]})
        assert selected.status_code == 200 and "event: done" in selected.text
        assert "Bitte antworte mir knapp." in seen[-1][0]["content"]
        assert "Ich mag lange Antworten." not in seen[-1][0]["content"]
        answer = client.get(f"/chats/{chat['id']}").json()["messages"][-1]
        assert answer["memory_context"][0]["text_snapshot"] == "Bitte antworte mir knapp."
        assert review.get(accepted["id"])["last_used"]["assistant_message_id"] == answer["id"]
        corrected = review.decide(accepted["id"], 2, "correct",
                                  "Bitte antworte mir ausführlich.")
        assert corrected["last_used"]["text_snapshot"] == "Bitte antworte mir knapp."
        assert client.post(route, headers=token, json={
            "content": "Doppelt?", "memory_proposal_ids": [accepted["id"], accepted["id"]]}
            ).status_code == 422
        plain = client.post(route, headers=token, json={"content": "Ohne Erinnerung."})
        assert plain.status_code == 200
        assert "Bitte antworte mir ausführlich." not in seen[-1][0]["content"]
        assert client.get(f"/chats/{chat['id']}").json()["messages"][-1]["memory_context"] == []
    archive = tmp_path / "memory-context.zip"
    assert create_backup(data, archive)["schema"] == 58
    restored = tmp_path / "restored"
    assert restore_backup(archive, restored)["schema"] == 58
    restored_db = Database(restored / "jarvis.db")
    restored_answer = next(item for item in restored_db.list_messages(chat["id"])
                           if item["id"] == answer["id"])
    assert restored_answer["memory_context"][0]["text_snapshot"] == "Bitte antworte mir knapp."
    assert MemoryReviewStore(restored_db).get(accepted["id"])["last_used"]["text_snapshot"] == (
        "Bitte antworte mir knapp.")


def test_memory_context_cloud_rejected_and_schema_50_migrates(tmp_path):
    path = tmp_path / "jarvis.db"
    Database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE memory_context_uses")
        for table in ("memory_proposals", "memory_proposal_events"):
            connection.execute(f"ALTER TABLE {table} DROP COLUMN valid_from")
            connection.execute(f"ALTER TABLE {table} DROP COLUMN valid_until")
        connection.execute("PRAGMA user_version=50")
    Database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 58
        assert connection.execute("SELECT COUNT(*) FROM memory_context_uses").fetchone()[0] == 0
    cloud = Settings(JARVIS_DATA_DIR=tmp_path / "cloud", JARVIS_PROVIDER="openrouter",
                     JARVIS_OPENROUTER_API_KEY="fixture",
                     JARVIS_OPENROUTER_MODEL="fixture")
    with TestClient(create_app(cloud)) as client:
        token = csrf(client)
        chat = client.post("/chats", headers=token).json()
        response = client.post(f"/chats/{chat['id']}/stream", headers=token,
                               json={"content": "Hallo", "allow_cloud": True,
                                     "memory_proposal_ids": ["unbekannt"]})
        assert response.status_code == 422
        assert client.get(f"/chats/{chat['id']}").json()["messages"] == []
