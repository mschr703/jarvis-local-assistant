import hashlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database, utcnow
from jarvis.documents import DocumentStore
from jarvis.markdown import MarkdownConflict, MarkdownNotes


@pytest.fixture
def note(tmp_path):
    root = tmp_path / "freigegeben"
    root.mkdir()
    path = root / "projekt.md"
    path.write_bytes(b"# Projekt\nAlte Entscheidung\n")
    db = Database(tmp_path / "data" / "jarvis.db")
    documents = DocumentStore(db)
    folder = documents.add_folder(str(root), "local_only")
    assert documents.index_folder(folder["id"])["indexed"] == 1
    document = documents.documents()[0]
    return db, documents, MarkdownNotes(db, documents, b"test-secret"), path, document


def test_markdown_preview_save_reindex_and_undo(note):
    _, documents, markdown, path, document = note
    opened = markdown.read(document["id"])
    updated = "# Projekt\nNeue Entscheidung\n"
    preview = markdown.preview(document["id"], opened["sha256"], updated)
    assert "-Alte Entscheidung" in preview["diff"]
    assert "+Neue Entscheidung" in preview["diff"]
    assert path.read_bytes().decode("utf-8") == opened["content"]

    saved = markdown.save(document["id"], opened["sha256"], updated,
                          preview["approval_token"])
    assert path.read_bytes().decode("utf-8") == updated
    assert saved["index_error"] is None
    assert documents.get_document(document["id"])["sha256"] == saved["sha256"]
    assert documents.search("Neue")[0]["document_id"] == document["id"]
    assert documents.search("Alte") == []
    assert markdown.read(document["id"])["undo_revision_id"] == saved["revision_id"]

    undone = markdown.undo(document["id"], saved["revision_id"])
    assert undone["index_error"] is None
    assert path.read_bytes().decode("utf-8") == opened["content"]
    assert documents.search("Alte")[0]["document_id"] == document["id"]
    assert documents.search("Neue") == []
    assert markdown.read(document["id"])["undo_revision_id"] is None


def test_markdown_external_change_blocks_save_and_undo(note):
    _, _, markdown, path, document = note
    opened = markdown.read(document["id"])
    preview = markdown.preview(document["id"], opened["sha256"], "Neue Fassung")
    path.write_bytes("Änderung außerhalb von Jarvis".encode())
    with pytest.raises(MarkdownConflict, match="seit der Vorschau"):
        markdown.save(document["id"], opened["sha256"], "Neue Fassung",
                      preview["approval_token"])
    assert path.read_bytes().decode("utf-8") == "Änderung außerhalb von Jarvis"

    path.write_bytes(opened["content"].encode("utf-8"))
    saved = markdown.save(document["id"], opened["sha256"], "Neue Fassung",
                          preview["approval_token"])
    path.write_bytes("Noch eine externe Änderung".encode())
    with pytest.raises(MarkdownConflict, match="seit dem Speichern"):
        markdown.undo(document["id"], saved["revision_id"])
    assert path.read_bytes().decode("utf-8") == "Noch eine externe Änderung"


def test_markdown_requires_preview_and_respects_exclusion(note):
    _, documents, markdown, path, document = note
    opened = markdown.read(document["id"])
    with pytest.raises(ValueError, match="Änderungsvorschau"):
        markdown.save(document["id"], opened["sha256"], "Neuer Text", "0" * 64)
    assert path.read_bytes().decode("utf-8") == opened["content"]
    documents.set_excluded(document["id"], True)
    with pytest.raises(ValueError, match="indexierte, freigegebene"):
        markdown.read(document["id"])


