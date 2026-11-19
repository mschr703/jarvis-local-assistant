"""Bounded keyword search finds only explicitly saved contact information."""

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.contacts import ContactStore
from jarvis.db import Database
from jarvis.memory_events import MemoryEventStore
from jarvis.unified_search import search


def test_contact_keyword_search_sources_and_same_name_identity(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    contacts = ContactStore(db)
    private = contacts.create("Alex", "München privat", "Freund")
    work = contacts.create("Alex", "Arbeit", "Kollege")
    note = contacts.add_note(private["id"], "Mag Kastanienkuchen")
    contacts.add_plan(private["id"], "Paddeln im Herbst", "Bei schönem Wetter")
    events = MemoryEventStore(db)
    event = events.create("2024-08-12", "Café in Zürich", location="Schweiz")
    events.link_contact(event["id"], private["id"], 1)

    by_context = search(db, "München", "contact")
    assert by_context["counts"]["contact"] == 1
    assert by_context["results"][0]["id"] == private["id"]
    assert by_context["results"][0]["source"] == "Kontakt · manuell bestätigt"
    assert search(db, "Arbeit", "contact")["results"][0]["id"] == work["id"]
    assert search(db, "Kastanienkuchen", "contact")["results"][0]["id"] == private["id"]
    assert search(db, "Paddeln", "contact")["results"][0]["id"] == private["id"]
    by_event = search(db, "Zurich", "contact")
    assert by_event["results"][0]["id"] == private["id"]
    assert "Café in Zürich" in by_event["results"][0]["excerpt"]
    assert search(db, "Zürich", "all")["counts"]["contact"] == 1
    assert search(db, "Kastanienkuchen", "life_event")["counts"]["contact"] == 0

    contacts.update_note(private["id"], note["id"], note["version"], "Mag Mohnkuchen")
    assert search(db, "Kastanienkuchen", "contact")["results"] == []
    assert search(db, "Mohnkuchen", "contact")["results"][0]["id"] == private["id"]
    events.unlink_contact(event["id"], private["id"], 2)
    assert search(db, "Zurich", "contact")["results"] == []
    contacts.delete(private["id"], contacts.get(private["id"])["version"])
    assert search(db, "Mohnkuchen", "contact")["results"] == []


def test_contact_keyword_search_bounds_and_read_only_api(tmp_path, monkeypatch):
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    contacts = ContactStore(db)
    person = contacts.create("Mira", "Köln")
    contacts.add_note(person["id"], "Älteres Detail: Wintergarten")
    contacts.add_note(person["id"], "Neueres Detail: Sommerfest")
    monkeypatch.setattr("jarvis.unified_search.MAX_CONTACT_DETAILS", 1)
    bounded = search(db, "Wintergarten", "contact")
    assert bounded["results"] == [] and bounded["limited"]
    assert search(db, "Sommerfest", "contact")["results"][0]["id"] == person["id"]
    other = contacts.create("Lina", "Bonn")
    with db.connect() as connection:
        connection.execute("UPDATE contacts SET updated_at='2020-01-01T00:00:00Z' WHERE id=?",
                           (person["id"],))
        connection.execute("UPDATE contacts SET updated_at='2030-01-01T00:00:00Z' WHERE id=?",
                           (other["id"],))
    monkeypatch.setattr("jarvis.unified_search.MAX_CONTACT_ROWS", 1)
    assert search(db, "Mira", "contact")["results"] == []
    assert search(db, "Mira", "contact")["limited"]
    monkeypatch.setattr("jarvis.unified_search.MAX_CONTACT_ROWS", 2000)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        assert client.get("/search/unified", params={"q": "Sommerfest",
                                                  "kind": "contact"}).status_code == 401
        client.get("/session")
        response = client.get("/search/unified", params={"q": "Sommerfest",
                                                          "kind": "contact"})
        assert response.status_code == 200
        assert response.json()["results"][0]["id"] == person["id"]
        assert response.json()["counts"]["contact"] == 1
        with db.connect() as connection:
            assert connection.execute("SELECT count(*) FROM model_usage").fetchone()[0] == 0
