"""A chat ZIP contains only verified sent attachments and no storage paths."""

import io
import json
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from jarvis import chat_export
from jarvis.app import create_app
from jarvis.chat_attachments import MAX_ATTACHMENT_BYTES, ChatAttachmentStore
from jarvis.chat_export import build_chat_export
from jarvis.config import Settings
from jarvis.db import Database


def sample(tmp_path):
    db = Database(tmp_path / "data" / "jarvis.db")
    store = ChatAttachmentStore(db)
    chat = db.create_chat()
    first = store.save(chat["id"], "Beleg.txt", b"Orion erste Seite")
    second = store.save(chat["id"], "Beleg.txt", b"Saturn zweite Seite")
    pending = store.save(chat["id"], "NichtGesendet.txt", b"Bleibt draussen")
    db.add_message(chat["id"], "user", "Bitte `Code` und ``` pruefen",
                   attachment_ids=[first["id"], second["id"]])
    db.add_message(chat["id"], "assistant", "Orion und Saturn sind genannt.")
    return db, store, chat, first, second, pending


def test_export_has_transcript_manifest_and_exact_sent_originals(tmp_path):
    db, store, chat, first, second, pending = sample(tmp_path)
    blob = build_chat_export(db, store, chat["id"])
    with ZipFile(io.BytesIO(blob)) as archive:
        names = archive.namelist()
        assert names == ["transcript.md", "manifest.json",
                         "attachments/001-Beleg.txt", "attachments/002-Beleg.txt"]
        assert archive.read(names[2]) == b"Orion erste Seite"
        assert archive.read(names[3]) == b"Saturn zweite Seite"
        transcript = archive.read("transcript.md").decode("utf-8")
        assert "Bitte `Code` und ``` pruefen" in transcript
        assert "````\nBitte `Code` und ``` pruefen\n````" in transcript
        assert "Orion und Saturn sind genannt." in transcript
        assert "NichtGesendet" not in transcript
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == "jarvis-chat-export-v1"
        assert manifest["chat_id"] == chat["id"]
        assert manifest["message_count"] == 2
        assert [item["id"] for item in manifest["attachments"]] == [first["id"], second["id"]]
        assert all(item["archive_name"] in names for item in manifest["attachments"])
        assert pending["id"] not in str(manifest)
        assert str(tmp_path) not in str(manifest)
        assert all(".." not in name and not name.startswith("/") for name in names)


def test_export_rejects_tampered_missing_and_retargeted_attachment(tmp_path):
    db, store, chat, first, _, _ = sample(tmp_path)
    path = store._path(first["id"], ".txt")
    original = path.read_bytes()
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="weicht"):
        build_chat_export(db, store, chat["id"])
    path.write_bytes(original)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        build_chat_export(db, store, chat["id"])
    path.write_bytes(original)
    with db.connect() as connection:
        connection.execute("UPDATE chat_attachments SET size=? WHERE id=?",
                           (MAX_ATTACHMENT_BYTES + 1, first["id"]))
    with pytest.raises(ValueError, match="weicht"):
        build_chat_export(db, store, chat["id"])
    with db.connect() as connection:
        connection.execute("UPDATE chat_attachments SET size=? WHERE id=?",
                           (len(original), first["id"]))
    store.root = tmp_path / "outside"
    with pytest.raises(ValueError, match="außerhalb"):
        build_chat_export(db, store, chat["id"])


def test_export_limits_and_concurrent_chat_change(tmp_path, monkeypatch):
    db, store, chat, first, _, _ = sample(tmp_path)
    monkeypatch.setattr(chat_export, "MAX_EXPORT_ATTACHMENTS", 1)
    with pytest.raises(ValueError, match="zu viele Anhänge"):
        build_chat_export(db, store, chat["id"])
    monkeypatch.setattr(chat_export, "MAX_EXPORT_ATTACHMENTS", 100)
    monkeypatch.setattr(chat_export, "MAX_TRANSCRIPT_BYTES", 50)
    with pytest.raises(ValueError, match="transkript"):
        build_chat_export(db, store, chat["id"])
    monkeypatch.setattr(chat_export, "MAX_TRANSCRIPT_BYTES", 2 * 1024 * 1024)
    monkeypatch.setattr(chat_export, "MAX_TOTAL_ATTACHMENT_BYTES", 10)
    with pytest.raises(ValueError, match="32 MiB"):
        build_chat_export(db, store, chat["id"])
    monkeypatch.setattr(chat_export, "MAX_TOTAL_ATTACHMENT_BYTES", 32 * 1024 * 1024)
    original_read = store.read_original

    def change_chat(chat_id, attachment_id):
        result = original_read(chat_id, attachment_id)
        if attachment_id == first["id"]:
            db.rename_chat(chat_id, "Geändert")
        return result

    monkeypatch.setattr(store, "read_original", change_chat)
    with pytest.raises(ValueError, match="Gespräch hat sich"):
        build_chat_export(db, store, chat["id"])


def test_export_route_requires_session_and_supports_archived_chat(tmp_path):
    db, _, chat, _, _, _ = sample(tmp_path)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=db.path.parent,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        endpoint = f"/chats/{chat['id']}/export"
        assert client.get(endpoint).status_code == 401
        token = client.get("/session").json()["csrf"]
        assert client.patch(f"/chats/{chat['id']}/archive",
                            json={"archived": True},
                            headers={"x-jarvis-csrf": token}).status_code == 200
        response = client.get(endpoint)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-disposition"].endswith('.zip"')
        with ZipFile(io.BytesIO(response.content)) as archive:
            assert json.loads(archive.read("manifest.json"))["archived"] is True
        assert client.get("/chats/missing/export").status_code == 404
