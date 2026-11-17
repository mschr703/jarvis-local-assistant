"""A learning round is local, source-backed, revisable, and restorable."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.learning import LearningStore, parse_feedback, parse_question
from jarvis.provider import Completion


def csrf(client):
    return {"x-jarvis-csrf": client.get("/session").json()["csrf"]}


def test_model_output_requires_literal_evidence():
    source = {"text": "Die Photosynthese nutzt Sonnenlicht, Wasser und Kohlendioxid."}
    valid = {"source_index": 1, "question": "Was nutzt die Photosynthese?",
             "answer": "Sonnenlicht, Wasser und Kohlendioxid.",
             "evidence_quote": "Sonnenlicht, Wasser und Kohlendioxid"}
    assert parse_question(json.dumps(valid), [source])["question"] == valid["question"]
    with pytest.raises(ValueError, match="Quellenzitat"):
        parse_question(json.dumps({**valid, "evidence_quote": "Mondlicht und Eisen"}), [source])
    with pytest.raises(ValueError, match="Quellenzitat"):
        parse_question(json.dumps({**valid, "evidence_quote":
                                   "sonnenlicht, Wasser und Kohlendioxid"}), [source])
    with pytest.raises(ValueError, match="Fundstelle"):
        parse_question(json.dumps({**valid, "source_index": 2}), [source])
    feedback = {"evaluation": "partial", "feedback": "Sonnenlicht stimmt; Wasser fehlt.",
                "evidence_quote": "Sonnenlicht, Wasser", "next_question": "Welche Stoffe noch?"}
    assert parse_feedback(json.dumps(feedback), source["text"])["evaluation"] == "partial"
    for grade in ("correct", "incorrect"):
        assert parse_feedback(json.dumps({**feedback, "evaluation": grade}),
                              source["text"])["evaluation"] == grade
    with pytest.raises(ValueError, match="Quellenbeleg"):
        parse_feedback(json.dumps({**feedback, "evidence_quote": "Nur Mondlicht"}), source["text"])


def test_schema_48_migrates_to_learning_store(tmp_path):
    path = tmp_path / "jarvis.db"
    Database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE learning_sessions")
        connection.execute("DROP TABLE memory_proposal_events")
        connection.execute("DROP TABLE memory_proposals")
        connection.execute("DROP TABLE memory_context_uses")
        connection.execute("PRAGMA user_version=48")
    Database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 52
        assert connection.execute("SELECT COUNT(*) FROM learning_sessions").fetchone()[0] == 0


def test_learning_round_api_revision_and_backup(tmp_path, monkeypatch):
    outputs = [
        {"source_index": 1, "question": "Welche Stoffe nutzt die Photosynthese?",
         "answer": "Sonnenlicht, Wasser und Kohlendioxid.",
         "evidence_quote": "Sonnenlicht, Wasser und Kohlendioxid"},
        {"evaluation": "partial", "feedback": "Sonnenlicht stimmt; Wasser und Kohlendioxid fehlen.",
         "evidence_quote": "Sonnenlicht, Wasser und Kohlendioxid",
         "next_question": "Welcher weitere Stoff ist nötig?"},
    ]
    calls = []

    async def generate(_db, _config, purpose, messages):
        calls.append(messages)
        assert purpose == "learning"
        return Completion(json.dumps(outputs.pop(0), ensure_ascii=False), 30, 20, 0,
                          "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    data = tmp_path / "data"
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "biologie.txt").write_text(
        "Die Photosynthese nutzt Sonnenlicht, Wasser und Kohlendioxid.", encoding="utf-8")
    settings = Settings(JARVIS_DATA_DIR=data, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        token = csrf(client)
        folder = client.post("/folders", headers=token, json={
            "path": str(source_dir), "policy": "local_only"}).json()
        client.post(f"/folders/{folder['id']}/index", headers=token).raise_for_status()
        passage = client.get("/search?q=Photosynthese").json()[0]
        invalid = client.post("/learning/sessions", headers=token, json={
            "topic": "Biologie", "passage_ids": [passage["passage_id"]] * 2})
        assert invalid.status_code == 422 and not calls
        assert client.post("/learning/sessions", json={
            "topic": "Biologie", "passage_ids": [passage["passage_id"]]}).status_code == 403
        created = client.post("/learning/sessions", headers=token, json={
            "topic": "Biologie", "passage_ids": [passage["passage_id"]]})
        assert created.status_code == 200, created.text
        item = created.json()
        assert item["version"] == 1 and item["source"]["sha256"] == passage["sha256"]
        assert item["source"]["path"] == passage["path"]
        assert client.get("/learning/sessions").json()[0]["id"] == item["id"]
        assert client.get(f"/learning/sessions/{item['id']}").json()["evidence_quote"]
        bad_correction = client.patch(f"/learning/sessions/{item['id']}", headers=token,
                                      json={"expected_version": 1,
                                            "question": item["question"],
                                            "reference_answer": "Andere Antwort",
                                            "evidence_quote": "ausgedachter Beleg"})
        assert bad_correction.status_code == 422
        corrected = client.patch(f"/learning/sessions/{item['id']}", headers=token,
                                 json={"expected_version": 1,
                                       "question": item["question"],
                                       "reference_answer": "Sonnenlicht und Wasser.",
                                       "evidence_quote": "Sonnenlicht, Wasser"})
        assert corrected.status_code == 200 and corrected.json()["version"] == 2
        stale = client.post(f"/learning/sessions/{item['id']}/answer", headers=token,
                            json={"expected_version": 1, "answer": "Sonnenlicht."})
        assert stale.status_code == 409 and len(calls) == 1
        result = client.post(f"/learning/sessions/{item['id']}/answer", headers=token,
                             json={"expected_version": 2, "answer": "Sonnenlicht."})
        assert result.status_code == 200, result.text
        assert result.json()["evaluation"] == "partial"
        assert result.json()["user_answer"] == "Sonnenlicht."
        assert result.json()["next_question"]
        assert client.patch(f"/learning/sessions/{item['id']}", headers=token,
                            json={"expected_version": 3, "question": item["question"],
                                  "reference_answer": "X", "evidence_quote": "Sonnenlicht, Wasser"}
                            ).status_code == 409
        outputs.append({"evaluation": "correct", "feedback": "Ohne gültigen Beleg.",
                        "evidence_quote": "Erfundene Quelle", "next_question": ""})
        retried = client.post(f"/learning/sessions/{item['id']}/answer", headers=token,
                              json={"expected_version": 3,
                                    "answer": "Sonnenlicht.\nAuch Wasser."})
        assert retried.status_code == 200
        assert retried.json()["evaluation"] == "uncertain"
        assert retried.json()["feedback_quote"] == ""
        assert retried.json()["user_answer"] == "Sonnenlicht.\nAuch Wasser."
    archive = tmp_path / "learning.zip"
    assert create_backup(data, archive)["schema"] == 52
    restored = tmp_path / "restored"
    assert restore_backup(archive, restored)["schema"] == 52
    store = LearningStore(Database(restored / "jarvis.db"))
    assert store.get(item["id"])["user_answer"] == "Sonnenlicht.\nAuch Wasser."
    with TestClient(create_app(settings)) as client:
        token = csrf(client)
        assert client.delete(f"/learning/sessions/{item['id']}", headers=token).status_code == 200
        assert client.get(f"/learning/sessions/{item['id']}").status_code == 404


def test_learning_invalid_model_reply_and_cloud_block(tmp_path, monkeypatch):
    calls = []

    async def generate(*_args):
        calls.append(1)
        return Completion('{"source_index":1,"question":"Was ist erfunden?",'
                          '"answer":"Falsch","evidence_quote":"Nicht in der Quelle"}',
                          10, 10, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "fakten.txt").write_text("Die Erde kreist um die Sonne.", encoding="utf-8")
    settings = Settings(JARVIS_DATA_DIR=tmp_path / "local", JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        token = csrf(client)
        folder = client.post("/folders", headers=token, json={
            "path": str(source_dir), "policy": "local_only"}).json()
        client.post(f"/folders/{folder['id']}/index", headers=token).raise_for_status()
        passage_id = client.get("/search?q=Erde").json()[0]["passage_id"]
        response = client.post("/learning/sessions", headers=token, json={
            "topic": "Astronomie", "passage_ids": [passage_id]})
        assert response.status_code == 502 and client.get("/learning/sessions").json() == []
    cloud = Settings(JARVIS_DATA_DIR=tmp_path / "cloud", JARVIS_PROVIDER="openrouter",
                     JARVIS_OPENROUTER_API_KEY="fixture", JARVIS_OPENROUTER_MODEL="fixture")
    with TestClient(create_app(cloud)) as client:
        response = client.post("/learning/sessions", headers=csrf(client), json={
            "topic": "Astronomie", "passage_ids": [1]})
        assert response.status_code == 422 and len(calls) == 1
