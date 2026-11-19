"""Personal contacts stay distinct, editable, searchable, and backup safe."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.contacts import ContactConflict, ContactStore
from jarvis.db import Database
from jarvis.tasks import TaskStore


def future_due(days: int = 3) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).astimezone(
        ZoneInfo("Europe/Paris")).strftime("%Y-%m-%dT%H:%M")


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
    assert create_backup(root, archive)["schema"] == 62
    restored_root = tmp_path / "restored"
    assert restore_backup(archive, restored_root)["schema"] == 62
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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 62
        assert connection.execute("SELECT detail FROM activity WHERE id='old'").fetchone()[0] == "kept"
    assert ContactStore(migrated).create("Alt", "Migration")["name"] == "Alt"


def test_shared_plans_search_versions_backup_and_cascade(tmp_path):
    root = tmp_path / "data"
    store = ContactStore(Database(root / "jarvis.db"))
    alex = store.create("Alex", "München")
    other = store.create("Alex", "Arbeit")
    plan = store.add_plan(alex["id"], "Museum besuchen", "Im Dezember")
    assert plan["status"] == "open" and plan["version"] == 1
    assert plan["source_kind"] == "manual"
    assert [item["id"] for item in store.list("Dezember")] == [alex["id"]]
    assert store.get(other["id"])["plans"] == []
    with pytest.raises(ContactConflict):
        store.delete(alex["id"], alex["version"])
    revised = store.update_plan(alex["id"], plan["id"], 1,
                                "Kunstmuseum", "Samstag", "completed")
    assert revised["status"] == "completed" and revised["version"] == 2
    assert store.list("Museum")[0]["id"] == alex["id"]
    with pytest.raises(ContactConflict):
        store.update_plan(alex["id"], plan["id"], 1, "Alter Titel", "", "open")
    with pytest.raises(ValueError):
        store.update_plan(alex["id"], plan["id"], 2, "X", "", "maybe")

    archive = tmp_path / "backup.zip"
    assert create_backup(root, archive)["schema"] == 62
    restored_root = tmp_path / "restored"
    assert restore_backup(archive, restored_root)["schema"] == 62
    restored = ContactStore(Database(restored_root / "jarvis.db"))
    assert restored.get(alex["id"])["plans"][0]["title"] == "Kunstmuseum"
    restored.delete_plan(alex["id"], plan["id"], 2)
    assert restored.get(alex["id"])["plans"] == []
    another = restored.add_plan(alex["id"], "Kino")
    current_version = restored.get(alex["id"])["version"]
    restored.delete(alex["id"], current_version)
    with restored.db.connect() as db:
        assert db.execute("SELECT count(*) FROM contact_plans WHERE id=?",
                          (another["id"],)).fetchone()[0] == 0


def test_shared_plan_api_session_and_stale_update(tmp_path):
    settings = Settings(JARVIS_DATA_DIR=tmp_path / "data", JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        contact = client.post("/contacts", json={"name": "Mira", "context": "Verein"},
                              headers=headers).json()
        endpoint = f"/contacts/{contact['id']}/plans"
        assert client.post(endpoint, json={"title": "Wandern"}).status_code == 403
        created = client.post(endpoint, json={"title": "Wandern", "details": "Am See"},
                              headers=headers)
        assert created.status_code == 201
        plan = created.json()
        assert client.get(f"/contacts/{contact['id']}").json()["plans"][0]["id"] == plan["id"]
        update = {"title": "Wandern", "details": "Sonntag", "status": "completed",
                  "expected_version": 1}
        assert client.put(f"{endpoint}/{plan['id']}", json=update,
                          headers=headers).status_code == 200
        assert client.put(f"{endpoint}/{plan['id']}", json=update,
                          headers=headers).status_code == 409
        assert client.delete(f"{endpoint}/{plan['id']}?expected_version=1",
                             headers=headers).status_code == 409
        assert client.delete(f"{endpoint}/{plan['id']}?expected_version=2",
                             headers=headers).status_code == 200
        assert client.get(f"/contacts/{contact['id']}").json()["plans"] == []


def test_schema_55_contacts_and_notes_survive_plan_migration(tmp_path):
    path = tmp_path / "jarvis.db"
    db = Database(path)
    store = ContactStore(db)
    person = store.create("Alex", "Familie")
    note = store.add_note(person["id"], "Bevorzugt Spaziergänge")
    with db.connect() as connection:
        connection.execute("DROP TABLE contact_plans")
        connection.execute("PRAGMA user_version=55")
    migrated = ContactStore(Database(path))
    assert migrated.get(person["id"])["notes"][0]["id"] == note["id"]
    assert migrated.get(person["id"])["plans"] == []
    assert migrated.add_plan(person["id"], "Spaziergang")["status"] == "open"


def test_plan_reminder_link_tracks_task_changes_and_delivery(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    contacts = ContactStore(db)
    tasks = TaskStore(db)
    person = contacts.create("Mira", "Verein")
    plan = contacts.add_plan(person["id"], "Museum")
    due = future_due()
    task = tasks.create("Mit Mira ins Museum", "", due, "Europe/Paris")
    linked = contacts.link_reminder(person["id"], plan["id"], 1, task["id"])
    assert linked["version"] == 2
    assert linked["reminder"]["status"] == "active"
    assert linked["reminder"]["task_title_snapshot"] == task["title"]
    with pytest.raises(ContactConflict):
        contacts.unlink_reminder(person["id"], plan["id"], 1)
    another = contacts.add_plan(person["id"], "Kino")
    with pytest.raises(ContactConflict):
        contacts.link_reminder(person["id"], another["id"], 1, task["id"])
    tasks.update(task["id"], "Mit Mira ins Kunstmuseum", "", due, "Europe/Paris")
    assert next(p for p in contacts.get(person["id"])["plans"] if p["id"] == plan["id"])[
        "reminder"]["status"] == "task_changed"
    tasks.update(task["id"], "Mit Mira ins Kunstmuseum", "", None, "Europe/Paris")
    assert next(p for p in contacts.get(person["id"])["plans"] if p["id"] == plan["id"])[
        "reminder"]["status"] == "due_removed"
    tasks.complete(task["id"])
    assert next(p for p in contacts.get(person["id"])["plans"] if p["id"] == plan["id"])[
        "reminder"]["status"] == "task_completed"
    tasks.delete(task["id"])
    stale = next(p for p in contacts.get(person["id"])["plans"] if p["id"] == plan["id"])
    assert stale["reminder"]["status"] == "task_deleted"
    assert stale["reminder"]["task_id"] is None
    assert stale["reminder"]["task_title_snapshot"] == "Mit Mira ins Museum"
    assert contacts.unlink_reminder(person["id"], plan["id"], 2)["reminder"] is None


def test_reminder_link_backup_migration_and_actual_local_notification(tmp_path):
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    contacts = ContactStore(db)
    tasks = TaskStore(db)
    person = contacts.create("Alex", "München")
    plan = contacts.add_plan(person["id"], "Kino")
    task = tasks.create("Kino mit Alex", "", future_due(), "Europe/Paris")
    contacts.link_reminder(person["id"], plan["id"], 1, task["id"])
    archive = tmp_path / "backup.zip"
    assert create_backup(root, archive)["schema"] == 62
    restored_root = tmp_path / "restored"
    assert restore_backup(archive, restored_root)["schema"] == 62
    restored_db = Database(restored_root / "jarvis.db")
    assert ContactStore(restored_db).get(person["id"])["plans"][0]["reminder"][
        "status"] == "active"
    future_utc = (datetime.fromisoformat(task["due_utc"]) + timedelta(minutes=1)).isoformat()
    assert TaskStore(restored_db).fire_due(future_utc) == 1
    assert ContactStore(restored_db).get(person["id"])["plans"][0]["reminder"][
        "status"] == "fired"
    with restored_db.connect() as connection:
        assert connection.execute("SELECT title FROM notifications WHERE task_id=?",
                                  (task["id"],)).fetchone()[0] == "Kino mit Alex"
    with db.connect() as connection:
        connection.execute("DROP TABLE contact_plan_reminders")
        connection.execute("PRAGMA user_version=56")
    migrated = ContactStore(Database(db.path))
    assert migrated.get(person["id"])["plans"][0]["title"] == "Kino"
    assert migrated.get(person["id"])["plans"][0]["reminder"] is None


def test_reminder_link_api_rejects_stale_and_missing_due(tmp_path):
    settings = Settings(JARVIS_DATA_DIR=tmp_path / "data", JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        person = client.post("/contacts", json={"name": "Mira", "context": "Freundin"},
                             headers=headers).json()
        plan = client.post(f"/contacts/{person['id']}/plans", json={"title": "Wandern"},
                           headers=headers).json()
        task = client.post("/tasks", json={"title": "Wandern mit Mira", "notes": "",
                                          "local_due": future_due(),
                                          "timezone": "Europe/Paris"}, headers=headers).json()
        endpoint = f"/contacts/{person['id']}/plans/{plan['id']}/reminder"
        assert client.put(endpoint, json={"task_id": task["id"],
                                          "expected_plan_version": 1}).status_code == 403
        response = client.put(endpoint, json={"task_id": task["id"],
                                              "expected_plan_version": 1}, headers=headers)
        assert response.status_code == 200 and response.json()["reminder"]["status"] == "active"
        assert client.put(endpoint, json={"task_id": task["id"],
                                          "expected_plan_version": 1},
                          headers=headers).status_code == 409
        assert client.delete(f"{endpoint}?expected_plan_version=1",
                             headers=headers).status_code == 409
        assert client.delete(f"{endpoint}?expected_plan_version=2",
                             headers=headers).status_code == 200
        no_due = client.post("/tasks", json={"title": "Ohne Datum", "notes": "",
                                            "timezone": "Europe/Paris"}, headers=headers).json()
        assert client.put(endpoint, json={"task_id": no_due["id"],
                                          "expected_plan_version": 3},
                          headers=headers).status_code == 422
