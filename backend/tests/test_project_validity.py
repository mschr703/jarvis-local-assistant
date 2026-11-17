"""Nutzervertrauen und Gültigkeit bleiben getrennt von Quellenprüfung."""

import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from test_project_people import setup_project

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.project_people import ProjectPeopleStore
from jarvis.project_person_tasks import ProjectPersonTaskStore
from jarvis.project_validity import clean_metadata, local_date, validity_status


def test_metadata_dates_are_iso_inclusive_and_use_selected_timezone():
    for start, end in (("2026-02-30", None), ("20261028", None),
                       ("2026-10-29", "2026-10-28")):
        with pytest.raises(ValueError):
            clean_metadata("plausible", start, end)
    with pytest.raises(ValueError):
        clean_metadata("certain", None, None)
    instant = datetime(2026, 10, 28, 23, 30, tzinfo=UTC)
    assert local_date("Europe/Paris", instant) == date(2026, 10, 29)
    assert local_date("America/New_York", instant) == date(2026, 10, 28)
    assert validity_status("2026-10-29", "2026-10-29", date(2026, 10, 28)) == "noch_nicht_gueltig"
    assert validity_status("2026-10-29", "2026-10-29", date(2026, 10, 29)) == "aktuell"
    assert validity_status("2026-10-29", "2026-10-29", date(2026, 10, 30)) == "abgelaufen"


def test_person_and_task_validity_do_not_hide_changed_source(tmp_path):
    _root, db, _, states, source, _, document, task, state = setup_project(tmp_path)
    people = ProjectPeopleStore(db, states)
    links = ProjectPersonTaskStore(db, states, people)
    person = people.add(state["id"], 1, "Mira", "lead", "Protokoll nennt Mira",
                        document["id"], "confirmed", "2026-10-29", None)
    link = links.add(state["id"], 1, person["id"], task["id"], "responsible",
                     "Protokoll nennt die Aufgabe", document["id"],
                     "confirmed", None, "2026-10-27")
    today = date(2026, 10, 28)
    assert people.list_for_state(state["id"], as_of=today)[0]["validity_status"] == "noch_nicht_gueltig"
    before = links.list_for_state(state["id"], as_of=today)[0]
    assert before["status"] == "person_nicht_gueltig"
    assert before["validity_status"] == "abgelaufen"
    person = people.update(state["id"], person["id"], 1, 1, "Mira", "lead",
                           "Protokoll erneut bestätigt", document["id"],
                           "confirmed", "2026-10-28", None)
    assert person["confidence"] == "confirmed" and person["version"] == 2
    assert links.list_for_state(state["id"], as_of=today)[0]["status"] == "abgelaufen"
    source.write_text("Inhalt inzwischen geändert", encoding="utf-8")
    changed = links.list_for_state(state["id"], as_of=today)[0]
    assert changed["confidence"] == "confirmed"
    assert changed["source_status"] == "geaendert"
    assert changed["validity_status"] == "abgelaufen"
    assert changed["status"] == "beleg_pruefen"
    assert link["id"] == changed["id"]


def test_validity_api_revision_backup_and_schema43_migration(tmp_path):
    root, db, _, states, _, _, document, task, state = setup_project(tmp_path)
    people = ProjectPeopleStore(db, states)
    person = people.add(state["id"], 1, "Mira", "lead", "Protokoll nennt Mira",
                        document["id"])
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root))) as client:
        csrf = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        person_url = f"/work-states/{state['id']}/people/{person['id']}"
        person_body = {"expected_state_version": 1, "expected_version": 1,
                       "name": "Mira", "relation_type": "lead",
                       "source_note": "Protokoll nennt Mira",
                       "source_document_id": document["id"],
                       "confidence": "confirmed", "valid_from": "2026-10-28",
                       "valid_until": "2026-10-29"}
        assert client.put(person_url, json=person_body).status_code == 403
        assert client.put(person_url, headers=csrf, json={**person_body,
            "valid_until": "2026-10-27"}).status_code == 422
        assert client.put(person_url, headers=csrf, json={**person_body,
            "valid_from": "2026-02-30"}).status_code == 422
        updated = client.put(person_url, headers=csrf, json=person_body)
        assert updated.status_code == 200 and updated.json()["version"] == 2
        assert client.put(person_url, headers=csrf, json=person_body).status_code == 409
        url = f"/work-states/{state['id']}/person-tasks"
        link_body = {"expected_state_version": 1, "person_id": person["id"],
                     "task_id": task["id"], "relation_type": "responsible",
                     "source_note": "Mira betreut die Aufgabe", "source_document_id": None,
                     "confidence": "plausible", "valid_from": None,
                     "valid_until": "2999-12-31"}
        link = client.post(url, headers=csrf, json=link_body)
        assert link.status_code == 200 and link.json()["confidence"] == "plausible"
        query = client.get(f"/work-states/{state['id']}/connections").json()
        assert query["timezone"] == "Europe/Berlin"
        assert query["people"][0]["confidence"] == "confirmed"
        assert query["person_tasks"][0]["confidence"] == "plausible"
        changed_zone = client.patch("/settings", headers=csrf,
                                    json={"timezone": "America/New_York"})
        assert changed_zone.status_code == 200
        query = client.get(f"/work-states/{state['id']}/connections").json()
        assert query["timezone"] == "America/New_York"
        assert query["as_of_local_date"] == local_date("America/New_York").isoformat()
    archive = tmp_path / "profile.jarvis"
    assert create_backup(root, archive)["schema"] == 52
    restored = tmp_path / "restored"
    assert restore_backup(archive, restored)["schema"] == 52
    with Database(restored / "jarvis.db").connect() as connection:
        assert tuple(connection.execute("SELECT confidence,valid_from FROM project_people WHERE id=?",
                                        (person["id"],)).fetchone()) == ("confirmed", "2026-10-28")

    # Eine echte Schema-43-Struktur ohne die sechs neuen Spalten migriert mit
    # konservativem Vertrauensgrad und ohne Gültigkeitsgrenze.
    older = tmp_path / "older.db"
    with closing(sqlite3.connect(db.path)) as source_db, closing(sqlite3.connect(older)) as target_db:
        source_db.backup(target_db)
    with sqlite3.connect(older) as connection:
        connection.execute("DROP TABLE draft_named_style_usage")
        connection.execute("DROP TABLE named_style_profile_revisions")
        connection.execute("DROP TABLE named_style_profiles")
        connection.execute("DROP TABLE project_memory_links")
        connection.execute("DROP TABLE project_person_merges")
        connection.execute("DROP TABLE project_person_aliases")
        for table in ("project_people", "project_person_tasks"):
            for column in ("confidence", "valid_from", "valid_until"):
                connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        connection.execute("DROP TABLE learning_sessions")
        connection.execute("DROP TABLE memory_proposal_events")
        connection.execute("DROP TABLE memory_proposals")
        connection.execute("DROP TABLE memory_context_uses")
        connection.execute("PRAGMA user_version=43")
    migrated = Database(older)
    with migrated.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 52
        assert tuple(connection.execute("SELECT confidence,valid_from,valid_until FROM project_people"
                                        ).fetchone()) == ("unverified", None, None)
