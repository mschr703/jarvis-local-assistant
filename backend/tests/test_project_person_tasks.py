"""Belegte, korrigierbare Projektaufgaben-Personen-Verbindungen."""

import sqlite3

from fastapi.testclient import TestClient
from test_project_people import setup_project

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.project_people import ProjectPeopleStore
from jarvis.project_person_tasks import ProjectPersonTaskStore
from jarvis.tasks import TaskStore
from jarvis.work_states import WorkStateStore


def test_person_task_link_lifecycle_with_source_and_project_change(tmp_path):
    root, db, documents, states, source, folder, document, task, state = setup_project(tmp_path)
    people = ProjectPeopleStore(db, states)
    links = ProjectPersonTaskStore(db, states, people)
    person = people.add(state["id"], 1, "Mira", "lead", "Protokoll nennt Mira",
                        document["id"])
    other = states.create("Beta", "", "", "Weiter", [], [], [])
    try:
        links.add(other["id"], 1, person["id"], task["id"], "responsible",
                  "Im Protokoll genannt", None)
    except ValueError as exc:
        assert "Person" in str(exc)
    else:
        raise AssertionError("Fremde Projektperson wurde angenommen")
    link = links.add(state["id"], 1, person["id"], task["id"], "responsible",
                     "Mira betreut diese Aufgabe", document["id"])
    assert link["status"] == "aktuell"
    assert link["source_status"] == "unveraendert"
    assert link["source_sha256"] == state["documents"][0]["sha256"]
    assert link["task_title"] == task["title"]
    assert links.list_for_state(other["id"]) == []
    try:
        links.add(state["id"], 1, person["id"], task["id"], "responsible",
                  "Nochmals eingetragen", None)
    except ValueError as exc:
        assert "bereits" in str(exc)
    else:
        raise AssertionError("Doppelte Aufgabenbeziehung wurde angenommen")
    source.write_text("Protokoll korrigiert", encoding="utf-8")
    assert links.list_for_state(state["id"])[0]["status"] == "beleg_pruefen"
    assert links.list_for_state(state["id"])[0]["source_status"] == "geaendert"
    documents.index_folder(folder["id"])
    current = states.confirm_document(state["id"], document["id"], 1)
    assert links.list_for_state(state["id"])[0]["source_status"] == "quelle_erneuert"
    revised = links.update(state["id"], link["id"], current["version"], 1,
                           person["id"], task["id"], "reviewer",
                           "Neue eigene Aufgabenangabe", None)
    assert revised["version"] == 2 and revised["status"] == "person_beleg_pruefen"
    TaskStore(db).complete(task["id"])
    assert links.list_for_state(state["id"])[0]["task_status"] == "done"
    current = states.update(state["id"], current["version"], "Atlas", "", "", "Weiter",
                            [document["id"]], [], [])
    assert links.list_for_state(state["id"])[0]["status"] == "aufgabe_nicht_mehr_zugeordnet"
    archive = tmp_path / "profile.jarvis"
    assert create_backup(root, archive)["schema"] == 52
    restored = tmp_path / "restored"
    assert restore_backup(archive, restored)["schema"] == 52
    with Database(restored / "jarvis.db").connect() as connection:
        assert connection.execute("SELECT count(*) FROM project_person_tasks WHERE id=?",
                                  (link["id"],)).fetchone()[0] == 1
    people.delete(state["id"], person["id"], current["version"], 1)
    assert links.list_for_state(state["id"]) == []
    with db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM project_person_tasks").fetchone()[0] == 0


