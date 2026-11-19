"""Album ZIP contains only confirmed event data and freshly verified local photos."""

import hashlib
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.memory_events import MemoryEventStore
from jarvis.photo_library import PhotoConflict, PhotoLibrary


def image(path, color="red"):
    Image.new("RGB", (24, 18), color).save(path, format="JPEG")


def test_album_zip_exact_originals_manifest_and_no_absolute_paths(tmp_path):
    folder = tmp_path / "Fotos"
    folder.mkdir()
    image(folder / "reise eins.jpg")
    image(folder / "reise_zwei.jpg", "blue")
    db = Database(tmp_path / "jarvis.db")
    library = PhotoLibrary(db)
    event = MemoryEventStore(db).create("2024-08-12", "Reise", "Mit Alex", "Lyon")
    grant = library.add_folder(str(folder))
    library.index(grant["id"])
    for asset in library.list():
        library.set_event(asset["id"], event["id"], 1)
    with zipfile.ZipFile(io.BytesIO(library.album(event["id"]))) as archive:
        names = archive.namelist()
        assert names[:2] == ["album.md", "manifest.json"]
        assert len(names) == 4 and all(not name.startswith("/") and ".." not in name
                                       for name in names)
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["event"]["title"] == "Reise"
        assert manifest["event"]["date_local"] == "2024-08-12"
        assert manifest["event"]["source_kind"] == "manual"
        assert "Mit Alex" in archive.read("album.md").decode("utf-8")
        for item in manifest["photos"]:
            original = (folder / item["name"]).read_bytes()
            assert archive.read(item["archive_name"]) == original
            assert item["sha256"] == hashlib.sha256(original).hexdigest()
            assert item["size"] == len(original)
        assert str(tmp_path).encode("utf-8") not in archive.read("manifest.json")
        assert str(tmp_path).encode("utf-8") not in archive.read("album.md")


def test_album_rejects_stale_and_missing_before_any_zip(tmp_path):
    folder = tmp_path / "Fotos"
    folder.mkdir()
    first, second = folder / "a.jpg", folder / "b.jpg"
    image(first)
    image(second)
    db = Database(tmp_path / "jarvis.db")
    library = PhotoLibrary(db)
    event = MemoryEventStore(db).create("2024-01-01", "Ausflug")
    grant = library.add_folder(str(folder))
    library.index(grant["id"])
    for asset in library.list():
        library.set_event(asset["id"], event["id"], 1)
    image(second, "green")
    with pytest.raises(PhotoConflict):
        library.album(event["id"])
    second.unlink()
    with pytest.raises(PhotoConflict):
        library.album(event["id"])
    with pytest.raises(LookupError):
        library.album("missing")
    with pytest.raises(ValueError):
        library.album(MemoryEventStore(db).create("2024-01-02", "Leer")["id"])


def test_album_photo_count_limit(tmp_path):
    folder = tmp_path / "Fotos"
    folder.mkdir()
    image(folder / "000.jpg")
    data = (folder / "000.jpg").read_bytes()
    for number in range(1, 11):
        (folder / f"{number:03}.jpg").write_bytes(data)
    db = Database(tmp_path / "jarvis.db")
    library = PhotoLibrary(db)
    event = MemoryEventStore(db).create("2024-01-01", "Viele Fotos")
    grant = library.add_folder(str(folder))
    library.index(grant["id"])
    for asset in library.list():
        library.set_event(asset["id"], event["id"], 1)
    with pytest.raises(ValueError, match="höchstens zehn"):
        library.album(event["id"])


def test_album_total_size_limit(tmp_path, monkeypatch):
    folder = tmp_path / "Fotos"
    folder.mkdir()
    image(folder / "a.jpg")
    image(folder / "b.jpg")
    db = Database(tmp_path / "jarvis.db")
    library = PhotoLibrary(db)
    event = MemoryEventStore(db).create("2024-01-01", "Zwei Fotos")
    grant = library.add_folder(str(folder))
    library.index(grant["id"])
    for asset in library.list():
        library.set_event(asset["id"], event["id"], 1)
    monkeypatch.setattr("jarvis.photo_library.MAX_ALBUM_BYTES", (folder / "a.jpg").stat().st_size)
    with pytest.raises(ValueError, match="64 MiB"):
        library.album(event["id"])


def test_album_api_download_and_stale_response(tmp_path):
    folder = tmp_path / "Fotos"
    folder.mkdir()
    photo = folder / "eins.jpg"
    image(photo)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path / "data",
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        event = client.post("/memory-events", json={"date_local": "2024-01-01",
                            "title": "Moment"}, headers=headers).json()
        grant = client.post("/photo-folder", json={"path": str(folder)}, headers=headers).json()
        client.post(f"/photo-folder/{grant['id']}/index", headers=headers).raise_for_status()
        asset = client.get("/photo-assets").json()[0]
        client.put(f"/photo-assets/{asset['id']}/event", json={"event_id": event["id"],
                   "expected_version": 1}, headers=headers).raise_for_status()
        response = client.get(f"/memory-events/{event['id']}/album")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/zip")
        assert "lebensalbum.zip" in response.headers["content-disposition"]
        assert response.headers["cache-control"] == "no-store"
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert len(archive.namelist()) == 3
        image(photo, "green")
        stale = client.get(f"/memory-events/{event['id']}/album")
        assert stale.status_code == 409 and not stale.content.startswith(b"PK")
        with Database(tmp_path / "data" / "jarvis.db").connect() as connection:
            assert connection.execute("SELECT count(*) FROM model_usage").fetchone()[0] == 0
