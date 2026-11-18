"""Manuell erfasste Personen bleiben mit ihrem Projekt und Quellenstand verbunden."""

import sqlite3

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.project_aliases import ProjectAliasStore
from jarvis.project_connections import ProjectConnections
from jarvis.project_people import ProjectPeopleStore, ProjectPersonConflict
from jarvis.project_person_tasks import ProjectPersonTaskStore
from jarvis.tasks import TaskStore
from jarvis.work_states import WorkStateStore


def setup_project(tmp_path):
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    documents = DocumentStore(db)
    tasks = TaskStore(db)
    work_states = WorkStateStore(db, documents, tasks)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    source = source_dir / "protokoll.txt"
    source.write_text("Projekt Atlas: Mira leitet die technische Prüfung.", encoding="utf-8")
    folder = documents.add_folder(str(source_dir), "local_only")
    documents.index_folder(folder["id"])
    document = documents.documents()[0]
    task = tasks.create("Atlas prüfen", "", None, "Europe/Paris")
    state = work_states.create("Atlas", "Start bestätigt", "", "Prüfen",
                               [document["id"]], [task["id"]], [])
    return root, db, documents, work_states, source, folder, document, task, state


def test_person_source_lifecycle_and_other_project_isolation(tmp_path):
    root, db, documents, states, source, folder, document, task, state = setup_project(tmp_path)
    people = ProjectPeopleStore(db, states)
    second = states.create("Beta", "", "", "Starten", [], [], [])
    linked = people.add(state["id"], 1, "  Mira   Weber ", "lead",
                        "Protokoll nennt die Leitung", document["id"])
    assert linked["name"] == "Mira Weber"
    assert linked["source_status"] == "unveraendert"
    assert linked["source_sha256"] == state["documents"][0]["sha256"]
    assert linked["source_path"] == str(source)
    assert people.list_for_state(second["id"]) == []
    assert states.get(state["id"])["tasks"][0]["id"] == task["id"]
    assert states.get(state["id"])["documents"][0]["id"] == document["id"]
    try:
        people.add(state["id"], 1, "mira weber", "contact", "Zweite Angabe", None)
    except ValueError as exc:
        assert "bereits" in str(exc)
    else:
        raise AssertionError("Doppelte Person wurde angenommen")

    source.write_text("Projekt Atlas: Mira ist jetzt externe Prüferin.", encoding="utf-8")
    assert people.list_for_state(state["id"])[0]["source_status"] == "geaendert"
    try:
        people.update(state["id"], linked["id"], 1, 1, "Mira Weber", "reviewer",
                      "Datei wurde geprüft", document["id"])
    except ProjectPersonConflict as exc:
        assert "Quelldatei" in str(exc)
    else:
        raise AssertionError("Geänderte Quelldatei wurde als gültig angenommen")
    documents.index_folder(folder["id"])
    refreshed = states.confirm_document(state["id"], document["id"], 1)
    assert people.list_for_state(state["id"])[0]["source_status"] == "quelle_erneuert"
    revised = people.update(state["id"], linked["id"], refreshed["version"], 1,
                            "Mira Weber", "reviewer", "Neu bestätigtes Protokoll",
                            document["id"])
    assert revised["version"] == 2 and revised["source_status"] == "unveraendert"
    documents.remove_folder(folder["id"])
    assert people.list_for_state(state["id"])[0]["source_status"] == "freigabe_entfernt"
    manual = people.update(state["id"], linked["id"], refreshed["version"], 2,
                           "Mira Weber", "contact", "Eigene bestätigte Angabe", None)
    assert manual["source_status"] == "eigene_angabe"
    assert manual["source_document_id"] is None
    people.delete(state["id"], linked["id"], refreshed["version"], 3)
    assert people.list_for_state(state["id"]) == []
    another = people.add(state["id"], refreshed["version"], "Noah", "contributor",
                         "Von mir eingetragen", None)
    archive = tmp_path / "profile.jarvis"
    assert create_backup(root, archive)["schema"] == 54
    restored_root = tmp_path / "restored"
    assert restore_backup(archive, restored_root)["schema"] == 54
    restored_db = Database(restored_root / "jarvis.db")
    with restored_db.connect() as connection:
        assert connection.execute("SELECT name FROM project_people WHERE id=?",
                                  (another["id"],)).fetchone()[0] == "Noah"
    states.delete(state["id"], refreshed["version"])
    with db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM project_people").fetchone()[0] == 0
    assert people.list_for_state(second["id"]) == []


def test_person_api_csrf_revision_and_source_guards(tmp_path):
    root, _, _, states, _, _, document, _, state = setup_project(tmp_path)
    other = states.create("Beta", "", "", "Starten", [], [], [])
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root))) as client:
        csrf = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        url = f"/work-states/{state['id']}/people"
        body = {"expected_state_version": 1, "name": "Mira", "relation_type": "lead",
                "source_note": "Im Atlas-Protokoll genannt",
                "source_document_id": document["id"]}
        assert client.post(url, json=body).status_code == 403
        assert client.post(url, headers=csrf, json={**body,
            "source_document_id": "fremd"}).status_code == 422
        assert client.post(f"/work-states/{other['id']}/people", headers=csrf,
                           json=body).status_code == 422
        assert client.post(url, headers=csrf, json={**body,
            "expected_state_version": 99}).status_code == 409
        response = client.post(url, headers=csrf, json=body)
        assert response.status_code == 200, response.text
        person = response.json()
        assert person["source_status"] == "unveraendert"
        assert len(client.get(url).json()) == 1
        detail = url + "/" + person["id"]
        assert client.put(detail, headers=csrf, json={**body,
            "expected_version": 9}).status_code == 409
        assert client.put(f"/work-states/{other['id']}/people/{person['id']}",
            headers=csrf, json={**body, "expected_version": 1}).status_code == 404
        updated = client.put(detail, headers=csrf, json={**body,
            "expected_version": 1, "relation_type": "reviewer"})
        assert updated.status_code == 200 and updated.json()["version"] == 2
        assert client.delete(detail, headers=csrf, params={
            "expected_state_version": 1, "expected_version": 1}).status_code == 409
        assert client.delete(detail, headers=csrf, params={
            "expected_state_version": 1, "expected_version": 2}).status_code == 200
        assert client.get(url).json() == []
        assert client.get("/work-states/missing/people").status_code == 404


