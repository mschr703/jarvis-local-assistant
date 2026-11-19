import hashlib
import io
from pathlib import Path

import pytest
from pypdf import PdfWriter

from jarvis.db import Database
from jarvis.documents import DocumentStore


def make_store(tmp_path):
    return DocumentStore(Database(tmp_path / "state" / "jarvis.db"))


def test_index_search_policy_change_and_file_refresh(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    file = root / "entscheidung.md"
    file.write_text("Projekt Orion nutzt SQLite für lokale Daten.", encoding="utf-8")
    store = make_store(tmp_path)
    folder = store.add_folder(str(root), "local_only")
    assert store.index_folder(folder["id"])["indexed"] == 1
    result = store.search("SQLite")
    assert len(result) == 1
    assert result[0]["path"] == str(file.resolve())
    assert result[0]["policy"] == "local_only"
    assert store.search("SQLite", cloud_only=True) == []
    store.set_folder_policy(folder["id"], "cloud_allowed")
    assert len(store.search("SQLite", cloud_only=True)) == 1
    file.write_text("Projekt Orion nutzt PostgreSQL für lokale Daten.", encoding="utf-8")
    assert store.index_folder(folder["id"])["indexed"] == 1
    assert store.search("SQLite") == []
    assert len(store.search("PostgreSQL")) == 1
    file.unlink()
    assert store.index_folder(folder["id"])["removed"] == 1
    assert store.search("PostgreSQL") == []


def test_deleted_file_is_removed_despite_other_file_parse_error(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    removed = root / "removed.txt"
    removed.write_text("Verwaister Suchmarker", encoding="utf-8")
    store = make_store(tmp_path)
    folder = store.add_folder(str(root), "local_only")
    assert store.index_folder(folder["id"])["indexed"] == 1
    removed.unlink()
    (root / "broken.pdf").write_bytes(b"not a PDF")

    counts = store.index_folder(folder["id"])
    assert counts["errors"] == 1 and counts["removed"] == 1
    assert store.search("Suchmarker") == []


def test_path_resolution_error_keeps_unvisited_index_entries(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    removed = root / "removed.txt"
    removed.write_text("Bis zum sicheren Scan behalten", encoding="utf-8")
    store = make_store(tmp_path)
    folder = store.add_folder(str(root), "local_only")
    assert store.index_folder(folder["id"])["indexed"] == 1
    removed.unlink()
    transient = root / "transient.txt"
    transient.write_text("Anderer Inhalt", encoding="utf-8")
    original_resolve = Path.resolve

    def failing_resolve(path, *args, **kwargs):
        if path == transient:
            raise OSError("vorübergehend nicht auflösbar")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", failing_resolve)
    counts = store.index_folder(folder["id"])
    assert counts["errors"] == 1 and counts["removed"] == 0
    assert len(store.search("behalten")) == 1


def test_unreadable_subdirectory_is_reported_without_deleting_old_index(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    indexed = root / "indexed.txt"
    indexed.write_text("Eintrag bis zum nächsten sicheren Scan", encoding="utf-8")
    store = make_store(tmp_path)
    folder = store.add_folder(str(root), "local_only")
    assert store.index_folder(folder["id"])["indexed"] == 1
    indexed.unlink()
    inaccessible = root / "inaccessible"
    inaccessible.mkdir()
    (inaccessible / "new.txt").write_text("Neuer Eintrag", encoding="utf-8")
    original_resolve = Path.resolve

    def failing_resolve(path, *args, **kwargs):
        if path == inaccessible:
            raise OSError("Unterordner vorübergehend nicht lesbar")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", failing_resolve)
    counts = store.index_folder(folder["id"])
    assert counts["errors"] == 1 and counts["removed"] == 0
    assert len(store.search("sicheren")) == 1


def test_file_changed_while_reading_is_not_indexed(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    file = root / "note.txt"
    file.write_text("Vorher", encoding="utf-8")
    store = make_store(tmp_path)
    folder = store.add_folder(str(root), "local_only")
    original_open = Path.open

    def changing_open(path, *args, **kwargs):
        if path == file and args and args[0] == "rb":
            with original_open(path, "rb") as stream:
                original = stream.read()
            with original_open(path, "wb") as stream:
                stream.write(b"Nachher und viel laenger")
            return io.BytesIO(original)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", changing_open)
    assert store.index_folder(folder["id"])["errors"] == 1
    assert store.documents()[0]["error"] == "Datei wurde während der Indexierung geändert"
    assert store.search("Vorher") == []
    monkeypatch.setattr(Path, "open", original_open)
    assert store.index_folder(folder["id"])["indexed"] == 1
    assert len(store.search("Nachher")) == 1


def test_blank_pdf_is_explicit_ocr_pending(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with (root / "scan.pdf").open("wb") as stream:
        writer.write(stream)
    store = make_store(tmp_path)
    folder = store.add_folder(str(root), "local_only")
    assert store.index_folder(folder["id"])["ocr_pending"] == 1
    assert store.documents()[0]["status"] == "ocr_pending"
    assert store.search("scan") == []


def test_reincluded_document_rebuilds_passages_even_with_same_file_hash(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "notiz.txt").write_text("Einmaliger Marker", encoding="utf-8")
    store = make_store(tmp_path)
    folder = store.add_folder(str(root), "local_only")
    assert store.index_folder(folder["id"])["indexed"] == 1
    document_id = store.documents()[0]["id"]
    assert len(store.search("Marker")) == 1
    store.set_excluded(document_id, True)
    assert store.search("Marker") == []
    assert store.index_folder(folder["id"])["indexed"] == 0
    reincluded = store.set_excluded(document_id, False)
    assert reincluded["status"] == "pending_index"
    assert store.search("Marker") == []
    assert store.index_folder(folder["id"])["indexed"] == 1
    assert len(store.search("Marker")) == 1


def test_transient_read_error_can_rebuild_same_file_hash(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    file = root / "note.txt"
    file.write_text("Wiederherstellbarer Inhalt", encoding="utf-8")
    store = make_store(tmp_path)
    folder = store.add_folder(str(root), "local_only")
    assert store.index_folder(folder["id"])["indexed"] == 1

    original_open = Path.open
    failed = False

    def fail_once(path, *args, **kwargs):
        nonlocal failed
        if path == file and not failed:
            failed = True
            raise OSError("vorübergehend nicht lesbar")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_once)
    assert store.index_folder(folder["id"])["errors"] == 1
    assert store.documents()[0]["status"] == "error"
    assert store.search("Wiederherstellbarer") == []
    assert store.index_folder(folder["id"])["indexed"] == 1
    assert len(store.search("Wiederherstellbarer")) == 1


def test_explicit_memory_lifecycle_survives_restart(tmp_path):
    store = make_store(tmp_path)
    memory = store.add_memory("Orion nutzt SQLite", "Projektbesprechung 25.10.", "local_only")
    reloaded = make_store(tmp_path)
    assert reloaded.memories("SQLite")[0]["source_ref"] == "Projektbesprechung 25.10."
    reloaded.update_memory(memory["id"], "Orion nutzt PostgreSQL", "Beschluss 26.10.", "cloud_allowed")
    assert reloaded.memories("SQLite") == []
    assert reloaded.memories("PostgreSQL")[0]["policy"] == "cloud_allowed"
    reloaded.delete_memory(memory["id"])
    assert reloaded.memories("PostgreSQL") == []


def test_directory_traversal_and_symlink_escape_are_not_indexed(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("Vertraulicher Marker", encoding="utf-8")
    store = make_store(tmp_path)
    folder = store.add_folder(str(root), "local_only")
    link = root / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        return  # Windows ohne Symlink-Recht; kanonische Prüfung wird sonst getestet.
    store.index_folder(folder["id"])
    assert store.search("Marker") == []
    assert all(Path(item["path"]).is_relative_to(root) for item in store.documents())


def test_overlapping_grants_cannot_change_cloud_policy_implicitly(tmp_path):
    root = tmp_path / "allowed"
    nested = root / "subfolder"
    nested.mkdir(parents=True)
    (nested / "private.txt").write_text("Privater Projektinhalt", encoding="utf-8")
    store = make_store(tmp_path)
    granted = store.add_folder(str(root), "local_only")
    store.index_folder(granted["id"])
    with pytest.raises(ValueError, match="Überlappende"):
        store.add_folder(str(nested), "cloud_allowed")
    assert store.search("Privater", cloud_only=True) == []


def test_granted_folder_retargeting_requires_new_consent(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "original.txt").write_text("Erlaubter Inhalt", encoding="utf-8")
    private = tmp_path / "private"
    private.mkdir()
    (private / "secret.txt").write_text("Geheimer Inhalt", encoding="utf-8")
    store = make_store(tmp_path)
    folder = store.add_folder(str(root), "cloud_allowed")
    assert store.index_folder(folder["id"])["indexed"] == 1
    original_resolve = Path.resolve

    def retargeted(path, *args, **kwargs):
        if path == root:
            return private
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", retargeted)
    with pytest.raises(ValueError, match="umgeleitet"):
        store.index_folder(folder["id"])
    assert store.search("Geheimer", cloud_only=True) == []


def test_limited_scan_keeps_unvisited_index_entries(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "a.txt").write_text("Erstes Dokument", encoding="utf-8")
    (root / "b.txt").write_text("Zweites Dokument", encoding="utf-8")
    store = make_store(tmp_path)
    folder = store.add_folder(str(root), "local_only")
    assert store.index_folder(folder["id"])["indexed"] == 2
    monkeypatch.setattr("jarvis.documents.MAX_FILES_PER_SCAN", 1)
    counts = store.index_folder(folder["id"])
    assert counts["limited"] == 1 and counts["removed"] == 0
    assert len(store.documents()) == 2
    assert len(store.search("Zweites")) == 1


def test_directory_limit_keeps_unvisited_index_entries(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    nested = root / "nested"
    nested.mkdir(parents=True)
    file = nested / "entry.txt"
    file.write_text("Bis zum nächsten Scan erhalten", encoding="utf-8")
    store = make_store(tmp_path)
    folder = store.add_folder(str(root), "local_only")
    assert store.index_folder(folder["id"])["indexed"] == 1
    file.unlink()
    monkeypatch.setattr("jarvis.documents.MAX_DIRECTORIES_PER_SCAN", 1)

    counts = store.index_folder(folder["id"])
    assert counts["limited"] == 1 and counts["removed"] == 0
    assert len(store.search("erhalten")) == 1


def test_junction_subdirectory_is_not_traversed(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    junction = root / "linked"
    junction.mkdir(parents=True)
    (junction / "secret.txt").write_text("Nicht durch Junction lesen", encoding="utf-8")
    store = make_store(tmp_path)
    folder = store.add_folder(str(root), "local_only")
    original_is_junction = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda path: path == junction or original_is_junction(path))

    assert store.index_folder(folder["id"])["indexed"] == 0
    assert store.search("Junction") == []


def test_extracted_text_limit_records_visible_error(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "large.txt").write_text("abcdefghijklmnop", encoding="utf-8")
    monkeypatch.setattr("jarvis.documents.MAX_EXTRACTED_CHARS", 10)
    store = make_store(tmp_path)
    folder = store.add_folder(str(root), "local_only")
    assert store.index_folder(folder["id"])["errors"] == 1
    document = store.documents()[0]
    assert document["status"] == "error"
    assert store.search("abcdefghijklmnop") == []


def test_walk_error_preserves_existing_index_entries(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "retained.txt").write_text("Wichtiger Inhalt", encoding="utf-8")
    store = make_store(tmp_path)
    folder = store.add_folder(str(root), "local_only")
    assert store.index_folder(folder["id"])["indexed"] == 1

    def inaccessible_walk(path, *, followlinks, onerror):
        onerror(PermissionError("Ordner vorübergehend nicht lesbar"))
        yield path, [], []

    monkeypatch.setattr("jarvis.documents.os.walk", inaccessible_walk)
    counts = store.index_folder(folder["id"])
    assert counts["errors"] == 1 and counts["removed"] == 0
    assert len(store.search("Wichtiger")) == 1


def test_policy_revocation_during_scan_is_not_reverted(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "source.txt").write_text("Freigegebener Projekttext", encoding="utf-8")
    store = make_store(tmp_path)
    folder = store.add_folder(str(root), "cloud_allowed")
    original_sha = hashlib.sha256
    called = False

    def change_policy(data):
        nonlocal called
        if not called:
            called = True
            store.set_folder_policy(folder["id"], "local_only")
        return original_sha(data)

    monkeypatch.setattr("jarvis.documents.hashlib.sha256", change_policy)
    assert store.index_folder(folder["id"])["indexed"] == 1
    assert len(store.search("Projekttext")) == 1
    assert store.search("Projekttext", cloud_only=True) == []


def test_exclusion_during_refresh_cannot_reintroduce_passages(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    file = root / "source.txt"
    file.write_text("Erster Projekttext", encoding="utf-8")
    store = make_store(tmp_path)
    folder = store.add_folder(str(root), "local_only")
    store.index_folder(folder["id"])
    document_id = store.documents()[0]["id"]
    file.write_text("Zweiter Projekttext", encoding="utf-8")
    original_sha = hashlib.sha256
    called = False

    def exclude_during_refresh(data):
        nonlocal called
        if not called:
            called = True
            store.set_excluded(document_id, True)
        return original_sha(data)

    monkeypatch.setattr("jarvis.documents.hashlib.sha256", exclude_during_refresh)
    store.index_folder(folder["id"])
    assert store.documents()[0]["excluded"] == 1
    assert store.search("Zweiter") == []
