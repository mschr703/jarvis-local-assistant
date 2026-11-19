"""Manual person links keep same-name contacts distinct and never infer people."""

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.contacts import ContactStore
from jarvis.db import Database
from jarvis.memory_events import MemoryEventConflict, MemoryEventStore
from jarvis.unified_search import search


def test_manual_event_contacts_same_name_search_and_unlink(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    contacts = ContactStore(db)
    private = contacts.create("Alex", "München", "Freund")
    work = contacts.create("Alex", "Arbeit", "Kollege")
    events = MemoryEventStore(db)
    event = events.create("2024-08-12", "Museumsbesuch")
    linked = events.link_contact(event["id"], private["id"], 1)
    assert linked["version"] == 2
    assert linked["contacts"][0]["name"] == "Alex"
    assert linked["contacts"][0]["context"] == "München"
    assert linked["contacts"][0]["source_kind"] == "manual"
    assert linked["contacts"][0]["contact_id"] == private["id"]
    assert events.link_contact(event["id"], private["id"], 2)["version"] == 2
    assert events.list("München")[0]["id"] == event["id"]
    assert events.list("Arbeit") == []
    assert search(db, "München", "life_event")["results"][0]["id"] == event["id"]
    assert search(db, "Arbeit", "life_event")["results"] == []
    assert "Alex (München)" in events.export_markdown().decode("utf-8")
    try:
        events.link_contact(event["id"], work["id"], 1)
    except MemoryEventConflict:
        pass
    else:
        raise AssertionError("stale version accepted")
    linked_both = events.link_contact(event["id"], work["id"], 2)
    assert {person["contact_id"] for person in linked_both["contacts"]} == {
        private["id"], work["id"]}
    unlinked = events.unlink_contact(event["id"], private["id"], 3)
    assert unlinked["version"] == 4
    assert [person["contact_id"] for person in unlinked["contacts"]] == [work["id"]]
    assert events.list("München") == []
    contacts.delete(work["id"], work["version"])
    remaining = events.get(event["id"])
    assert remaining["version"] == 5 and remaining["contacts"] == []


def test_contact_list_search_follows_confirmed_event_changes(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    contacts = ContactStore(db)
    private = contacts.create("Alex", "Privat")
    work = contacts.create("Alex", "Arbeit")
    events = MemoryEventStore(db)
    event = events.create("2024-08-12", "Café in Zürich", "Am Fluss", "Schweiz")
    assert contacts.list("cafe") == []
    linked = events.link_contact(event["id"], private["id"], 1)
    for query in ("cafe", "Zurich", "Schweiz", "Fluss", "2024-08-12"):
        assert [person["id"] for person in contacts.list(query)] == [private["id"]]
    assert [person["id"] for person in contacts.list("Arbeit")] == [work["id"]]
    changed = events.update(event["id"], linked["version"], "2024-08-12",
                            "Galerie in Zürich", "Am Fluss", "Schweiz")
    assert contacts.list("cafe") == []
    assert [person["id"] for person in contacts.list("Galerie")] == [private["id"]]
    events.unlink_contact(event["id"], private["id"], changed["version"])
    assert contacts.list("Galerie") == []


def test_event_contact_api_and_backup(tmp_path):
    root = tmp_path / "data"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        contact = client.post("/contacts", json={"name": "Mira", "context": "Köln"},
                              headers=headers).json()
        event = client.post("/memory-events", json={"date_local": "2024-05-01",
                            "title": "Wanderung"}, headers=headers).json()
        route = f"/memory-events/{event['id']}/contacts/{contact['id']}"
        assert client.put(route, json={"expected_version": 1}).status_code == 403
        linked = client.put(route, json={"expected_version": 1}, headers=headers)
        assert linked.status_code == 200 and linked.json()["version"] == 2
        assert client.put(route, json={"expected_version": 1},
                          headers=headers).status_code == 409
        assert client.put(f"/memory-events/{event['id']}/contacts/missing",
                          json={"expected_version": 2}, headers=headers).status_code == 404
        assert client.get("/memory-events?q=Köln").json()[0]["id"] == event["id"]
        assert client.get("/search/unified", params={"q": "Köln", "kind": "life_event"}
                          ).json()["results"][0]["id"] == event["id"]
        with Database(root / "jarvis.db").connect() as connection:
            assert connection.execute("SELECT count(*) FROM model_usage").fetchone()[0] == 0
    archive = tmp_path / "backup.zip"
    assert create_backup(root, archive)["schema"] == 64
    restored = tmp_path / "restored"
    assert restore_backup(archive, restored)["schema"] == 64
    assert MemoryEventStore(Database(restored / "jarvis.db")).get(event["id"])[
        "contacts"][0]["contact_id"] == contact["id"]
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=restored,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        assert client.delete(route, params={"expected_version": 1},
                             headers=headers).status_code == 409
        removed = client.delete(route, params={"expected_version": 2}, headers=headers)
        assert removed.status_code == 200 and removed.json()["contacts"] == []
        assert client.get(f"/memory-events/{event['id']}").status_code == 200
        assert client.get(f"/contacts/{contact['id']}").status_code == 200


def test_event_contact_migration_from_62(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    with db.connect() as connection:
        connection.execute("DROP TABLE memory_event_contacts")
        connection.execute("PRAGMA user_version=62")
    migrated = Database(db.path)
    with migrated.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 64
        assert connection.execute("SELECT count(*) FROM memory_event_contacts").fetchone()[0] == 0


def test_event_contact_limit_preserves_existing_links(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    contacts = ContactStore(db)
    events = MemoryEventStore(db)
    event = events.create("2024-08-12", "Feier")
    version = event["version"]
    for number in range(10):
        contact = contacts.create(f"Person {number}", "Freunde")
        version = events.link_contact(event["id"], contact["id"], version)["version"]
    extra = contacts.create("Person 10", "Freunde")
    with pytest.raises(ValueError, match="Höchstens zehn"):
        events.link_contact(event["id"], extra["id"], version)
    assert len(events.get(event["id"])["contacts"]) == 10
    assert events.get(event["id"])["version"] == version
