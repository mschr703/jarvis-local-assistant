"""Chat memory proposals require literal user evidence and explicit decisions."""

import hashlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.memory_review import (
    MemoryProposalConflict,
    MemoryReviewStore,
    eligible_messages,
    parse_conflicts,
    parse_proposals,
    proposal_prompt,
)
from jarvis.provider import Completion
from jarvis.semantic_cache import PersistentEmbeddingCache


def csrf(client):
    return {"x-jarvis-csrf": client.get("/session").json()["csrf"]}


def test_proposal_parser_uses_only_literal_user_statements():
    messages = [
        {"id": "u1", "role": "user", "status": "complete", "created_at": "now",
         "content": "Mein Lieblingsfach ist Biologie."},
        {"id": "a1", "role": "assistant", "status": "complete", "created_at": "now",
         "content": "Du wohnst in Berlin."},
        {"id": "u2", "role": "user", "status": "complete", "created_at": "now",
         "content": "Ich lerne für die Prüfung am Freitag."},
    ]
    eligible = eligible_messages(messages)
    assert [item["id"] for item in eligible] == ["u1", "u2"]
    assert "Biologie" in proposal_prompt(eligible)[1]["content"]
    raw = json.dumps([
        {"source_index": 1, "source_quote": "Mein Lieblingsfach ist Biologie",
         "memory": "Das Lieblingsfach ist Biologie."},
        {"source_index": 2, "source_quote": "Ich lerne für die Prüfung",
         "memory": "Die Person lernt für eine Prüfung."},
        {"source_index": 1, "source_quote": "Du wohnst in Berlin",
         "memory": "Die Person wohnt in Berlin."},
        {"source_index": 1, "source_quote": "Mein Lieblingsfach ist Biologie",
         "memory": "Das Lieblingsfach ist Biologie."},
    ], ensure_ascii=False)
    parsed = parse_proposals(raw, eligible)
    assert len(parsed) == 2
    assert [item["message_id"] for item in parsed] == ["u1", "u2"]
    truncated = '[{"source_index":1,"source_quote":"Mein Lieblingsfach ist Biologie",' \
                '"memory":"Das Lieblingsfach ist Biologie."}, {"source_index":2,'
    assert parse_proposals(truncated, eligible)[0]["message_id"] == "u1"


