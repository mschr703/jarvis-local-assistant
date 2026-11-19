"""Downloads return the verified indexed bytes, never an unchecked later read."""

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore


def sample(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    original = root / "Mietvertrag_Übung.md"
    original.write_text("Wohnung: Mietbeginn am Sonntag.", encoding="utf-8")
    db = Database(tmp_path / "data" / "jarvis.db")
    docs = DocumentStore(db)
    folder = docs.add_folder(str(root), "local_only")
    assert docs.index_folder(folder["id"])["indexed"] == 1
    document = docs.documents()[0]
    return db, docs, folder, document, original


def test_download_returns_exact_bytes_and_safe_headers(tmp_path):
    db, _, _, document, original = sample(tmp_path)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=db.path.parent,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        assert client.get(f"/documents/{document['id']}/original").status_code == 401
        client.get("/session")
        response = client.get(f"/documents/{document['id']}/original")
        assert response.status_code == 200
        assert response.content == original.read_bytes()
        assert "Mietvertrag_%C3%9Cbung.md" in response.headers["content-disposition"]
        assert response.headers["content-type"].startswith("application/octet-stream")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"


def test_download_rejects_changed_deleted_and_removed_sources(tmp_path):
    db, docs, folder, document, original = sample(tmp_path)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=db.path.parent,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        client.get("/session")
        url = f"/documents/{document['id']}/original"
        original.write_text("Wohnung: Mietbeginn am Freitag.", encoding="utf-8")
        assert client.get(url).status_code == 409
        assert "Index" in client.get(url).json()["detail"]
        original.unlink()
        assert client.get(url).status_code == 409
        docs.set_excluded(document["id"], True)
        assert client.get(url).status_code == 404
        docs.remove_folder(folder["id"])
        assert client.get(url).status_code == 404
        assert client.get("/documents/unknown/original").status_code == 404


def test_download_rejects_index_change_during_read(tmp_path, monkeypatch):
    _, docs, _, document, _ = sample(tmp_path)
    original = DocumentStore._current_source_bytes

    def change_index(row):
        contents = original(row)
        with docs.db.connect() as connection:
            connection.execute("UPDATE documents SET sha256=? WHERE id=?",
                               ("0" * 64, row["id"]))
        return contents

    monkeypatch.setattr(DocumentStore, "_current_source_bytes", staticmethod(change_index))
    with pytest.raises(ValueError, match="geändert"):
        docs.original_bytes(document["id"])


def test_download_rejects_invalid_saved_size_before_read(tmp_path, monkeypatch):
    db, docs, _, document, _ = sample(tmp_path)
    with db.connect() as connection:
        connection.execute("UPDATE documents SET size=-1 WHERE id=?", (document["id"],))
    monkeypatch.setattr("pathlib.Path.open",
                        lambda *_args, **_kwargs: pytest.fail("Invalid size must not read a file"))
    with pytest.raises(ValueError, match="Index"):
        docs.original_bytes(document["id"])


def test_download_does_not_follow_replaced_symlink(tmp_path):
    _, docs, _, document, original = sample(tmp_path)
    external = tmp_path / "outside.md"
    external.write_text(original.read_text(encoding="utf-8"), encoding="utf-8")
    original.unlink()
    try:
        original.symlink_to(external)
    except OSError:
        pytest.skip("Windows erlaubt hier keine Symlinks")
    with pytest.raises(ValueError, match="Index"):
        docs.original_bytes(document["id"])