def test_markdown_recovers_prepared_revision_after_process_crash(note):
    db, documents, _markdown, path, document = note
    before = path.read_bytes()
    after = b"# Projekt\nNach Absturz\n"
    revision_id = "55555555-5555-4555-8555-555555555555"
    now = utcnow()
    with db.connect() as connection:
        connection.execute("INSERT INTO markdown_revisions VALUES(?,?,?,?,?,'prepared',?,?)",
                           (revision_id, document["id"], hashlib.sha256(before).hexdigest(),
                            hashlib.sha256(after).hexdigest(), before, now, now))
    path.write_bytes(after)
    restarted = MarkdownNotes(db, documents, b"different-secret")
    assert restarted.read(document["id"])["undo_revision_id"] == revision_id
    with db.connect() as connection:
        assert connection.execute("SELECT status FROM markdown_revisions WHERE id=?",
                                  (revision_id,)).fetchone()[0] == "applied"
    restarted.undo(document["id"], revision_id)
    assert path.read_bytes() == before


def test_markdown_rejects_non_utf8_and_non_markdown(note):
    _, documents, markdown, path, document = note
    path.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="UTF-8"):
        markdown.read(document["id"])
    path.write_text("Wieder gültig", encoding="utf-8")
    other = Path(path.parent / "datei.txt")
    other.write_text("Text", encoding="utf-8")
    assert documents.index_folder(document["folder_id"])["indexed"] >= 1
    text_document = next(item for item in documents.documents() if item["path"] == str(other))
    with pytest.raises(ValueError, match="Markdown"):
        markdown.read(text_document["id"])


def test_markdown_keeps_windows_crlf_line_endings(note):
    _, documents, markdown, path, document = note
    path.write_bytes(b"# Projekt\r\nAlte Entscheidung\r\n")
    documents.index_folder(document["folder_id"])
    opened = markdown.read(document["id"])
    preview = markdown.preview(document["id"], opened["sha256"],
                               "# Projekt\nNeue Entscheidung\n")
    assert "-# Projekt" not in preview["diff"]
    saved = markdown.save(document["id"], opened["sha256"],
                          "# Projekt\nNeue Entscheidung\n", preview["approval_token"])
    assert path.read_bytes() == b"# Projekt\r\nNeue Entscheidung\r\n"
    assert saved["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_markdown_undo_follows_revision_order_even_when_content_repeats(note):
    _, _, markdown, path, document = note
    original = markdown.read(document["id"])
    text_a = "Erste Fassung\n"
    text_b = original["content"]
    first_preview = markdown.preview(document["id"], original["sha256"], text_a)
    first = markdown.save(document["id"], original["sha256"], text_a,
                          first_preview["approval_token"])
    second_preview = markdown.preview(document["id"], first["sha256"], text_b)
    second = markdown.save(document["id"], first["sha256"], text_b,
                           second_preview["approval_token"])
    assert markdown.read(document["id"])["undo_revision_id"] == second["revision_id"]
    with pytest.raises(MarkdownConflict, match="letzte Änderung"):
        markdown.undo(document["id"], first["revision_id"])
    markdown.undo(document["id"], second["revision_id"])
    assert path.read_text(encoding="utf-8") == text_a
    assert markdown.read(document["id"])["undo_revision_id"] == first["revision_id"]
    markdown.undo(document["id"], first["revision_id"])
    assert path.read_text(encoding="utf-8") == original["content"]


def test_markdown_save_rechecks_exclusion_before_replacing_file(note, monkeypatch):
    _, documents, markdown, path, document = note
    original = markdown.read(document["id"])
    preview = markdown.preview(document["id"], original["sha256"], "Neue Fassung")
    sync = os.fsync

    def exclude_then_sync(fd):
        documents.set_excluded(document["id"], True)
        sync(fd)

    monkeypatch.setattr(os, "fsync", exclude_then_sync)
    with pytest.raises(ValueError, match="indexierte, freigegebene"):
        markdown.save(document["id"], original["sha256"], "Neue Fassung",
                      preview["approval_token"])
    assert path.read_bytes() == original["content"].encode()


def test_markdown_revision_survives_backup_but_external_file_is_not_copied(note, tmp_path):
    db, _, markdown, path, document = note
    original = markdown.read(document["id"])
    preview = markdown.preview(document["id"], original["sha256"], "Neue Fassung")
    saved = markdown.save(document["id"], original["sha256"], "Neue Fassung",
                          preview["approval_token"])
    archive = tmp_path / "snapshot.zip"
    create_backup(db.path.parent, archive)
    restored_root = tmp_path / "restored"
    restore_backup(archive, restored_root)
    restored_db = Database(restored_root / "jarvis.db")
    restored = MarkdownNotes(restored_db, DocumentStore(restored_db), b"other-secret")
    assert restored.read(document["id"])["undo_revision_id"] == saved["revision_id"]
    with restored_db.connect() as connection:
        revision = connection.execute("SELECT before_bytes FROM markdown_revisions WHERE id=?",
                                      (saved["revision_id"],)).fetchone()
    assert revision[0] == original["content"].encode()
    path.write_text("Externe Änderung nach Backup", encoding="utf-8")
    with pytest.raises(MarkdownConflict, match="seit dem Speichern"):
        restored.undo(document["id"], saved["revision_id"])
    assert path.read_text(encoding="utf-8") == "Externe Änderung nach Backup"


def test_excluding_document_erases_saved_markdown_revision(note):
    db, documents, markdown, path, document = note
    opened = markdown.read(document["id"])
    preview = markdown.preview(document["id"], opened["sha256"], "Nur kurz gespeichert")
    markdown.save(document["id"], opened["sha256"], "Nur kurz gespeichert",
                  preview["approval_token"])
    documents.set_excluded(document["id"], True)
    with db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM markdown_revisions WHERE document_id=?",
                                  (document["id"],)).fetchone()[0] == 0
    assert path.read_text(encoding="utf-8") == "Nur kurz gespeichert"


