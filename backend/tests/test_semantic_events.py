"""Confirmed life events can be found locally without exposing stale sources."""

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.contacts import ContactStore
from jarvis.db import Database
from jarvis.memory_events import MemoryEventStore
from jarvis.photo_library import PhotoLibrary
from jarvis.semantic_search import search


def test_semantic_events_filter_context_and_limit(tmp_path, monkeypatch):
    db = Database(tmp_path / "data" / "jarvis.db")
    contacts = ContactStore(db)
    person = contacts.create("Alex", "Lyon privat")
    contacts.create("Alex", "Arbeit")
    events = MemoryEventStore(db)
    first = events.create("2024-08-12", "Café am Fluss", "Gemeinsames Wochenende")
    second = events.create("2024-08-13", "Bürotag", "Projektbesprechung")
    events.link_contact(first["id"], person["id"], first["version"])
    observed = []

    def fake_embed(_url, texts, _cache_path=None):
        observed.extend(texts)
        return [[1.0, 0.0]] + [[1.0, 0.0] if "Lyon privat" in text
                               else [0.0, 1.0] for text in texts[1:]]

    monkeypatch.setattr("jarvis.semantic_search.embed_texts", fake_embed)
    found = search(db, "Wo war ich am Wochenende am Wasser?",
                   "http://127.0.0.1:11434", source_kind="life_event")
    assert found["counts"] == {"document": 0, "memory": 0, "project": 0,
                                "life_event": 2}
    assert found["checked_events"] == 2 and found["checked_passages"] == 0
    assert found["results"][0]["id"] == first["id"]
    assert found["results"][0]["source"] == "2024-08-12 · manuell bestätigt"
    assert "Alex (Lyon privat)" in found["results"][0]["excerpt"]
    assert "Arbeit" not in found["results"][0]["excerpt"]
    assert all("Lyon privat" not in text for text in observed[:1])
    assert all("Alex (Arbeit)" not in text for text in observed)
    assert second["id"] in {row["id"] for row in found["results"]}
    assert not found["limited"]
    combined = search(db, "Wo war ich am Wochenende am Wasser?",
                      "http://127.0.0.1:11434", source_kind="all")
    assert combined["counts"]["life_event"] == 2
    assert combined["results"][0]["id"] == first["id"]
    with pytest.raises(ValueError, match="passen nicht zusammen"):
        search(db, "Wo war ich?", "http://127.0.0.1:11434",
               folder_id="wrong", source_kind="life_event")
    monkeypatch.setattr("jarvis.semantic_search.MAX_EVENTS", 1)
    limited = search(db, "Wo war ich?", "http://127.0.0.1:11434",
                     source_kind="life_event")
    assert limited["checked_events"] == 1 and limited["limited"]


def test_semantic_event_changed_during_embedding_is_hidden(tmp_path, monkeypatch):
    db = Database(tmp_path / "data" / "jarvis.db")
    events = MemoryEventStore(db)
    event = events.create("2024-08-12", "Café am Fluss")
    contact = ContactStore(db).create("Alex", "Privat")
    events.link_contact(event["id"], contact["id"], event["version"])

    def change_contact(_url, texts, _cache_path=None):
        ContactStore(db).update(contact["id"], contact["version"], "Alex", "Arbeit")
        return [[1.0] for _ in texts]

    monkeypatch.setattr("jarvis.semantic_search.embed_texts", change_contact)
    found = search(db, "Alex am Fluss", "http://127.0.0.1:11434",
                   source_kind="life_event")
    assert found["results"] == []
    assert found["counts"]["life_event"] == 0
    assert found["changed_sources"] == [{"kind": "life_event", "id": event["id"]}]
    assert events.get(event["id"])["version"] == 3


def test_semantic_event_api_and_deletion_during_embedding(tmp_path, monkeypatch):
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    events = MemoryEventStore(db)
    event = events.create("2024-08-12", "Café am Fluss")
    monkeypatch.setattr("jarvis.semantic_search.embed_texts",
                        lambda _url, texts, _cache_path=None: [[1.0] for _ in texts])
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        client.get("/session")
        found = client.get("/search/semantic", params={
            "q": "Wo war ich?", "kind": "life_event"})
        assert found.status_code == 200
        assert found.json()["results"][0]["id"] == event["id"]
        assert found.json()["checked_events"] == 1
        assert client.get("/search/semantic", params={
            "q": "Wo war ich?", "kind": "life_event",
            "document_id": "wrong"}).status_code == 422

    def delete_event(_url, texts, _cache_path=None):
        events.delete(event["id"], event["version"])
        return [[1.0] for _ in texts]

    monkeypatch.setattr("jarvis.semantic_search.embed_texts", delete_event)
    found = search(db, "Wo war ich?", "http://127.0.0.1:11434",
                   source_kind="life_event")
    assert found["results"] == []
    assert found["changed_sources"] == [{"kind": "life_event", "id": event["id"]}]


def test_semantic_event_photo_name_without_folder_path_and_stale_link(tmp_path,
                                                                      monkeypatch):
    folder = tmp_path / "PrivateFotos"
    folder.mkdir()
    Image.new("RGB", (32, 24), "red").save(folder / "flussabend.jpg")
    db = Database(tmp_path / "data" / "jarvis.db")
    events = MemoryEventStore(db)
    event = events.create("2024-08-12", "Ausflug")
    photos = PhotoLibrary(db)
    grant = photos.add_folder(str(folder))
    photos.index(grant["id"])
    asset = photos.list()[0]
    photos.set_event(asset["id"], event["id"], asset["version"])
    observed = []

    def detach_photo(_url, texts, _cache_path=None):
        observed.extend(texts)
        current = photos.list()[0]
        photos.set_event(current["id"], None, current["version"])
        return [[1.0] for _ in texts]

    monkeypatch.setattr("jarvis.semantic_search.embed_texts", detach_photo)
    found = search(db, "Wann war der Flussabend?", "http://127.0.0.1:11434",
                   source_kind="life_event")
    assert any("flussabend.jpg" in text for text in observed)
    assert all(str(folder) not in text for text in observed)
    assert found["results"] == []
    assert found["changed_sources"] == [{"kind": "life_event", "id": event["id"]}]
