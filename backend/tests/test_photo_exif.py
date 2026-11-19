"""EXIF capture time is an unconfirmed hint, never a created life event."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.photo_library import PhotoConflict, PhotoLibrary


def make_photo(path: Path, date: str | None = None, tag: int = 36867,
               color: str = "red") -> None:
    image = Image.new("RGB", (32, 24), color)
    if date is None:
        image.save(path, format="JPEG")
    else:
        metadata = Image.Exif()
        metadata[tag] = date
        image.save(path, format="JPEG", exif=metadata)


def test_exif_date_is_local_unconfirmed_and_missing_stays_unknown(tmp_path):
    folder = tmp_path / "Fotos"
    folder.mkdir()
    make_photo(folder / "original.jpg", "2024:08:12 14:30:05")
    make_photo(folder / "digital.jpg", "2023:03:04 09:10:11", 36868)
    make_photo(folder / "fallback.jpg", "2022:12:31 23:59:59", 306)
    make_photo(folder / "invalid.jpg", "2024:02:30 14:30:00")
    make_photo(folder / "unknown.jpg")
    db = Database(tmp_path / "jarvis.db")
    library = PhotoLibrary(db)
    grant = library.add_folder(str(folder))
    assert library.index(grant["id"])["indexed"] == 5
    by_name = {item["name"]: item for item in library.list()}
    assert by_name["original.jpg"]["capture_local"] == "2024-08-12T14:30:05"
    assert by_name["digital.jpg"]["capture_local"] == "2023-03-04T09:10:11"
    assert by_name["fallback.jpg"]["capture_local"] == "2022-12-31T23:59:59"
    assert by_name["invalid.jpg"]["capture_local"] is None
    assert by_name["unknown.jpg"]["capture_local"] is None
    assert library.verified_capture_date(by_name["original.jpg"]["id"]) == {
        "capture_local": "2024-08-12T14:30:05", "source": "exif_unconfirmed"}
    with pytest.raises(ValueError):
        library.verified_capture_date(by_name["unknown.jpg"]["id"])
    assert library.index(grant["id"])["unchanged"] == 5
    with db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM memory_events").fetchone()[0] == 0


def test_exif_hint_is_blocked_when_original_changes(tmp_path):
    folder = tmp_path / "Fotos"
    folder.mkdir()
    photo = folder / "photo.jpg"
    make_photo(photo, "2024:08:12 14:30:05")
    library = PhotoLibrary(Database(tmp_path / "jarvis.db"))
    grant = library.add_folder(str(folder))
    library.index(grant["id"])
    asset = library.list()[0]
    make_photo(photo, "2025:01:01 00:00:00", color="green")
    with pytest.raises(PhotoConflict):
        library.verified_capture_date(asset["id"])
    library.index(grant["id"])
    current = library.list()[0]
    assert current["capture_local"] == "2025-01-01T00:00:00"
    assert current["event_id"] is None


def test_exif_api_backup_and_migration(tmp_path):
    folder = tmp_path / "Fotos"
    folder.mkdir()
    make_photo(folder / "photo.jpg", "2024:08:12 14:30:05")
    root = tmp_path / "data"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        grant = client.post("/photo-folder", json={"path": str(folder)}, headers=headers).json()
        client.post(f"/photo-folder/{grant['id']}/index", headers=headers).raise_for_status()
        asset = client.get("/photo-assets").json()[0]
        response = client.get(f"/photo-assets/{asset['id']}/capture-date")
        assert response.status_code == 200 and response.json()["source"] == "exif_unconfirmed"
        assert response.json()["capture_local"] == "2024-08-12T14:30:05"
        assert client.get("/memory-events").json() == []
        with Database(root / "jarvis.db").connect() as connection:
            assert connection.execute("SELECT count(*) FROM model_usage").fetchone()[0] == 0
    archive = tmp_path / "backup.zip"
    assert create_backup(root, archive)["schema"] == 62
    restored = tmp_path / "restored"
    assert restore_backup(archive, restored)["schema"] == 62
    assert PhotoLibrary(Database(restored / "jarvis.db")).list()[0][
        "capture_local"] == "2024-08-12T14:30:05"

    migrated_db = Database(tmp_path / "old.db")
    with migrated_db.connect() as connection:
        connection.execute("ALTER TABLE photo_assets DROP COLUMN capture_local")
        connection.execute("PRAGMA user_version=61")
    migrated = Database(migrated_db.path)
    with migrated.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 62
        columns = [row[1] for row in connection.execute("PRAGMA table_info(photo_assets)")]
        assert "capture_local" in columns