def test_markdown_rejects_redirected_parent_directory(tmp_path, monkeypatch):
    root = tmp_path / "freigegeben"
    nested = root / "projekt"
    nested.mkdir(parents=True)
    path = nested / "notiz.md"
    path.write_text("Ursprünglich", encoding="utf-8")
    db = Database(tmp_path / "data" / "jarvis.db")
    documents = DocumentStore(db)
    folder = documents.add_folder(str(root), "local_only")
    documents.index_folder(folder["id"])
    document = documents.documents()[0]
    original_is_junction = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda candidate: (
        candidate == nested or original_is_junction(candidate)))
    markdown = MarkdownNotes(db, documents, b"test-secret")
    with pytest.raises(ValueError, match="Verknüpfte"):
        markdown.read(document["id"])
    assert path.read_text(encoding="utf-8") == "Ursprünglich"


def test_markdown_api_requires_preview_and_rejects_stale_file(tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    path = root / "brief.md"
    path.write_bytes(b"# Brief\nAlt\n")
    settings = Settings(JARVIS_DATA_DIR=tmp_path / "data", JARVIS_PROVIDER="ollama")
    with TestClient(create_app(settings)) as client:
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        folder = client.post("/folders", headers=headers,
                             json={"path": str(root), "policy": "local_only"}).json()
        assert client.post(f"/folders/{folder['id']}/index", headers=headers).status_code == 200
        document_id = client.get("/documents").json()[0]["id"]
        opened = client.get(f"/documents/{document_id}/markdown").json()
        url = f"/documents/{document_id}/markdown"
        data = {"expected_sha256": opened["sha256"], "content": "# Brief\nNeu\n"}
        assert client.put(url, headers=headers, json={**data, "approval_token": "0" * 64}).status_code == 422
        preview = client.post(url + "/preview", headers=headers, json=data).json()
        path.write_bytes(b"Fremde Aenderung")
        conflict = client.put(url, headers=headers,
                              json={**data, "approval_token": preview["approval_token"]})
        assert conflict.status_code == 409
        assert path.read_bytes() == b"Fremde Aenderung"
        path.write_bytes(opened["content"].encode())
        saved = client.put(url, headers=headers,
                           json={**data, "approval_token": preview["approval_token"]})
        assert saved.status_code == 200
        revision_id = saved.json()["revision_id"]
        assert client.post(f"{url}/{revision_id}/undo", headers=headers).status_code == 200
        assert path.read_bytes() == opened["content"].encode()