def test_memory_review_decisions_are_explicit_versioned_and_restorable(tmp_path, monkeypatch):
    output = [
        {"source_index": 1, "source_quote": "Mein Lieblingsfach ist Biologie",
         "memory": "Das Lieblingsfach ist Biologie."},
        {"source_index": 2, "source_quote": "Ich wohne in Bremen",
         "memory": "Die Person wohnt in Bremen."},
        {"source_index": 3, "source_quote": "Ich mag kurze Antworten",
         "memory": "Die Person bevorzugt kurze Antworten."},
    ]
    calls = []

    async def generate(_db, _config, purpose, messages):
        calls.append(messages)
        assert purpose == "memory_proposal"
        return Completion(json.dumps(output, ensure_ascii=False), 60, 80, 0,
                          "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    data = tmp_path / "data"
    settings = Settings(JARVIS_DATA_DIR=data, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        token = csrf(client)
        db = Database(data / "jarvis.db")
        chat = db.create_chat()
        for statement in ("Mein Lieblingsfach ist Biologie.",
                          "Ich wohne in Bremen.", "Ich mag kurze Antworten."):
            db.add_message(chat["id"], "user", statement)
        latest = db.add_message(chat["id"], "assistant", "Verstanden.")
        assert client.post(f"/chats/{chat['id']}/memory-proposals", json={
            "last_message_id": latest["id"]}).status_code == 403
        created = client.post(f"/chats/{chat['id']}/memory-proposals", headers=token,
                              json={"last_message_id": latest["id"]})
        assert created.status_code == 200, created.text
        items = created.json()
        assert len(items) == 3 and len(calls) == 1
        by_quote = {item["source_quote"]: item for item in items}
        biology = by_quote["Mein Lieblingsfach ist Biologie"]
        bremen = by_quote["Ich wohne in Bremen"]
        assert client.post(f"/memory-proposals/{biology['id']}/decision", json={
            "expected_version": 1, "action": "reject"}).status_code == 403
        assert client.get("/memories").json() == []
        assert client.post(f"/chats/{chat['id']}/memory-proposals", headers=token,
                           json={"last_message_id": latest["id"]}).status_code == 200
        assert len(calls) == 1
        rejected = client.post(f"/memory-proposals/{biology['id']}/decision",
                               headers=token, json={"expected_version": 1,
                                                    "action": "reject"})
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "rejected"
        assert client.get("/memories").json() == []
        accepted = client.post(f"/memory-proposals/{bremen['id']}/decision",
                               headers=token, json={"expected_version": 1,
                                                    "action": "accept",
                                                    "text": "Ich wohne in Bremen."})
        assert accepted.status_code == 200, accepted.text
        accepted_item = accepted.json()
        assert accepted_item["status"] == "accepted"
        assert client.get("/memories").json()[0]["text"] == "Ich wohne in Bremen."
        embedding_cache = PersistentEmbeddingCache(data / "semantic-embeddings.sqlite")
        text_hash = hashlib.sha256(b"Ich wohne in Bremen.").hexdigest()
        embedding_cache.write("a" * 64, {text_hash: [1.0, 0.0]})
        assert embedding_cache.read("a" * 64, [text_hash])
        assert client.post(f"/memory-proposals/{bremen['id']}/decision",
                           headers=token, json={"expected_version": 1,
                                                "action": "correct",
                                                "text": "Ich wohne in Hamburg."}
                           ).status_code == 409
        corrected = client.post(f"/memory-proposals/{bremen['id']}/decision",
                                headers=token, json={"expected_version": 2,
                                                     "action": "correct",
                                                     "text": "Ich wohne jetzt in Hamburg."})
        assert corrected.status_code == 200
        assert corrected.json()["version"] == 3
        assert embedding_cache.read("a" * 64, [text_hash]) == {}
        assert client.get("/memories").json()[0]["text"] == "Ich wohne jetzt in Hamburg."
        memory = client.get("/memories").json()[0]
        external = {"text": "Anderer Ort", "source_ref": memory["source_ref"],
                    "policy": "local_only"}
        assert client.patch(f"/memories/{memory['id']}", headers=token,
                            json=external).status_code == 200
        assert client.post(f"/memory-proposals/{bremen['id']}/decision",
                           headers=token, json={"expected_version": 3,
                                                "action": "correct",
                                                "text": "Ich wohne in Köln."}).status_code == 409
        assert client.patch(f"/memories/{memory['id']}", headers=token,
                            json={**external, "text": "Ich wohne jetzt in Hamburg."}
                            ).status_code == 200
        embedding_cache.write("a" * 64, {text_hash: [1.0, 0.0]})
        removed = client.post(f"/memory-proposals/{bremen['id']}/decision",
                              headers=token, json={"expected_version": 3,
                                                   "action": "delete"})
        assert removed.status_code == 200
        assert embedding_cache.read("a" * 64, [text_hash]) == {}
        assert removed.json()["status"] == "deleted"
        assert client.get("/memories").json() == []
        assert [event["action"] for event in removed.json()["events"]] == [
            "accepted", "corrected", "deleted"]
        assert removed.json()["source_quote"] == "Ich wohne in Bremen"
        assert client.get(f"/memory-proposals?chat_id={chat['id']}").status_code == 200
        assert client.delete(f"/chats/{chat['id']}", headers=token).status_code == 200
        assert MemoryReviewStore(db).get(bremen["id"])["source_available"] is False
    archive = tmp_path / "review.zip"
    assert create_backup(data, archive)["schema"] == 57
    restored = tmp_path / "restored"
    assert restore_backup(archive, restored)["schema"] == 57
    review = MemoryReviewStore(Database(restored / "jarvis.db"))
    assert review.get(biology["id"])["status"] == "rejected"
    assert review.get(bremen["id"])["events"][-1]["action"] == "deleted"
    assert review.get(bremen["id"])["source_quote"] == "Ich wohne in Bremen"


def test_memory_review_stale_chat_and_cloud_block(tmp_path, monkeypatch):
    calls = []

    async def generate(*_args):
        calls.append(1)
        return Completion("[]", 10, 10, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    data = tmp_path / "data"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=data,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        token = csrf(client)
        db = Database(data / "jarvis.db")
        chat = db.create_chat()
        user = db.add_message(chat["id"], "user", "Ich mag lokale Modelle.")
        db.add_message(chat["id"], "assistant", "Verstanden.")
        stale = client.post(f"/chats/{chat['id']}/memory-proposals", headers=token,
                            json={"last_message_id": user["id"]})
        assert stale.status_code == 409 and not calls
    cloud = Settings(JARVIS_DATA_DIR=tmp_path / "cloud", JARVIS_PROVIDER="openrouter",
                     JARVIS_OPENROUTER_API_KEY="fixture",
                     JARVIS_OPENROUTER_MODEL="fixture")
    with TestClient(create_app(cloud)) as client:
        response = client.post("/chats/example/memory-proposals", headers=csrf(client),
                               json={"last_message_id": "example"})
        assert response.status_code == 422 and not calls


def test_schema_49_migrates_to_memory_review(tmp_path):
    path = tmp_path / "jarvis.db"
    Database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE memory_proposal_events")
        connection.execute("DROP TABLE memory_proposals")
        connection.execute("DROP TABLE memory_context_uses")
        connection.execute("PRAGMA user_version=49")
    Database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 57
        assert connection.execute("SELECT COUNT(*) FROM memory_proposals").fetchone()[0] == 0


def test_memory_validity_is_reviewed_enforced_and_restored(tmp_path):
    data = tmp_path / "data"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=data,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        token = csrf(client)
        db = Database(data / "jarvis.db")
        chat = db.create_chat()
        user = db.add_message(chat["id"], "user", "Ich wohne in Bremen.")
        latest = db.add_message(chat["id"], "assistant", "Verstanden.")
        store = MemoryReviewStore(db)
        item = store.create_batch(chat, latest["id"], [{
            "message_id": user["id"], "message_created_at": user["created_at"],
            "source_quote": "Ich wohne in Bremen", "candidate_text": "Ich wohne in Bremen.",
        }])[0]
        url = f"/memory-proposals/{item['id']}/decision"
        bad = client.post(url, headers=token, json={"expected_version": 1,
            "action": "accept", "text": "Ich wohne in Bremen.",
            "valid_from": "2999-01-01", "valid_until": "2000-01-01"})
        assert bad.status_code == 422
        accepted = client.post(url, headers=token, json={"expected_version": 1,
            "action": "accept", "text": "Ich wohne in Bremen.",
            "valid_until": "2000-01-01"})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["validity_state"] == "expired"
        with pytest.raises(MemoryProposalConflict, match="zeitlich"):
            store.selected_context([item["id"]])
        future = client.post(url, headers=token, json={"expected_version": 2,
            "action": "correct", "text": "Ich wohne in Bremen.",
            "valid_from": "2999-01-01", "valid_until": ""})
        assert future.status_code == 200, future.text
        assert future.json()["validity_state"] == "future"
        with pytest.raises(MemoryProposalConflict, match="zeitlich"):
            store.selected_context([item["id"]])
        current = client.post(url, headers=token, json={"expected_version": 3,
            "action": "correct", "text": "Ich wohne in Bremen.",
            "valid_from": "", "valid_until": "2999-01-01"})
        assert current.status_code == 200, current.text
        assert current.json()["validity_state"] == "current"
        assert store.selected_context([item["id"]])[0]["text"] == "Ich wohne in Bremen."
        assert [event["valid_until"] for event in current.json()["events"]] == [
            "2000-01-01", None, "2999-01-01"]
    archive = tmp_path / "validity.zip"
    assert create_backup(data, archive)["schema"] == 57
    restored = tmp_path / "restored"
    assert restore_backup(archive, restored)["schema"] == 57
    assert MemoryReviewStore(Database(restored / "jarvis.db")).get(item["id"])[
        "valid_until"] == "2999-01-01"


def test_possible_conflicts_need_two_literal_current_local_memories(tmp_path, monkeypatch):
    calls = []
    async def generate(_db, _config, purpose, messages):
        calls.append((purpose, messages))
        numbered = json.loads(messages[1]["content"])
        output = [{"first_index": numbered[0]["index"],
                   "second_index": numbered[1]["index"],
                   "first_quote": numbered[0]["text"].rstrip("."),
                   "second_quote": numbered[1]["text"].rstrip("."),
                   "reason": "Beide Orte könnten für denselben Zeitpunkt verschieden sein."}]
        return Completion(json.dumps(output, ensure_ascii=False), 50, 60, 0,
                          "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    data = tmp_path / "data"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=data,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        token = csrf(client)
        db = Database(data / "jarvis.db")
        chat = db.create_chat()
        one = db.add_message(chat["id"], "user", "Ich wohne in Bremen.")
        two = db.add_message(chat["id"], "user", "Ich wohne in Hamburg.")
        latest = db.add_message(chat["id"], "assistant", "Verstanden.")
        store = MemoryReviewStore(db)
        items = store.create_batch(chat, latest["id"], [
            {"message_id": message["id"], "message_created_at": message["created_at"],
             "source_quote": quote, "candidate_text": text}
            for message, quote, text in (
                (one, "Ich wohne in Bremen", "Ich wohne in Bremen."),
                (two, "Ich wohne in Hamburg", "Ich wohne in Hamburg."))])
        assert client.post("/memory-proposals/conflicts").status_code == 403
        assert client.post("/memory-proposals/conflicts", headers=token).json() == []
        assert calls == []
        for item in items:
            response = client.post(f"/memory-proposals/{item['id']}/decision",
                                   headers=token, json={"action": "accept",
                                                        "expected_version": 1,
                                                        "text": item["candidate_text"]})
            assert response.status_code == 200, response.text
        found = client.post("/memory-proposals/conflicts", headers=token)
        assert found.status_code == 200, found.text
        assert len(found.json()) == 1
        assert {found.json()[0]["first_id"], found.json()[0]["second_id"]} == {
            item["id"] for item in items}
        assert calls[0][0] == "memory_conflicts"
        assert len(client.get("/memories").json()) == 2
        invalid = [{"first_index": 1, "second_index": 2,
                    "first_quote": "Ich wohne in Paris",
                    "second_quote": "Ich wohne in Hamburg",
                    "reason": "Möglicher Widerspruch zwischen Wohnorten."}]
        assert parse_conflicts(json.dumps(invalid), store.conflict_candidates()) == []
        first = store.get(items[0]["id"])
        assert client.post(f"/memory-proposals/{first['id']}/decision", headers=token,
                           json={"action": "correct", "expected_version": 2,
                                 "text": first["decided_text"],
                                 "valid_until": "2000-01-01"}).status_code == 200
        assert client.post("/memory-proposals/conflicts", headers=token).json() == []
        assert len(calls) == 1
