"""Eigene, bewusst ausgewählte lokale Texte werden nur aus geprüften Indexfassungen importiert."""

import sqlite3

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.drafts import DraftStore
from jarvis.provider import Completion


def token(client):
    return {"x-jarvis-csrf": client.get("/session").json()["csrf"]}


def indexed_file(client, csrf, tmp_path, name="mein-text.md", content=None):
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    path = source / name
    path.write_text(content or "Dies ist mein eigener sachlicher Stil mit kurzen klaren Sätzen.",
                    encoding="utf-8")
    folder = client.post("/folders", headers=csrf,
                         json={"path": str(source), "policy": "local_only"}).json()
    assert client.post(f"/folders/{folder['id']}/index", headers=csrf).status_code == 200
    document = next(item for item in client.get("/documents").json() if item["path"] == str(path))
    return path, folder, document


def import_payload(document, **extra):
    return {"document_id": document["id"], "expected_sha256": document["sha256"],
            "title": "Mein Beispiel", "confirm_own_text": True, **extra}


def test_import_is_explicit_idempotent_local_and_usable_for_draft(tmp_path, monkeypatch):
    prompts = []

    async def generate(_db, _settings, _purpose, messages):
        prompts.append(messages[-1]["content"])
        return Completion("Ein kurzer Text.", 20, 10, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    root = tmp_path / "data"
    settings = Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        csrf = token(client)
        path, _, document = indexed_file(client, csrf, tmp_path)
        denied = client.post("/style/samples/import", headers=csrf,
                             json=import_payload(document, confirm_own_text=False))
        assert denied.status_code == 422
        assert client.get("/style/samples").json() == []
        imported = client.post("/style/samples/import", headers=csrf,
                               json=import_payload(document))
        assert imported.status_code == 200, imported.text
        sample = imported.json()
        assert sample["policy"] == "local_only" and sample["content"] == path.read_text(encoding="utf-8")
        assert sample["source_path"] == str(path)
        assert sample["source_sha256"] == document["sha256"]
        with pytest.raises(ValueError, match="Cloud"):
            DraftStore(Database(root / "jarvis.db"), root).selected_samples(
                [sample["id"]], cloud_only=True)
        assert client.post("/style/samples/import", headers=csrf,
                           json=import_payload(document)).json()["id"] == sample["id"]
        assert len(client.get("/style/samples").json()) == 1
        draft = client.post("/drafts/generate", headers=csrf, json={
            "kind": "text", "brief": "Kurzer Test", "style_sample_ids": [sample["id"]]})
        assert draft.status_code == 200 and sample["content"] in prompts[0]
        path.write_text("Die Quelldatei ist später verändert.", encoding="utf-8")
        assert client.get("/style/samples").json()[0]["content"] == sample["content"]
    with TestClient(create_app(settings)) as client:
        token(client)
        assert client.get("/style/samples").json()[0]["source_sha256"] == document["sha256"]
    archive = tmp_path / "samples.zip"
    assert create_backup(root, archive)["schema"] == 52
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    recovered = DraftStore(Database(restored / "jarvis.db"), restored).samples()[0]
    assert recovered["content"] == sample["content"]
    assert recovered["source_path"] == str(path)


def test_import_rejects_changed_excluded_outside_and_oversize_text(tmp_path):
    root = tmp_path / "data"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root))) as client:
        csrf = token(client)
        path, folder, document = indexed_file(client, csrf, tmp_path)
        path.write_text("Geändert nach der Indexierung.", encoding="utf-8")
        assert client.post("/style/samples/import", headers=csrf,
                           json=import_payload(document)).status_code == 422
        assert client.post(f"/folders/{folder['id']}/index", headers=csrf).status_code == 200
        current = client.get(f"/documents/{document['id']}").json()
        assert current["sha256"] != document["sha256"]
        assert client.post("/style/samples/import", headers=csrf,
                           json=import_payload(document)).status_code == 422
        assert client.patch(f"/documents/{document['id']}/excluded", headers=csrf,
                            json={"excluded": True}).status_code == 200
        assert client.post("/style/samples/import", headers=csrf,
                           json=import_payload(current)).status_code == 422

        long_path = path.parent / "zu-lang.txt"
        long_path.write_text("x" * 20_001, encoding="utf-8")
        assert client.post(f"/folders/{folder['id']}/index", headers=csrf).status_code == 200
        long_doc = next(item for item in client.get("/documents").json()
                        if item["path"] == str(long_path))
        assert client.post("/style/samples/import", headers=csrf,
                           json=import_payload(long_doc)).status_code == 422

        outside = tmp_path / "outside.txt"
        outside.write_text("Nicht freigegeben.", encoding="utf-8")
        with sqlite3.connect(root / "jarvis.db") as db:
            db.execute("UPDATE documents SET path=? WHERE id=?", (str(outside), long_doc["id"]))
        assert client.post("/style/samples/import", headers=csrf,
                           json=import_payload(long_doc)).status_code == 422
        assert client.get("/style/samples").json() == []


def test_import_rejects_pdf_and_migrates_old_profile(tmp_path):
    root = tmp_path / "data"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root))) as client:
        csrf = token(client)
        path, folder, _ = indexed_file(client, csrf, tmp_path)
        pdf = path.parent / "nicht-stilprobe.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        with pdf.open("wb") as stream:
            writer.write(stream)
        assert client.post(f"/folders/{folder['id']}/index", headers=csrf).status_code == 200
        pdf_doc = next(item for item in client.get("/documents").json()
                       if item["path"] == str(pdf))
        assert client.post("/style/samples/import", headers=csrf,
                           json=import_payload(pdf_doc)).status_code == 422

    legacy = tmp_path / "legacy.db"
    Database(legacy)
    with sqlite3.connect(legacy) as db:
        db.execute("DROP TABLE style_sample_origins")
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
        db.execute("PRAGMA user_version=33")
    assert DraftStore(Database(legacy), tmp_path).samples() == []
    with sqlite3.connect(legacy) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 52
