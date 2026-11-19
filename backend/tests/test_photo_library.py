"""A selected photo folder never silently changes event meaning or original files."""

import io
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.memory_events import MemoryEventStore
from jarvis.photo_library import PhotoConflict, PhotoLibrary


def make_photo(path: Path, color: str = "red", image_format: str = "JPEG") -> None:
    Image.new("RGB", (28, 22), color).save(path, format=image_format)


def test_photo_folder_index_link_change_and_originals(tmp_path):
    folder = tmp_path / "Fotos"
    folder.mkdir()
    first = folder / "urlaub.jpg"
    second = folder / "see.png"
    make_photo(first)
    make_photo(second, "blue", "PNG")
    (folder / "kaputt.jpg").write_bytes(b"not an image")
    (folder / "notiz.txt").write_text("privat", encoding="utf-8")
    nested = folder / "unterordner"
    nested.mkdir()
    make_photo(nested / "geheim.jpg")
    db = Database(tmp_path / "jarvis.db")
    library = PhotoLibrary(db)
    event = MemoryEventStore(db).create("2024-08-12", "Urlaub am See")
    grant = library.add_folder(str(folder))
    assert library.add_folder(str(folder))["id"] == grant["id"]
    with pytest.raises(ValueError):
        library.add_folder(str(tmp_path))
    counts = library.index(grant["id"])
    assert counts == {"indexed": 2, "unchanged": 0, "skipped": 1, "limited": False}
    assets = library.list()
    assert {item["name"] for item in assets} == {"urlaub.jpg", "see.png"}
    assert all(item["status"] == "current" and "folder_path" not in item for item in assets)
    chosen = next(item for item in assets if item["name"] == "urlaub.jpg")
    assert Image.open(io.BytesIO(library.preview(chosen["id"]))).format == "JPEG"
    linked = library.set_event(chosen["id"], event["id"], chosen["version"])
    assert linked["event_id"] == event["id"] and linked["version"] == 2
    with pytest.raises(PhotoConflict):
        library.set_event(chosen["id"], None, chosen["version"])
    make_photo(first, "green")
    with pytest.raises(PhotoConflict):
        library.preview(chosen["id"])
    with pytest.raises(PhotoConflict):
        library.set_event(chosen["id"], event["id"], linked["version"])
    changed = next(item for item in library.list() if item["id"] == chosen["id"])
    assert changed["status"] == "changed"
    refreshed = library.index(grant["id"])
    assert refreshed["indexed"] == 1 and refreshed["unchanged"] == 1
    unlinked = next(item for item in library.list() if item["id"] == chosen["id"])
    assert unlinked["event_id"] is None and unlinked["version"] == 3
    assert library.set_event(chosen["id"], event["id"], unlinked["version"])["event_id"] == event["id"]
    first_bytes, second_bytes = first.read_bytes(), second.read_bytes()
    library.remove_folder(grant["id"])
    assert library.list() == [] and library.folder() is None
    assert first.read_bytes() == first_bytes and second.read_bytes() == second_bytes


def test_photo_source_disappears_and_event_deleted(tmp_path):
    folder = tmp_path / "Bilder"
    folder.mkdir()
    photo = folder / "eins.webp"
    make_photo(photo, image_format="WEBP")
    db = Database(tmp_path / "jarvis.db")
    library = PhotoLibrary(db)
    event = MemoryEventStore(db).create("2024-01-01", "Moment")
    grant = library.add_folder(str(folder))
    library.index(grant["id"])
    asset = library.list()[0]
    library.set_event(asset["id"], event["id"], 1)
    photo.unlink()
    assert library.list()[0]["status"] == "unavailable"
    with pytest.raises(PhotoConflict):
        library.preview(asset["id"])
    with pytest.raises(PhotoConflict):
        library.set_event(asset["id"], event["id"], 2)
    MemoryEventStore(db).delete(event["id"], 1)
    assert library.list()[0]["event_id"] is None
    assert library.set_event(asset["id"], None, 2)["event_id"] is None