def test_person_task_api_guards_revisions_and_query(tmp_path):
    root, db, _, states, _, _, document, task, state = setup_project(tmp_path)
    people = ProjectPeopleStore(db, states)
    person = people.add(state["id"], 1, "Mira", "lead", "Protokoll nennt Mira",
                        document["id"])
    other = states.create("Beta", "", "", "Weiter", [], [], [])
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root))) as client:
        csrf = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        url = f"/work-states/{state['id']}/person-tasks"
        body = {"expected_state_version": 1, "person_id": person["id"],
                "task_id": task["id"], "relation_type": "responsible",
                "source_note": "Mira betreut diese Aufgabe",
                "source_document_id": document["id"]}
        assert client.post(url, json=body).status_code == 403
        assert client.post(url, headers=csrf, json={**body,
            "expected_state_version": 2}).status_code == 409
        assert client.post(url, headers=csrf, json={**body,
            "source_document_id": "fremd"}).status_code == 422
        assert client.post(f"/work-states/{other['id']}/person-tasks",
                           headers=csrf, json=body).status_code == 422
        assert client.post(url, headers=csrf, json={**body,
            "task_id": "fremd"}).status_code == 422
        response = client.post(url, headers=csrf, json=body)
        assert response.status_code == 200, response.text
        link = response.json()
        assert link["status"] == "aktuell"
        assert client.get(f"/work-states/{state['id']}/connections").json()[
            "person_tasks"][0]["id"] == link["id"]
        assert client.get(f"/work-states/{other['id']}/connections").json()[
            "person_tasks"] == []
        assert client.get("/work-states/missing/person-tasks").status_code == 404
        detail = f"{url}/{link['id']}"
        assert client.put(detail, headers=csrf, json={**body,
            "expected_version": 3}).status_code == 409
        assert client.put(f"/work-states/{other['id']}/person-tasks/{link['id']}",
                          headers=csrf, json={**body, "expected_version": 1}).status_code == 404
        changed = client.put(detail, headers=csrf, json={**body,
            "expected_version": 1, "relation_type": "involved"})
        assert changed.status_code == 200 and changed.json()["version"] == 2
        assert client.delete(detail, headers=csrf, params={
            "expected_state_version": 1, "expected_version": 1}).status_code == 409
        assert client.delete(detail, headers=csrf, params={
            "expected_state_version": 1, "expected_version": 2}).status_code == 200
        assert client.get(url).json() == []


def test_revoked_document_and_deleted_task_do_not_look_current(tmp_path):
    _root, db, documents, states, _, folder, document, task, state = setup_project(tmp_path)
    people = ProjectPeopleStore(db, states)
    links = ProjectPersonTaskStore(db, states, people)
    person = people.add(state["id"], 1, "Mira", "lead", "Protokoll nennt Mira",
                        document["id"])
    links.add(state["id"], 1, person["id"], task["id"], "responsible",
              "Protokoll nennt die Aufgabe", document["id"])
    documents.remove_folder(folder["id"])
    revoked = links.list_for_state(state["id"])[0]
    assert revoked["source_status"] == "freigabe_entfernt"
    assert revoked["person_source_status"] == "freigabe_entfernt"
    assert revoked["status"] == "beleg_pruefen"
    TaskStore(db).delete(task["id"])
    deleted = links.list_for_state(state["id"])[0]
    assert deleted["task_status"] == "geloescht"
    assert deleted["status"] == "aufgabe_geloescht"


def test_person_task_link_limit_and_schema42_migration(tmp_path):
    _root, db, _, states, _, _, document, task, state = setup_project(tmp_path)
    people = ProjectPeopleStore(db, states)
    links = ProjectPersonTaskStore(db, states, people)
    for index in range(15):
        person = people.add(state["id"], 1, f"Person {index}", "contributor",
                            "Eigene Projektangabe", None)
        for relation in ("responsible", "involved"):
            links.add(state["id"], 1, person["id"], task["id"], relation,
                      "Eigene Aufgabenangabe", None)
    assert len(links.list_for_state(state["id"])) == 30
    extra = people.add(state["id"], 1, "Person extra", "contact",
                       "Eigene Projektangabe", None)
    try:
        links.add(state["id"], 1, extra["id"], task["id"], "involved",
                  "Eigene Aufgabenangabe", None)
    except ValueError as exc:
        assert "Höchstens 30" in str(exc)
    else:
        raise AssertionError("Mehr als 30 Aufgabenbeziehungen wurden angenommen")
    with db.connect() as connection:
        connection.execute("DROP TABLE draft_named_style_usage")
        connection.execute("DROP TABLE named_style_profile_revisions")
        connection.execute("DROP TABLE named_style_profiles")
        connection.execute("DROP TABLE project_memory_links")
        connection.execute("DROP TABLE project_person_merges")
        connection.execute("DROP TABLE project_person_aliases")
        connection.execute("DROP TABLE project_person_tasks")
        connection.execute("ALTER TABLE project_people DROP COLUMN confidence")
        connection.execute("ALTER TABLE project_people DROP COLUMN valid_from")
        connection.execute("ALTER TABLE project_people DROP COLUMN valid_until")
        connection.execute("DROP TABLE learning_sessions")
        connection.execute("DROP TABLE memory_proposal_events")
        connection.execute("DROP TABLE memory_proposals")
        connection.execute("DROP TABLE memory_context_uses")
        connection.execute("PRAGMA user_version=42")
    migrated = Database(db.path)
    with migrated.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 52
        assert connection.execute("SELECT count(*) FROM project_people").fetchone()[0] == 16
        assert connection.execute("SELECT count(*) FROM project_person_tasks").fetchone()[0] == 0
    assert WorkStateStore(migrated, DocumentStore(migrated), TaskStore(migrated)).get(
        state["id"])["documents"][0]["id"] == document["id"]
    with sqlite3.connect(migrated.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 52
