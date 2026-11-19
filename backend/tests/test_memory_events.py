"""Confirmed life events remain local, editable, exportable, and restorable."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.memory_events import MemoryEventConflict, MemoryEventStore


def test_timeline_store_search_version_and_export(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    store = MemoryEventStore(db)
    first = store.create("2024-08-12", "Café in Lyon", "Mit Alex\nAm Fluss", "Lyon")
    second = store.create("2023-01-03", "Winterfahrt", "Mit Mira", "Zürich")
    assert first["source_kind"] == "manual" and first["version"] == 1
    assert [item["id"] for item in store.list()] == [first["id"], second["id"]]
    assert [item["id"] for item in store.list("CAFE")] == [first["id"]]
    assert [item["id"] for item in store.list("mira")] == [second["id"]]
    assert [item["id"] for item in store.list(from_date="2024-01-01")] == [first["id"]]
    assert [item["id"] for item in store.list(to_date="2023-12-31")] == [second["id"]]
    changed = store.update(first["id"], 1, "2024-08-13", "Café am Fluss", "Mit Alex", "Lyon")
    assert changed["version"] == 2 and changed["date_local"] == "2024-08-13"
    with pytest.raises(MemoryEventConflict):
        store.update(first["id"], 1, "2024-08-13", "Überschreiben")
    with pytest.raises(MemoryEventConflict):
        store.delete(first["id"], 1)
    markdown = store.export_markdown("Lyon").decode("utf-8")
    assert "2024-08-13 · Café am Fluss" in markdown
    assert "Winterfahrt" not in markdown
    assert "Quelle: manuell bestätigt · Version 2" in markdown
    store.delete(first["id"], 2)
    assert store.list("Lyon") == []
    with pytest.raises(LookupError):
        store.get(first["id"])


def test_same_title_on_different_dates_stays_distinct(tmp_path):
    store = MemoryEventStore(Database(tmp_path / "jarvis.db"))
    older = store.create("2022-06-01", "Urlaub", location="Lyon")
    newer = store.create("2024-06-01", "Urlaub", location="Paris")
    assert older["id"] != newer["id"]
    assert [item["id"] for item in store.list("Urlaub")] == [newer["id"], older["id"]]
    store.delete(older["id"], 1)
    assert [item["id"] for item in store.list("Urlaub")] == [newer["id"]]


@pytest.mark.parametrize("date_local,title,description,location", [
    ("2024-02-30", "Titel", "", ""),
    ("2024-1-1", "Titel", "", ""),
    ("2024-01-01", "   ", "", ""),
    ("2024-01-01", "Titel", "x" * 5001, ""),
    ("2024-01-01", "Titel", "Text\x00", ""),
    ("2024-01-01", "Titel", "", "Ort" * 60),
])
def test_timeline_rejects_invalid_fields(tmp_path, date_local, title, description, location):
    db = Database(tmp_path / "jarvis.db")
    with pytest.raises(ValueError):
        MemoryEventStore(db).create(date_local, title, description, location)


def test_timeline_filter_validation_and_migration(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    with db.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 64
        connection.execute("DROP TABLE memory_events")
        connection.execute("PRAGMA user_version=58")
    db.migrate()
    store = MemoryEventStore(db)
    store.create("2024-01-01", "Nach Migration")
    with pytest.raises(ValueError):
        store.list(from_date="2024-05-01", to_date="2024-01-01")
    with pytest.raises(ValueError):
        store.list(from_date="2024-02-30")
    with pytest.raises(ValueError):
        store.list("q" * 201)
    with sqlite3.connect(db.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 64


def test_timeline_api_csrf_conflicts_export_and_backup(tmp_path):
    root = tmp_path / "data"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture"))) as client:
        payload = {"date_local": "2025-04-17", "title": "Geburtstag", "description": "Schön", "location": "Paris"}
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        assert client.post("/memory-events", json=payload).status_code == 403
        created = client.post("/memory-events", json=payload, headers=headers)
        assert created.status_code == 201, created.text
        event = created.json()
        assert client.get(f"/memory-events/{event['id']}").json()["title"] == "Geburtstag"
        assert len(client.get("/memory-events?q=Paris").json()) == 1
        assert client.get("/memory-events?from_date=2025-04-18").json() == []
        exported = client.get("/memory-events/export?q=Paris")
        assert exported.status_code == 200 and "Geburtstag" in exported.text
        assert "lebenschronik.md" in exported.headers["content-disposition"]
        assert exported.headers["cache-control"] == "no-store"
        assert client.get("/memory-events?from_date=2025-02-30").status_code == 422
        assert client.put(f"/memory-events/{event['id']}", json={**payload,
                          "expected_version": 1, "title": "Korrigierter Geburtstag"},
                          headers=headers).status_code == 200
        assert client.put(f"/memory-events/{event['id']}", json={**payload,
                          "expected_version": 1}, headers=headers).status_code == 409
        assert client.delete(f"/memory-events/{event['id']}?expected_version=1",
                             headers=headers).status_code == 409
        with Database(root / "jarvis.db").connect() as db:
            assert db.execute("SELECT count(*) FROM model_usage").fetchone()[0] == 0
    archive = tmp_path / "timeline.zip"
    assert create_backup(root, archive)["schema"] == 64
    restored = tmp_path / "restored"
    assert restore_backup(archive, restored)["schema"] == 64
    assert MemoryEventStore(Database(restored / "jarvis.db")).get(event["id"])[
        "title"] == "Korrigierter Geburtstag"
