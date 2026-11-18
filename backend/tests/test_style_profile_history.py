"""Bewusste Profilfassungen bleiben nachvollziehbar und revidierbar."""

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.drafts import DraftStore
from jarvis.provider import Completion


def csrf(client):
    return {"x-jarvis-csrf": client.get("/session").json()["csrf"]}


def test_general_history_conflict_restore_and_unchanged_draft(tmp_path, monkeypatch):
    prompts = []

    async def generate(_db, _config, _purpose, messages):
        prompts.append(messages[-1]["content"])
        return Completion("Ein bearbeitbarer Text.", 20, 8, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    root = tmp_path / "data"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        token = csrf(client)
        assert client.get("/style/profile").json()["revision"] == 0
        assert client.get("/style/profile/history").json()["versions"] == []
        first = client.put("/style/profile", headers=token, json={
            "description": "Klar und knapp.", "policy": "local_only",
            "expected_revision": 0})
        assert first.status_code == 200 and first.json()["revision"] == 1
        second = client.put("/style/profile", headers=token, json={
            "description": "Freundlich und konkret.", "policy": "cloud_allowed",
            "expected_revision": 1})
        assert second.status_code == 200 and second.json()["revision"] == 2
        old = client.post("/drafts/generate", headers=token, json={
            "kind": "text", "brief": "Schreibe einen Text."})
        assert old.status_code == 200 and old.json()["style_snapshot"] == "Freundlich und konkret."
        assert "Freundlich und konkret." in prompts[-1]

        stale = client.put("/style/profile", headers=token, json={
            "description": "Verlorene Änderung.", "policy": "local_only",
            "expected_revision": 1})
        assert stale.status_code == 409
        assert client.get("/style/profile").json()["description"] == "Freundlich und konkret."
        history = client.get("/style/profile/history").json()
        assert history["revision"] == 2
        assert [(item["revision"], item["policy"]) for item in history["versions"]] == [
            (2, "cloud_allowed"), (1, "local_only")]
        assert client.post("/style/profile/restore", headers=token, json={
            "revision": 1, "expected_revision": 1}).status_code == 409
        assert client.post("/style/profile/restore", headers=token, json={
            "revision": 99, "expected_revision": 2}).status_code == 404
        restored = client.post("/style/profile/restore", headers=token, json={
            "revision": 1, "expected_revision": 2})
        assert restored.status_code == 200 and restored.json()["revision"] == 3
        assert restored.json()["description"] == "Klar und knapp."
        assert restored.json()["policy"] == "local_only"
        assert client.get("/style/profile/history").json()["versions"][0]["restored_from"] == 1
        new = client.post("/drafts/generate", headers=token, json={
            "kind": "text", "brief": "Schreibe noch einen Text."})
        assert new.status_code == 200 and new.json()["style_snapshot"] == "Klar und knapp."
        assert client.get(f"/drafts/{old.json()['id']}").json()["style_snapshot"] == (
            "Freundlich und konkret.")
        unchanged = client.put("/style/profile", headers=token, json={
            "description": "Klar und knapp.", "policy": "local_only", "expected_revision": 3})
        assert unchanged.status_code == 200 and unchanged.json()["revision"] == 3
        same_content_restore = client.post("/style/profile/restore", headers=token, json={
            "revision": 1, "expected_revision": 3})
        assert same_content_restore.status_code == 200
        assert same_content_restore.json()["revision"] == 4
        assert client.get("/style/profile/history").json()["versions"][0]["restored_from"] == 1

    archive = tmp_path / "profile-history.zip"
    assert create_backup(root, archive)["schema"] == 53
    restored_root = tmp_path / "restored"
    assert restore_backup(archive, restored_root)["schema"] == 53
    recovered = DraftStore(Database(restored_root / "jarvis.db"), restored_root)
    assert recovered.profile()["revision"] == 4
    assert recovered.profile_history("general")["versions"][0]["restored_from"] == 1
    assert recovered.get_draft(old.json()["id"])["style_snapshot"] == "Freundlich und konkret."


def test_typed_history_fallback_restore_and_policy(tmp_path):
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        token = csrf(client)
        assert client.put("/style/profile", headers=token, json={
            "description": "Allgemeiner Stil.", "policy": "cloud_allowed",
            "expected_revision": 0}).status_code == 200
        initial = client.get("/style/profiles/email").json()
        assert initial["revision"] == 0 and not initial["override"]
        first = client.put("/style/profiles/email", headers=token, json={
            "description": "E-Mails knapp.", "policy": "local_only",
            "expected_revision": 0})
        assert first.status_code == 200 and first.json()["revision"] == 1
        second = client.put("/style/profiles/email", headers=token, json={
            "description": "E-Mails freundlich.", "policy": "cloud_allowed",
            "expected_revision": 1})
        assert second.status_code == 200 and second.json()["revision"] == 2
        cleared = client.put("/style/profiles/email", headers=token, json={
            "description": "", "policy": "cloud_allowed", "expected_revision": 2})
        assert cleared.status_code == 200 and cleared.json()["revision"] == 3
        assert not cleared.json()["override"]
        assert cleared.json()["effective_description"] == "Allgemeiner Stil."
        assert client.put("/style/profiles/email", headers=token, json={
            "description": "Veraltet.", "policy": "local_only",
            "expected_revision": 2}).status_code == 409
        assert client.get("/style/profiles/essay/history").json()["versions"] == []
        assert client.get("/style/profiles/unknown/history").status_code == 422
        assert client.get("/style/profiles/general/history").status_code == 422
        assert client.post("/style/profiles/unknown/restore", headers=token, json={
            "revision": 1, "expected_revision": 0}).status_code == 422
        assert client.post("/style/profiles/general/restore", headers=token, json={
            "revision": 1, "expected_revision": 1}).status_code == 422
        history = client.get("/style/profiles/email/history").json()
        assert [(item["revision"], item["description"]) for item in history["versions"]] == [
            (3, ""), (2, "E-Mails freundlich."), (1, "E-Mails knapp.")]
        assert history["versions"][0]["policy"] == "local_only"
        restored = client.post("/style/profiles/email/restore", headers=token, json={
            "revision": 1, "expected_revision": 3})
        assert restored.status_code == 200 and restored.json()["revision"] == 4
        assert restored.json()["effective_description"] == "E-Mails knapp."
        assert restored.json()["effective_policy"] == "local_only"
        fallback = client.post("/style/profiles/email/restore", headers=token, json={
            "revision": 3, "expected_revision": 4})
        assert fallback.status_code == 200 and fallback.json()["revision"] == 5
        assert not fallback.json()["override"]
        assert fallback.json()["effective_policy"] == "cloud_allowed"
        assert client.get("/style/profiles/email/history").json()["versions"][0][
            "restored_from"] == 3
        essay_first = client.put("/style/profiles/essay", headers=token, json={
            "description": "Essays mit klarer These.", "policy": "local_only",
            "expected_revision": 0})
        assert essay_first.status_code == 200 and essay_first.json()["revision"] == 1
        essay_second = client.put("/style/profiles/essay", headers=token, json={
            "description": "Essays mit kurzen Absätzen.", "policy": "cloud_allowed",
            "expected_revision": 1})
        assert essay_second.status_code == 200 and essay_second.json()["revision"] == 2
        essay_restore = client.post("/style/profiles/essay/restore", headers=token, json={
            "revision": 1, "expected_revision": 2})
        assert essay_restore.status_code == 200
        assert essay_restore.json()["revision"] == 3
        assert essay_restore.json()["effective_description"] == "Essays mit klarer These."
        assert essay_restore.json()["effective_policy"] == "local_only"


def test_restored_typed_policy_controls_cloud_drafts(tmp_path, monkeypatch):
    calls = []

    async def generate(_db, _config, _purpose, messages):
        calls.append(messages[-1]["content"])
        return Completion("Eine E-Mail.", 20, 5, 0, "reported")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_PROVIDER="openrouter",
                        JARVIS_OPENROUTER_API_KEY="fixture", JARVIS_OPENROUTER_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        token = csrf(client)
        assert client.put("/style/profile", headers=token, json={
            "description": "Allgemein freigegeben.", "policy": "cloud_allowed",
            "expected_revision": 0}).status_code == 200
        local = client.put("/style/profiles/email", headers=token, json={
            "description": "E-Mail nur lokal.", "policy": "local_only",
            "expected_revision": 0})
        assert local.status_code == 200 and local.json()["revision"] == 1
        request = {"kind": "email", "brief": "Antworte freundlich.", "allow_cloud": True}
        assert client.post("/drafts/generate", headers=token, json=request).status_code == 422
        cloud = client.put("/style/profiles/email", headers=token, json={
            "description": "E-Mail für Cloud.", "policy": "cloud_allowed",
            "expected_revision": 1})
        assert cloud.status_code == 200 and cloud.json()["revision"] == 2
        old = client.post("/drafts/generate", headers=token, json=request)
        assert old.status_code == 200 and old.json()["style_snapshot"] == "E-Mail für Cloud."
        assert client.post("/style/profiles/email/restore", headers=token, json={
            "revision": 1, "expected_revision": 2}).status_code == 200
        assert client.post("/drafts/generate", headers=token, json=request).status_code == 422
        assert client.get(f"/drafts/{old.json()['id']}").json()["style_snapshot"] == (
            "E-Mail für Cloud.")
        active = client.post("/style/profiles/email/restore", headers=token, json={
            "revision": 2, "expected_revision": 3})
        assert active.status_code == 200 and active.json()["effective_policy"] == "cloud_allowed"
        assert client.post("/drafts/generate", headers=token, json=request).status_code == 200
        assert len(calls) == 2


def test_schema36_migration_seeds_only_active_profiles(tmp_path):
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    store = DraftStore(db, root)
    store.set_profile("Vorhandenes allgemeines Profil.", "local_only")
    store.set_typed_profile("essay", "Vorhandenes Essay-Profil.", "cloud_allowed")
    with db.connect() as connection:
        connection.execute("DROP INDEX chats_archive_updated")
        connection.execute("ALTER TABLE chats DROP COLUMN archived_at")
        connection.execute("DROP TABLE style_profile_revisions")
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
        connection.execute("PRAGMA user_version=36")
    migrated = Database(root / "jarvis.db")
    profiles = DraftStore(migrated, root)
    with migrated.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 53
    assert profiles.profile_history("general")["revision"] == 1
    assert profiles.profile_history("general")["versions"][0]["description"] == (
        "Vorhandenes allgemeines Profil.")
    assert profiles.profile_history("essay")["revision"] == 1
    assert profiles.profile_history("email")["versions"] == []
    assert profiles.typed_profile("essay")["effective_policy"] == "cloud_allowed"
