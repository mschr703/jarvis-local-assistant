"""Personal contacts stay distinct, editable, searchable, and backup safe."""

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.contacts import ContactConflict, ContactStore
from jarvis.db import Database


def test_two_people_same_name_notes_search_and_backup(tmp_path):
    root = tmp_path / "data"
    contacts = ContactStore(Database(root / "jarvis.db"))
    friend = contacts.create("Alex", "München", "Freund")
    colleague = contacts.create("Alex", "Arbeit", "Kollege")
    assert friend["id"] != colleague["id"]
    with pytest.raises(ContactConflict):
        contacts.create("alex", "mÜnchen", "Bekannter")
    note = contacts.add_note(friend["id"], "Wir wollen ins Museum.")
    assert note["source_kind"] == "manual" and note["version"] == 1
    assert [item["id"] for item in contacts.list("Museum")] == [friend["id"]]
    assert [item["id"] for item in contacts.list("Arbeit")] == [colleague["id"]]
    assert contacts.get(colleague["id"])["notes"] == []

    archive = tmp_path / "backup.zip"
    assert create_backup(root, archive)["schema"] == 55
    restored_root = tmp_path / "restored"
    assert restore_backup(archive, restored_root)["schema"] == 55
    restored = ContactStore(Database(restored_root / "jarvis.db"))
    assert restored.get(friend["id"])["notes"][0]["content"] == "Wir wollen ins Museum."
    assert restored.get(colleague["id"])["notes"] == []
    with restored.db.connect() as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_versions_corrections_and_cascade_delete(tmp_path):
    store = ContactStore(Database(tmp_path / "jarvis.db"))
    person = store.create("Mira", "Verein")
    note = store.add_note(person["id"], "Mag Wandern")
    with pytest.raises(ContactConflict):
        store.delete(person["id"], 1)
    revised = store.update_note(person["id"], note["id"], 1, "Mag Radfahren")
    assert revised["version"] == 2 and revised["content"] == "Mag Radfahren"
    with pytest.raises(ContactConflict):
        store.update_note(person["id"], note["id"], 1, "Alter Stand")
    with pytest.raises(ContactConflict):
        store.delete(person["id"], 2)
    current = store.update(person["id"], 3, "Mira", "Sportverein", "Freundin")
    assert current["version"] == 4
    with pytest.raises(ContactConflict):
        store.update(person["id"], 1, "Mira", "Alt")
    store.delete(person["id"], 4)
    with store.db.connect() as db:
        assert db.execute("SELECT count(*) FROM contact_notes").fetchone()[0] == 0
    with pytest.raises(LookupError):
        store.get(person["id"])


def test_contact_api_session_validation_and_conflicts(tmp_path):
    settings = Settings(JARVIS_DATA_DIR=tmp_path / "data", JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        assert client.post("/contacts", json={"name": "A", "context": "B"}).status_code == 401
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        created = client.post("/contacts", json={"name": "  Alex ", "context": " Privat ",
                                                 "relationship": "Freund"}, headers=headers)
        assert created.status_code == 201
        person = created.json()
        assert (person["name"], person["context"]) == ("Alex", "Privat")
        assert client.post("/contacts", json={"name": "alex", "context": "privat"},
                           headers=headers).status_code == 409
        assert client.post("/contacts", json={"name": " ", "context": "Privat"},
                           headers=headers).status_code == 422
        note = client.post(f"/contacts/{person['id']}/notes", json={"content": "  Museum "},
                           headers=headers)
        assert note.status_code == 201 and note.json()["content"] == "Museum"
        note_id = note.json()["id"]
        assert len(client.get("/contacts?q=Museum").json()) == 1
        updated = client.put(f"/contacts/{person['id']}/notes/{note_id}",
                             json={"content": "Kino", "expected_version": 1}, headers=headers)
        assert updated.status_code == 200 and updated.json()["version"] == 2
        assert client.put(f"/contacts/{person['id']}/notes/{note_id}",
                          json={"content": "Alt", "expected_version": 1},
                          headers=headers).status_code == 409
        assert client.delete(f"/contacts/{person['id']}/notes/{note_id}?expected_version=2",
                             headers=headers).status_code == 200
        current_version = client.get(f"/contacts/{person['id']}").json()["version"]
        assert current_version == 4
        assert client.delete(f"/contacts/{person['id']}?expected_version=1",
                             headers=headers).status_code == 409
        assert client.delete(f"/contacts/{person['id']}?expected_version={current_version}",
                             headers=headers).status_code == 200
        assert client.get(f"/contacts/{person['id']}").status_code == 404
        assert client.get("/contacts").json() == []


def test_schema_54_migrates_without_dropping_existing_data(tmp_path):
    path = tmp_path / "jarvis.db"
    db = Database(path)
    with db.connect() as connection:
        connection.execute("INSERT INTO activity(id,kind,detail,created_at) "
                           "VALUES('old','test','kept','2026-01-01')")
        connection.execute("DROP TABLE contact_notes")
        connection.execute("DROP TABLE contacts")
        connection.execute("PRAGMA user_version=54")
    migrated = Database(path)
    with migrated.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 55
        assert connection.execute("SELECT detail FROM activity WHERE id='old'").fetchone()[0] == "kept"
    assert ContactStore(migrated).create("Alt", "Migration")["name"] == "Alt"
