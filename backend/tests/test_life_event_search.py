"""Confirmed life events participate only in bounded local keyword search."""

from fastapi.testclient import TestClient
from PIL import Image

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.memory_events import MemoryEventStore
from jarvis.photo_library import PhotoLibrary
from jarvis.unified_search import search


def test_life_event_search_by_text_date_and_photo_name(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    events = MemoryEventStore(db)
    first = events.create("2024-08-12", "Café in Lyon", "Mit Alex am Fluss", "Lyon")
    second = events.create("2022-03-04", "Winterabend", "Mit Mira", "Zürich")
    folder = tmp_path / "Fotos"
    folder.mkdir()
    Image.new("RGB", (20, 20), "red").save(folder / "weekend.jpg")
    photos = PhotoLibrary(db)
    grant = photos.add_folder(str(folder))
    photos.index(grant["id"])
    asset = photos.list()[0]
    photos.set_event(asset["id"], first["id"], 1)

    by_title = search(db, "cafe", "life_event")
    assert by_title["counts"] == {"document": 0, "memory": 0, "project": 0,
                                  "life_event": 1}
    assert by_title["results"][0]["id"] == first["id"]
    assert by_title["results"][0]["source"] == "2024-08-12 · manuell bestätigt"
    assert by_title["preferences"] == [] and by_title["stale_sources"] == []
    by_photo = search(db, "weekend", "life_event")
    assert by_photo["results"][0]["id"] == first["id"]
    assert "weekend.jpg" in by_photo["results"][0]["excerpt"]
    assert str(folder) not in str(by_photo)
    assert search(db, "2022", "life_event")["results"][0]["id"] == second["id"]
    assert search(db, "Lyon", "all")["counts"]["life_event"] == 1
    changed = events.update(first["id"], 1, "2024-08-12", "Reise nach Lyon",
                            "Mit Alex am Fluss", "Lyon")
    assert changed["version"] == 2
    assert search(db, "cafe", "life_event")["results"] == []
    events.delete(first["id"], 2)
    assert search(db, "weekend", "life_event")["results"] == []


def test_life_event_search_limit_and_api_no_model(tmp_path, monkeypatch):
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    events = MemoryEventStore(db)
    events.create("2024-01-01", "Ausflug Berlin")
    events.create("2024-01-02", "Ausflug München")
    monkeypatch.setattr("jarvis.unified_search.MAX_PER_KIND", 1)
    result = search(db, "Ausflug", "life_event")
    assert result["counts"]["life_event"] == 1 and result["limited"]
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        assert client.get("/search/unified", params={"q": "Ausflug",
                                                     "kind": "life_event"}).status_code == 401
        client.get("/session")
        response = client.get("/search/unified", params={"q": "Ausflug",
                                                        "kind": "life_event"})
        assert response.status_code == 200 and response.json()["counts"]["life_event"] == 1
        assert client.get("/search/unified", params={"q": "Ausflug",
                                                     "kind": "bad"}).status_code == 422
        with db.connect() as connection:
            assert connection.execute("SELECT count(*) FROM model_usage").fetchone()[0] == 0
