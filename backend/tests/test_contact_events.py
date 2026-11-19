"""Contact details show only explicitly linked life events for that person."""

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.contacts import ContactStore
from jarvis.db import Database
from jarvis.memory_events import MemoryEventStore


def test_contact_event_counts_same_names_order_and_independent_deletion(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    contacts = ContactStore(db)
    private = contacts.create("Alex", "Privat")
    work = contacts.create("Alex", "Arbeit")
    events = MemoryEventStore(db)
    older = events.create("2020-06-10", "Wandern", location="Lyon")
    newer = events.create("2024-08-12", "Café", location="Paris")
    unrelated = events.create("2025-01-01", "Büro")
    events.link_contact(older["id"], private["id"], 1)
    events.link_contact(newer["id"], private["id"], 1)
    events.link_contact(unrelated["id"], work["id"], 1)
    listed = {person["id"]: person for person in contacts.list("Alex")}
    assert listed[private["id"]]["event_count"] == 2
    assert listed[work["id"]]["event_count"] == 1
    detail = contacts.get(private["id"])
    assert detail["event_count"] == 2
    assert [item["id"] for item in detail["events"]] == [newer["id"], older["id"]]
    assert detail["events"][0]["location"] == "Paris"
    assert detail["events"][0]["source_kind"] == "manual"
    assert detail["events"][0]["linked_at"]
    assert unrelated["id"] not in {item["id"] for item in detail["events"]}
    events.update(newer["id"], 2, "2024-08-12", "Café am Fluss", location="Paris")
    assert contacts.get(private["id"])["events"][0]["title"] == "Café am Fluss"
    events.delete(older["id"], 2)
    assert contacts.get(private["id"])["event_count"] == 1
    assert contacts.get(work["id"])["event_count"] == 1
    contacts.delete(private["id"], private["version"])
    assert events.get(newer["id"])["contacts"] == []
    assert events.get(unrelated["id"])["contacts"][0]["contact_id"] == work["id"]


def test_contact_event_api_returns_current_linked_events(tmp_path):
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    contacts = ContactStore(db)
    contact = contacts.create("Mira", "Köln")
    event = MemoryEventStore(db).create("2023-09-10", "Konzert")
    MemoryEventStore(db).link_contact(event["id"], contact["id"], 1)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        client.get("/session")
        listed = client.get("/contacts").json()
        assert listed[0]["event_count"] == 1
        detail = client.get(f"/contacts/{contact['id']}").json()
        assert detail["events"][0]["id"] == event["id"]
        assert detail["events"][0]["title"] == "Konzert"
        assert detail["event_count"] == 1
