"""Photo registries can move after restore only when every original matches."""

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


def image(path: Path, color: str = "red") -> None:
    Image.new("RGB", (24, 18), color).save(path, format="JPEG")


def test_rebind_after_backup_preserves_verified_links(tmp_path):
    old = tmp_path / "old-photos"
    old.mkdir()
    image(old / "eins.jpg")
    image(old / "zwei.jpg", "blue")
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    library = PhotoLibrary(db)
    event = MemoryEventStore(db).create("2024-01-01", "Reise")
    folder = library.add_folder(str(old))
    assert folder["version"] == 1
    library.index(folder["id"])
    first = library.list()[0]
    library.set_event(first["id"], event["id"], first["version"])
    archive = tmp_path / "backup.zip"
    assert create_backup(root, archive)["schema"] == 62
    new = tmp_path / "new-photos"
    old.rename(new)
    restored_root = tmp_path / "restored"
    assert restore_backup(archive, restored_root)["schema"] == 62
    restored = PhotoLibrary(Database(restored_root / "jarvis.db"))
    assert restored.list()[0]["status"] == "unavailable"
    with pytest.raises(PhotoConflict):
        restored.preview(first["id"])
    with pytest.raises(PhotoConflict):
        restored.rebind_folder(folder["id"], str(new), 2)
    image(new / "zwei.jpg", "green")
    with pytest.raises(PhotoConflict):
        restored.rebind_folder(folder["id"], str(new), 1)
    assert restored.folder()["path"] == str(old) and restored.folder()["version"] == 1
    image(new / "zwei.jpg", "blue")
    moved = restored.rebind_folder(folder["id"], str(new), 1)
    assert moved["path"] == str(new) and moved["version"] == 2
    assert restored.list()[0]["status"] == "current"
    assert restored.list()[0]["event_id"] == event["id"]
    assert restored.preview(first["id"]).startswith(bytes([0xff, 0xd8]))
    assert restored.album(event["id"]).startswith(b"PK")
    with pytest.raises(PhotoConflict):
        restored.rebind_folder(folder["id"], str(new), 1)


def test_rebind_missing_file_is_atomic_and_same_path_rejected(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    image(old / "a.jpg")
    image(old / "b.jpg")
    new = tmp_path / "new"
    new.mkdir()
    image(new / "a.jpg")
    library = PhotoLibrary(Database(tmp_path / "jarvis.db"))
    folder = library.add_folder(str(old))
    library.index(folder["id"])
    with pytest.raises(ValueError):
        library.rebind_folder(folder["id"], str(old), 1)
    with pytest.raises(PhotoConflict):
        library.rebind_folder(folder["id"], str(new), 1)
    assert library.folder()["path"] == str(old)
    assert all(item["status"] == "current" for item in library.list())
    assert (old / "a.jpg").exists() and (old / "b.jpg").exists()


def test_rebind_api_csrf_conflict_and_success(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    image(old / "a.jpg")
    new = tmp_path / "new"
    new.mkdir()
    image(new / "a.jpg")
    settings = Settings(JARVIS_DATA_DIR=tmp_path / "data", JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        folder = client.post("/photo-folder", json={"path": str(old)}, headers=headers).json()
        client.post(f"/photo-folder/{folder['id']}/index", headers=headers).raise_for_status()
        route = f"/photo-folder/{folder['id']}/path"
        payload = {"path": str(new), "expected_version": 1}
        assert client.put(route, json=payload).status_code == 403
        assert client.put(route, json={**payload, "expected_version": 2},
                          headers=headers).status_code == 409
        moved = client.put(route, json=payload, headers=headers)
        assert moved.status_code == 200 and moved.json()["version"] == 2
        assert client.get("/photo-assets").json()[0]["status"] == "current"
        with Database(tmp_path / "data" / "jarvis.db").connect() as db:
            assert db.execute("SELECT count(*) FROM model_usage").fetchone()[0] == 0


def test_rebind_migration_from_60(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    with db.connect() as connection:
        connection.execute("ALTER TABLE photo_folders DROP COLUMN version")
        connection.execute("PRAGMA user_version=60")
    migrated = Database(db.path)
    with migrated.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 62
        columns = [row[1] for row in connection.execute("PRAGMA table_info(photo_folders)")]
        assert "version" in columns