def test_schema41_migrates_to_project_people_without_touching_work_states(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    work_states = WorkStateStore(db, DocumentStore(db), TaskStore(db))
    state = work_states.create("Atlas", "", "", "Weiter", [], [], [])
    with db.connect() as connection:
        connection.execute("DROP TABLE draft_named_style_usage")
        connection.execute("DROP TABLE named_style_profile_revisions")
        connection.execute("DROP TABLE named_style_profiles")
        connection.execute("DROP TABLE project_memory_links")
        connection.execute("DROP TABLE project_person_merges")
        connection.execute("DROP TABLE project_person_aliases")
        connection.execute("DROP TABLE project_person_tasks")
        connection.execute("DROP TABLE project_people")
        connection.execute("DROP TABLE learning_sessions")
        connection.execute("DROP TABLE memory_proposal_events")
        connection.execute("DROP TABLE memory_proposals")
        connection.execute("DROP TABLE memory_context_uses")
        connection.execute("PRAGMA user_version=41")
    migrated = Database(db.path)
    assert WorkStateStore(migrated, DocumentStore(migrated), TaskStore(migrated)).get(
        state["id"])["project"] == "Atlas"
    assert ProjectPeopleStore(migrated, WorkStateStore(
        migrated, DocumentStore(migrated), TaskStore(migrated))).list_for_state(state["id"]) == []
    with sqlite3.connect(db.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 54


def test_local_project_connections_show_provenance_changes_and_isolate_projects(tmp_path):
    _root, db, documents, states, source, folder, document, task, state = setup_project(tmp_path)
    people = ProjectPeopleStore(db, states)
    query = ProjectConnections(states, people, ProjectPersonTaskStore(db, states, people),
                               ProjectAliasStore(db, states, people))
    person = people.add(state["id"], 1, "Mira", "lead",
                        "Im Projektprotokoll genannt", document["id"])
    own = people.add(state["id"], 1, "Noah", "contact",
                     "Von mir selbst eingetragen", None)
    other = states.create("Beta", "", "", "Weiter", [], [], [])
    answer = query.for_state(state["id"])
    assert answer["project_id"] == state["id"] and answer["state_version"] == 1
    assert [item["id"] for item in answer["people"]] == [person["id"], own["id"]]
    assert [item["source_kind"] for item in answer["people"]] == [
        "current_document", "own_statement"]
    assert answer["people"][0]["source_document_id"] == document["id"]
    assert answer["people"][0]["source_sha256"] == answer["documents"][0]["sha256"]
    assert answer["tasks"][0]["id"] == task["id"]
    assert query.for_state(other["id"])["people"] == []
    assert query.for_state(other["id"])["documents"] == []
    source.write_text("Neue Fassung", encoding="utf-8")
    stale = query.for_state(state["id"])
    assert stale["people"][0]["source_kind"] == "stale_document"
    assert stale["people"][0]["source_status"] == "geaendert"
    assert stale["documents"][0]["status"] == "geaendert"
    TaskStore(db).complete(task["id"])
    assert query.for_state(state["id"])["tasks"][0]["status"] == "done"
    documents.remove_folder(folder["id"])
    assert query.for_state(state["id"])["people"][0]["source_status"] == "freigabe_entfernt"
    people.delete(state["id"], person["id"], 1, 1)
    assert [item["id"] for item in query.for_state(state["id"])["people"]] == [own["id"]]


def test_project_connections_api_reload_and_backup(tmp_path):
    root, db, _, states, _, _, document, _, state = setup_project(tmp_path)
    person = ProjectPeopleStore(db, states).add(state["id"], 1, "Mira", "lead",
                                                  "Im Protokoll genannt", document["id"])
    url = f"/work-states/{state['id']}/connections"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root))) as client:
        client.get("/session")
        response = client.get(url)
        assert response.status_code == 200, response.text
        assert response.json()["people"][0]["id"] == person["id"]
        assert response.json()["people"][0]["source_kind"] == "current_document"
        assert client.get("/work-states/unknown/connections").status_code == 404
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root))) as client:
        client.get("/session")
        assert client.get(url).json()["people"][0]["id"] == person["id"]
    archive = tmp_path / "profile.jarvis"
    create_backup(root, archive)
    restored_root = tmp_path / "restored"
    restore_backup(archive, restored_root)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=restored_root))) as client:
        client.get("/session")
        answer = client.get(url).json()
        assert answer["project_id"] == state["id"]
        assert answer["people"][0]["id"] == person["id"]
        assert answer["documents"][0]["id"] == document["id"]
