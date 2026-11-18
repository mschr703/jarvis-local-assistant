"""Refresh one approved source without scanning or changing its neighbors."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore


def sample(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    first, second = root / "Erster.md", root / "Zweiter.md"
    first.write_text("Projekt Orion: alte Entscheidung.", encoding="utf-8")
    second.write_text("Projekt Orion: Nachbarbeleg bleibt.", encoding="utf-8")
    db = Database(tmp_path / "data" / "jarvis.db")
    docs = DocumentStore(db)
    folder = docs.add_folder(str(root), "local_only")
    assert docs.index_folder(folder["id"])["indexed"] == 2
    indexed = {item["path"]: item for item in docs.documents()}
    return db, docs, indexed, first, second


def test_one_document_reindexes_changed_content_and_preserves_neighbor(tmp_path):
    _, docs, indexed, first, second = sample(tmp_path)
    old_neighbor = indexed[str(second)]
    first.write_text("Projekt Orion: neue Entscheidung.", encoding="utf-8")
    result = docs.index_document(indexed[str(first)]["id"])
    assert result["outcome"] == "indexed"
    assert result["document"]["sha256"] == hashlib.sha256(first.read_bytes()).hexdigest()
    assert result["document"]["id"] == indexed[str(first)]["id"]
    assert docs.get_document(old_neighbor["id"]) == old_neighbor
    assert any("neue Entscheidung" in hit["text"] for hit in docs.search("neue"))
    assert not any("alte Entscheidung" in hit["text"] for hit in docs.search("alte"))
    assert docs.index_document(indexed[str(first)]["id"])["outcome"] == "unchanged"


def test_missing_original_removes_only_that_document(tmp_path):
    _, docs, indexed, first, second = sample(tmp_path)
    first.unlink()
    result = docs.index_document(indexed[str(first)]["id"])
    assert result == {"document_id": indexed[str(first)]["id"],
                      "outcome": "removed", "document": None}
    assert docs.get_document(indexed[str(first)]["id"]) is None
    assert docs.get_document(indexed[str(second)]["id"]) == indexed[str(second)]
    assert docs.search("alte") == []


def test_excluded_and_redirected_document_are_not_reindexed(tmp_path):
    _, docs, indexed, first, _ = sample(tmp_path)
    document_id = indexed[str(first)]["id"]
    docs.set_excluded(document_id, True)
    with pytest.raises(ValueError, match="Ausgeschlossenes"):
        docs.index_document(document_id)
    docs.set_excluded(document_id, False)
    outside = tmp_path / "outside.md"
    outside.write_text("Privat außerhalb der Freigabe.", encoding="utf-8")
    first.unlink()
    try:
        first.symlink_to(outside)
    except OSError:
        pytest.skip("Windows erlaubt hier keine Symlinks")
    with pytest.raises(ValueError, match="umgeleitet"):
        docs.index_document(document_id)
    assert docs.get_document(document_id)["sha256"] == indexed[str(first)]["sha256"]
    assert docs.search("Privat") == []


def test_single_document_refuses_a_path_outside_its_saved_grant(tmp_path):
    db, docs, indexed, first, second = sample(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("Geheimtext außerhalb der Freigabe.", encoding="utf-8")
    document_id = indexed[str(first)]["id"]
    with db.connect() as connection:
        connection.execute("UPDATE documents SET path=? WHERE id=?", (str(outside), document_id))
    with pytest.raises(ValueError, match="freigegebenen Ordner"):
        docs.index_document(document_id)
    assert docs.get_document(indexed[str(second)]["id"]) == indexed[str(second)]
    assert docs.search("Geheimtext") == []


def test_single_document_route_requires_csrf_and_reports_fresh_status(tmp_path):
    db, docs, indexed, first, _ = sample(tmp_path)
    document_id = indexed[str(first)]["id"]
    first.write_text("Projekt Orion: neuer API-Stand.", encoding="utf-8")
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=db.path.parent,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        token = client.get("/session").json()["csrf"]
        endpoint = f"/documents/{document_id}/index"
        assert client.post(endpoint).status_code == 403
        response = client.post(endpoint, headers={"x-jarvis-csrf": token})
        assert response.status_code == 200
        assert response.json()["outcome"] == "indexed"
        assert response.json()["document"]["sha256"] == docs.get_document(document_id)["sha256"]
        assert client.post(endpoint, headers={"x-jarvis-csrf": token}).json()["outcome"] == "unchanged"
        assert client.post("/documents/missing/index", headers={"x-jarvis-csrf": token}).status_code == 404


def test_ocr_pending_remains_explicit_after_single_refresh(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    blank = root / "Leer.txt"
    blank.write_text("   ", encoding="utf-8")
    db = Database(tmp_path / "data" / "jarvis.db")
    docs = DocumentStore(db)
    folder = docs.add_folder(str(root), "local_only")
    assert docs.index_folder(folder["id"])["ocr_pending"] == 1
    document_id = docs.documents()[0]["id"]
    result = docs.index_document(document_id)
    assert result["outcome"] == "ocr_pending"
    assert result["document"]["status"] == "ocr_pending"


def test_single_file_parser_error_does_not_change_neighbor(tmp_path, monkeypatch):
    _, docs, indexed, first, second = sample(tmp_path)
    def broken_index(_folder, _path):
        raise ValueError("Synthetisch defektes Format")

    monkeypatch.setattr(docs, "_index_file", broken_index)
    result = docs.index_document(indexed[str(first)]["id"])
    assert result["outcome"] == "error"
    assert result["document"]["status"] == "error"
    assert docs.search("alte") == []
    assert docs.get_document(indexed[str(second)]["id"]) == indexed[str(second)]
