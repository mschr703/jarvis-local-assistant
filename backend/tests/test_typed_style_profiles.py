"""Explicit text-type profiles fall back to the existing general profile."""

import sqlite3

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.drafts import DraftStore
from jarvis.provider import Completion


def csrf(client):
    return {"x-jarvis-csrf": client.get("/session").json()["csrf"]}


def test_typed_profiles_choose_prompt_and_keep_draft_snapshot(tmp_path, monkeypatch):
    prompts = []

    async def generate(_db, _config, purpose, messages):
        assert purpose == "draft"
        prompts.append(messages[-1]["content"])
        return Completion("Hallo, hier ist mein Entwurf.", 20, 10, 0,
                          "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        token = csrf(client)
        assert client.put("/style/profile", headers=token, json={
            "description": "Allgemein sachlich.", "policy": "local_only"}).status_code == 200
        fallback = client.get("/style/profiles/email").json()
        assert not fallback["override"] and fallback["description"] == ""
        assert fallback["effective_description"] == "Allgemein sachlich."
        response = client.put("/style/profiles/email", headers=token, json={
            "description": "E-Mails kurz und freundlich.", "policy": "local_only"})
        assert response.status_code == 200 and response.json()["override"]
        assert client.get("/style/profile").json()["description"] == "Allgemein sachlich."
        email = client.post("/drafts/generate", headers=token, json={
            "kind": "email", "brief": "Antworte freundlich."}).json()
        text = client.post("/drafts/generate", headers=token, json={
            "kind": "text", "brief": "Schreibe einen kurzen Absatz."}).json()
        assert "E-Mails kurz und freundlich." in prompts[0]
        assert "Allgemein sachlich." not in prompts[0]
        assert "Allgemein sachlich." in prompts[1]
        assert email["style_snapshot"] == "E-Mails kurz und freundlich."
        assert text["style_snapshot"] == "Allgemein sachlich."
        assert client.put("/style/profiles/answer", headers=token, json={
            "description": "Antworten nennen Unsicherheit.", "policy": "local_only"}).status_code == 200
        assert client.put("/style/profiles/essay", headers=token, json={
            "description": "Essays sind klar gegliedert.", "policy": "local_only"}).status_code == 200
        answer = client.post("/drafts/generate", headers=token, json={
            "kind": "answer", "brief": "Antworte auf die Frage.",
            "input_text": "Was ist bekannt?"})
        essay = client.post("/drafts/generate", headers=token, json={
            "kind": "essay", "brief": "Schreibe einen kurzen Essay.",
            "word_target": 300, "outline": ["Einstieg", "Schluss"]})
        assert answer.status_code == 200 and essay.status_code == 200
        assert answer.json()["style_snapshot"] == "Antworten nennen Unsicherheit."
        assert essay.json()["style_snapshot"] == "Essays sind klar gegliedert."
        assert "Antworten nennen Unsicherheit." in prompts[2]
        assert "Essays sind klar gegliedert." in prompts[3]
        edited = client.patch(f"/drafts/{email['id']}", headers=token, json={
            "content": "Bearbeitete E-Mail.", "expected_version": 1})
        assert edited.status_code == 200
        exported = client.post(f"/drafts/{email['id']}/export", headers=token, json={
            "format": "eml", "expected_version": 2})
        assert exported.status_code == 200
        assert client.get(f"/drafts/{email['id']}").json()["style_snapshot"] == email["style_snapshot"]
        cleared = client.put("/style/profiles/email", headers=token, json={
            "description": "", "policy": "local_only"})
        assert cleared.status_code == 200 and not cleared.json()["override"]
        assert cleared.json()["effective_description"] == "Allgemein sachlich."
        assert client.get(f"/drafts/{email['id']}").json()["style_snapshot"] == email["style_snapshot"]
        assert client.post("/drafts/generate", headers=token, json={
            "kind": "email", "brief": "Weitere E-Mail."}).json()["style_snapshot"] == "Allgemein sachlich."


def test_typed_profile_cloud_policy_and_invalid_kind(tmp_path, monkeypatch):
    calls = []

    async def generate(_db, _config, _purpose, messages):
        calls.append(messages[-1]["content"])
        return Completion("Ein Entwurf.", 10, 3, 0, "reported")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_PROVIDER="openrouter",
                        JARVIS_OPENROUTER_API_KEY="fixture", JARVIS_OPENROUTER_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        token = csrf(client)
        assert client.get("/style/profiles/unknown").status_code == 422
        assert client.put("/style/profiles/unknown", headers=token, json={
            "description": "X", "policy": "local_only"}).status_code == 422
        assert client.put("/style/profiles/email", headers=token, json={
            "description": "X", "policy": "wrong"}).status_code == 422
        assert client.put("/style/profile", headers=token, json={
            "description": "Allgemein freigegeben.", "policy": "cloud_allowed"}).status_code == 200
        assert client.put("/style/profiles/email", headers=token, json={
            "description": "E-Mail nur lokal.", "policy": "local_only"}).status_code == 200
        blocked = client.post("/drafts/generate", headers=token, json={
            "kind": "email", "brief": "Schreibe", "allow_cloud": True})
        assert blocked.status_code == 422 and not calls
        general = client.post("/drafts/generate", headers=token, json={
            "kind": "text", "brief": "Schreibe", "allow_cloud": True})
        assert general.status_code == 200
        assert general.json()["style_snapshot"] == "Allgemein freigegeben."
        assert client.put("/style/profiles/email", headers=token, json={
            "description": "E-Mail freigegeben.", "policy": "cloud_allowed"}).status_code == 200
        allowed = client.post("/drafts/generate", headers=token, json={
            "kind": "email", "brief": "Schreibe", "allow_cloud": True})
        assert allowed.status_code == 200
        assert allowed.json()["style_snapshot"] == "E-Mail freigegeben."
        assert len(calls) == 2


def test_schema34_migration_and_schema40_restore(tmp_path):
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    store = DraftStore(db, root)
    store.set_profile("Allgemeine Altvorgabe.", "local_only")
    with db.connect() as connection:
        connection.execute("DROP INDEX chats_archive_updated")
        connection.execute("ALTER TABLE chats DROP COLUMN archived_at")
        connection.execute("DROP TABLE style_profile_revisions")
        connection.execute("DROP TABLE typed_style_profiles")
        connection.execute("DROP TABLE chat_compactions")
        connection.execute("DROP TABLE draft_named_style_usage")
        connection.execute("DROP TABLE named_style_profile_revisions")
        connection.execute("DROP TABLE named_style_profiles")
        connection.execute("DROP TABLE project_memory_links")
        connection.execute("DROP TABLE project_person_merges")
        connection.execute("DROP TABLE project_person_aliases")
        connection.execute("DROP TABLE project_person_tasks")
        connection.execute("DROP TABLE project_people")
        connection.execute("ALTER TABLE messages DROP COLUMN context_latest_omitted_id")
        connection.execute("ALTER TABLE messages DROP COLUMN context_omitted_count")
        connection.execute("ALTER TABLE messages DROP COLUMN context_token_budget")
        connection.execute("ALTER TABLE messages DROP COLUMN context_estimated_tokens")
        connection.execute("ALTER TABLE messages DROP COLUMN compaction_version")
        connection.execute("DROP TABLE learning_sessions")
        connection.execute("DROP TABLE memory_proposal_events")
        connection.execute("DROP TABLE memory_proposals")
        connection.execute("DROP TABLE memory_context_uses")
        connection.execute("PRAGMA user_version=34")
    migrated = Database(root / "jarvis.db")
    profiles = DraftStore(migrated, root)
    assert sqlite3.connect(migrated.path).execute("PRAGMA user_version").fetchone()[0] == 57
    assert profiles.typed_profile("essay")["effective_description"] == "Allgemeine Altvorgabe."
    profiles.set_typed_profile("essay", "Essays mit klarer Gliederung.", "local_only")
    archive = tmp_path / "typed-profiles.zip"
    assert create_backup(root, archive)["schema"] == 57
    restored = tmp_path / "restored"
    assert restore_backup(archive, restored)["schema"] == 57
    recovered = DraftStore(Database(restored / "jarvis.db"), restored)
    assert recovered.profile()["description"] == "Allgemeine Altvorgabe."
    assert recovered.typed_profile("essay")["description"] == "Essays mit klarer Gliederung."