def test_photo_hash_detects_changed_bytes_with_preserved_timestamp(tmp_path):
    folder = tmp_path / "Bilder"
    folder.mkdir()
    photo = folder / "bild.png"
    make_photo(photo, "red", "PNG")
    library = PhotoLibrary(Database(tmp_path / "jarvis.db"))
    grant = library.add_folder(str(folder))
    library.index(grant["id"])
    asset = library.list()[0]
    original = photo.stat()
    replacement = bytearray(photo.read_bytes())
    replacement[-20] ^= 1
    photo.write_bytes(replacement)
    os.utime(photo, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert photo.stat().st_size == original.st_size
    assert library.list()[0]["status"] == "current"  # listing uses cheap metadata
    with pytest.raises(PhotoConflict):
        library.preview(asset["id"])


def test_photo_index_stops_at_300_assets(tmp_path):
    folder = tmp_path / "Bilder"
    folder.mkdir()
    sample = folder / "000.jpg"
    make_photo(sample)
    contents = sample.read_bytes()
    for number in range(1, 302):
        (folder / f"{number:03}.jpg").write_bytes(contents)
    library = PhotoLibrary(Database(tmp_path / "jarvis.db"))
    grant = library.add_folder(str(folder))
    result = library.index(grant["id"])
    assert result["limited"] and result["indexed"] == 300
    assert len(library.list()) == 300


def test_photo_api_session_and_backup(tmp_path):
    folder = tmp_path / "Fotos"
    folder.mkdir()
    make_photo(folder / "eins.jpg")
    root = tmp_path / "data"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        assert client.post("/photo-folder", json={"path": str(folder)}).status_code == 403
        grant_response = client.post("/photo-folder", json={"path": str(folder)}, headers=headers)
        assert grant_response.status_code == 201, grant_response.text
        grant = grant_response.json()
        assert client.post(f"/photo-folder/{grant['id']}/index", headers=headers).json()["indexed"] == 1
        asset = client.get("/photo-assets").json()[0]
        assert client.get(f"/photo-assets/{asset['id']}/preview").headers["content-type"].startswith("image/jpeg")
        event = client.post("/memory-events", json={"date_local": "2024-01-01",
                            "title": "Erstes Foto"}, headers=headers).json()
        linked = client.put(f"/photo-assets/{asset['id']}/event",
                            json={"event_id": event["id"], "expected_version": 1},
                            headers=headers)
        assert linked.status_code == 200 and linked.json()["event_id"] == event["id"]
        assert client.put(f"/photo-assets/{asset['id']}/event",
                          json={"event_id": None, "expected_version": 1},
                          headers=headers).status_code == 409
        assert client.put(f"/photo-assets/{asset['id']}/event",
                          json={"event_id": "missing", "expected_version": 2},
                          headers=headers).status_code == 422
        with Database(root / "jarvis.db").connect() as connection:
            assert connection.execute("SELECT count(*) FROM model_usage").fetchone()[0] == 0
    archive = tmp_path / "photos.zip"
    assert create_backup(root, archive)["schema"] == 61
    restored = tmp_path / "restored"
    assert restore_backup(archive, restored)["schema"] == 61
    restored_library = PhotoLibrary(Database(restored / "jarvis.db"))
    assert restored_library.list()[0]["event_id"] == event["id"]
    assert restored_library.preview(asset["id"]).startswith(bytes([0xff, 0xd8]))


def test_photo_migration_from_59(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    with db.connect() as connection:
        connection.execute("DROP TABLE photo_assets")
        connection.execute("DROP TABLE photo_folders")
        connection.execute("PRAGMA user_version=59")
    migrated = Database(db.path)
    with migrated.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 61
        assert connection.execute("SELECT count(*) FROM photo_folders").fetchone()[0] == 0


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink needs developer privileges")
def test_photo_symlink_not_indexed(tmp_path):
    outside = tmp_path / "outside.jpg"
    make_photo(outside)
    folder = tmp_path / "Fotos"
    folder.mkdir()
    (folder / "link.jpg").symlink_to(outside)
    library = PhotoLibrary(Database(tmp_path / "jarvis.db"))
    grant = library.add_folder(str(folder))
    assert library.index(grant["id"])["skipped"] == 1
    assert library.list() == []
