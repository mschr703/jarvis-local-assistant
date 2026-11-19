"""Nur ausdrücklich bestätigte Essay-Stilhinweise steuern spätere lokale Essays."""

import sqlite3

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.drafts import DraftStore
from jarvis.provider import Completion


def token(client):
    return {"x-jarvis-csrf": client.get("/session").json()["csrf"]}


def seed(root):
    store = DraftStore(Database(root / "jarvis.db"), root)
    essay = store.create_draft("essay", "", "Essay", "## Beginn\n\nText", [], "", "klar",
                               word_target=300, outline=["Beginn", "Schluss"])
    plain = store.create_draft("text", "", "Text", "Ein Text.", [], "", "klar")
    return store, essay, plain


def test_essay_feedback_requires_current_essay_and_can_be_removed(tmp_path):
    root = tmp_path / "data"
    _, essay, plain = seed(root)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root))) as client:
        csrf = token(client)
        path = f"/drafts/{essay['id']}/style-feedback"
        data = {"note": "  Bitte kurze   Sätze und klare Verben.  ", "expected_version": 1}
        saved = client.post(path, headers=csrf, json=data)
        assert saved.status_code == 200, saved.text
        item = saved.json()
        assert item["note"] == "Bitte kurze Sätze und klare Verben."
        assert item["draft_id"] == essay["id"] and item["draft_version"] == 1
        assert client.get("/style/essay-feedback").json() == [item]
        assert client.post(path, headers=csrf, json={
            "note": "bitte kurze sätze und klare verben.", "expected_version": 1}).status_code == 422
        assert client.post(path, headers=csrf, json={
            "note": "Zu kurz", "expected_version": 1}).status_code == 422
        assert client.post(f"/drafts/{plain['id']}/style-feedback", headers=csrf,
                           json=data).status_code == 422
        assert client.post("/drafts/missing/style-feedback", headers=csrf,
                           json=data).status_code == 404
        updated = client.patch(f"/drafts/{essay['id']}", headers=csrf, json={
            "content": essay["content"] + "\nMehr Text.", "expected_version": 1})
        assert updated.status_code == 200
        assert client.post(path, headers=csrf, json={
            "note": "Schreibe Absätze mit einem klaren Gedanken.",
            "expected_version": 1}).status_code == 409
        assert client.delete(f"/style/essay-feedback/{item['id']}", headers=csrf).status_code == 200
        assert client.get("/style/essay-feedback").json() == []
        assert client.delete(f"/style/essay-feedback/{item['id']}", headers=csrf).status_code == 404
        assert client.post(path, headers=csrf, json={
            "note": data["note"], "expected_version": 2}).status_code == 200


def test_latest_five_feedback_used_only_for_future_essays_and_kept_as_snapshot(
        tmp_path, monkeypatch):
    root = tmp_path / "data"
    store, essay, _ = seed(root)
    for index in range(6):
        store.add_essay_feedback(essay["id"], 1,
                                 f"Stilregel {index}: schreibe sichtbar präzise Sätze.")
    prompts = []

    async def generate(_db, _settings, _purpose, messages):
        prompts.append(messages[-1]["content"])
        return Completion("Ein kurzer Absatz.", 20, 10, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    settings = Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        csrf = token(client)
        profile = client.put("/style/profile", headers=csrf, json={
            "description": "Sachlich.", "policy": "local_only"})
        assert profile.status_code == 200
        created = client.post("/drafts/generate", headers=csrf, json={
            "kind": "essay", "brief": "Beschreibe ein Buch.", "word_target": 300,
            "outline": ["Anfang", "Schluss"]})
        assert created.status_code == 200, created.text
        draft = created.json()
        assert len(draft["style_feedback"]) == 5
        assert "Stilregel 0" not in prompts[0]
        assert all(f"Stilregel {index}" in prompts[0] for index in range(1, 6))
        assert all(f"Stilregel {index}" in draft["style_feedback"][0]["note"]
                   for index in [5])
        plain = client.post("/drafts/generate", headers=csrf,
                            json={"kind": "text", "brief": "Ein kurzer Text."})
        assert plain.status_code == 200 and "Bestätigte Stilhinweise" not in prompts[-1]
        for item in client.get("/style/essay-feedback").json():
            assert client.delete(f"/style/essay-feedback/{item['id']}", headers=csrf).status_code == 200
        assert client.get(f"/drafts/{draft['id']}").json()["style_feedback"] == draft["style_feedback"]
        prompts.clear()
        later = client.post("/drafts/generate", headers=csrf, json={
            "kind": "essay", "brief": "Beschreibe ein Buch.", "word_target": 300,
            "outline": ["Anfang", "Schluss"]})
        assert later.status_code == 200
        assert later.json()["style_feedback"] == []
        assert "Stilregel" not in prompts[0]


def test_feedback_survives_restart_backup_and_schema_32_migration(tmp_path):
    root = tmp_path / "data"
    store, essay, _ = seed(root)
    item = store.add_essay_feedback(essay["id"], 1, "Vermeide Floskeln in der Einleitung.")
    later = store.create_draft("essay", "", "Später", "## Start\n\nEin neuer Text.", [], "",
                               "klar", word_target=300, outline=["Start", "Ende"],
                               feedback_snapshot=[{"id": item["id"], "note": item["note"]}])
    assert DraftStore(Database(root / "jarvis.db"), root).essay_feedback()[0]["id"] == item["id"]
    archive = tmp_path / "feedback.zip"
    assert create_backup(root, archive)["schema"] == 56
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    recovered = DraftStore(Database(restored / "jarvis.db"), restored)
    assert recovered.essay_feedback()[0]["note"] == item["note"]
    assert recovered.get_draft(later["id"])["style_feedback"] == [
        {"id": item["id"], "note": item["note"]}]

    legacy = tmp_path / "legacy.db"
    Database(legacy)
    with sqlite3.connect(legacy) as db:
        db.execute("DROP TABLE essay_style_feedback")
        db.execute("DROP TABLE essay_feedback_usage")
        db.execute("DROP INDEX chats_archive_updated")
        db.execute("ALTER TABLE chats DROP COLUMN archived_at")
        db.execute("DROP TABLE style_profile_revisions")
        db.execute("DROP TABLE chat_compactions")
        db.execute("DROP TABLE draft_named_style_usage")
        db.execute("DROP TABLE named_style_profile_revisions")
        db.execute("DROP TABLE named_style_profiles")
        db.execute("DROP TABLE project_memory_links")
        db.execute("DROP TABLE project_person_merges")
        db.execute("DROP TABLE project_person_aliases")
        db.execute("DROP TABLE project_person_tasks")
        db.execute("DROP TABLE project_people")
        db.execute("ALTER TABLE messages DROP COLUMN context_latest_omitted_id")
        db.execute("ALTER TABLE messages DROP COLUMN context_omitted_count")
        db.execute("ALTER TABLE messages DROP COLUMN context_token_budget")
        db.execute("ALTER TABLE messages DROP COLUMN context_estimated_tokens")
        db.execute("ALTER TABLE messages DROP COLUMN compaction_version")
        db.execute("DROP TABLE learning_sessions")
        db.execute("DROP TABLE memory_proposal_events")
        db.execute("DROP TABLE memory_proposals")
        db.execute("DROP TABLE memory_context_uses")
        db.execute("PRAGMA user_version=32")
    migrated = Database(legacy)
    assert DraftStore(migrated, tmp_path).essay_feedback() == []
    with sqlite3.connect(legacy) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 56
