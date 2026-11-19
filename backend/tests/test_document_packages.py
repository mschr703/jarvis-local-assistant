"""Document packages contain only user-selected verified originals."""

import hashlib
import io
import json
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.document_packages import build_document_package
from jarvis.documents import DocumentStore


def sample(tmp_path):
    root = tmp_path / "allowed"
    first_dir, second_dir = root / "eins", root / "zwei"
    first_dir.mkdir(parents=True)
    second_dir.mkdir()
    first, second = first_dir / "Beleg.md", second_dir / "Beleg.md"
    first.write_text("Erster privater Beleg.", encoding="utf-8")
    second.write_text("Zweiter privater Beleg.", encoding="utf-8")
    db = Database(tmp_path / "data" / "jarvis.db")
    docs = DocumentStore(db)
    folder = docs.add_folder(str(root), "local_only")
    assert docs.index_folder(folder["id"])["indexed"] == 2
    indexed = {item["path"]: item for item in docs.documents()}
    return db, docs, indexed, first, second


def test_package_download_has_exact_originals_and_no_local_paths(tmp_path):
    db, _, indexed, first, second = sample(tmp_path)
    ids = [indexed[str(second)]["id"], indexed[str(first)]["id"]]
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=db.path.parent,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        token = client.get("/session").json()["csrf"]
        assert client.post("/documents/package", json={"document_ids": ids}).status_code == 403
        response = client.post("/documents/package", json={"document_ids": ids},
                               headers={"x-jarvis-csrf": token})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/zip")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "jarvis-unterlagenpaket.zip" in response.headers["content-disposition"]
        with ZipFile(io.BytesIO(response.content)) as archive:
            assert archive.namelist() == ["01_Beleg.md", "02_Beleg.md", "manifest.json"]
            assert archive.read("01_Beleg.md") == second.read_bytes()
            assert archive.read("02_Beleg.md") == first.read_bytes()
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["format_version"] == 1
            assert [entry["document_id"] for entry in manifest["files"]] == ids
            assert [entry["archive_name"] for entry in manifest["files"]] == archive.namelist()[:2]
            assert [entry["sha256"] for entry in manifest["files"]] == [
                indexed[str(second)]["sha256"], indexed[str(first)]["sha256"]]
            assert [hashlib.sha256(archive.read(entry["archive_name"])).hexdigest()
                    for entry in manifest["files"]] == [entry["sha256"]
                                                       for entry in manifest["files"]]
            assert str(tmp_path) not in archive.read("manifest.json").decode("utf-8")
        with db.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0


def test_package_rejects_stale_excluded_duplicate_and_missing_document(tmp_path):
    db, docs, indexed, first, second = sample(tmp_path)
    ids = [indexed[str(first)]["id"], indexed[str(second)]["id"]]
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=db.path.parent,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        token = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": token}
        first.write_text("Inzwischen anders.", encoding="utf-8")
        response = client.post("/documents/package", json={"document_ids": ids}, headers=headers)
        assert response.status_code == 409
        assert "Index" in response.json()["detail"]
        assert client.post("/documents/package", json={"document_ids": ids * 2},
                           headers=headers).status_code == 409
        assert client.post("/documents/package", json={"document_ids": []},
                           headers=headers).status_code == 422
        assert client.post("/documents/package", json={"document_ids": ids * 6},
                           headers=headers).status_code == 422
        assert client.post("/documents/package", json={"document_ids": ["missing"]},
                           headers=headers).status_code == 404
        docs.set_excluded(ids[1], True)
        assert client.post("/documents/package", json={"document_ids": [ids[1]]},
                           headers=headers).status_code == 404


def test_package_total_size_limit_prevents_zip_creation(tmp_path, monkeypatch):
    _, docs, indexed, first, second = sample(tmp_path)
    monkeypatch.setattr("jarvis.document_packages.MAX_PACKAGE_BYTES", 30)
    with pytest.raises(ValueError, match="32 MiB"):
        build_document_package(docs, [indexed[str(first)]["id"], indexed[str(second)]["id"]])


def test_package_strips_source_path_and_unsafe_archive_characters():
    class FakeDocuments:
        def original_bytes(self, _document_id):
            return {"path": r"C:\private\folder\evil?.txt",
                    "sha256": hashlib.sha256(b"x").hexdigest()}, b"x"

    with ZipFile(io.BytesIO(build_document_package(FakeDocuments(), ["one"]))) as archive:
        assert archive.namelist() == ["01_evil_.txt", "manifest.json"]
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["files"][0]["filename"] == "evil?.txt"
        assert "private" not in archive.read("manifest.json").decode("utf-8")
