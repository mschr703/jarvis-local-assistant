"""Projektfristen stammen nur aus ausdrücklich verknüpften Aufgaben."""

import json

from fastapi.testclient import TestClient
from test_project_people import setup_project

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.tasks import TaskStore
from jarvis.work_states import WorkStateStore


def test_project_deadline_snapshot_dst_change_removal_and_legacy(tmp_path):
    root, db, _, states, _, _, _, _, _ = setup_project(tmp_path)
    tasks = TaskStore(db)
    task = tasks.create("Zeitfenster prüfen", "", "2030-10-27T02:30", "Europe/Paris", fold=0)
    state = states.create("Atlas-Fristen", "", "", "Termin prüfen", [], [task["id"]], [])
    original = states.get(state["id"])["tasks"][0]
    assert original["due_status"] == "unveraendert"
    assert original["due_offset"] == "+02:00" and original["due_fold"] == 0
    assert original["current_due_utc"] == original["due_utc"]

    tasks.update(task["id"], "Zeitfenster prüfen", "", "2030-10-27T02:30",
                 "Europe/Paris", fold=1)
    changed = states.get(state["id"])["tasks"][0]
    assert changed["due_status"] == "geaendert"
    assert changed["due_utc"] != changed["current_due_utc"]
    assert changed["due_fold"] == 0 and changed["current_due_fold"] == 1
    assert changed["current_due_offset"] == "+01:00"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root))) as client:
        client.get("/session")
        response = client.get(f"/work-states/{state['id']}/connections")
        assert response.status_code == 200, response.text
        connections = response.json()
        assert connections["tasks"][0]["due_status"] == "geaendert"
        graph = client.get(f"/work-states/{state['id']}/graph").json()
        assert any(node["id"] == f"deadline:{task['id']}" for node in graph["nodes"])
        assert any(edge["relation"] == "task_deadline" and edge["status"] == "geaendert"
                   for edge in graph["edges"])
        paths = client.get(f"/work-states/{state['id']}/graph/paths", params={
            "source": f"project:{state['id']}", "target": f"deadline:{task['id']}",
            "relations": "project_task,task_deadline"}).json()
        assert len(paths["paths"]) == 1

    tasks.update(task["id"], "Zeitfenster prüfen", "", None, "Europe/Paris")
    removed = states.get(state["id"])["tasks"][0]
    assert removed["due_status"] == "entfernt"
    assert removed["due_utc"] == original["due_utc"]
    assert removed["current_due_utc"] is None
    archive = tmp_path / "fristen.jarvis"
    assert create_backup(root, archive)["schema"] == 59
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    restored_db = Database(restored / "jarvis.db")
    restored_states = WorkStateStore(restored_db, DocumentStore(restored_db), TaskStore(restored_db))
    assert restored_states.get(state["id"])["tasks"][0]["due_status"] == "entfernt"

    with db.connect() as connection:
        legacy = [{"id": task["id"], "title": task["title"]}]
        connection.execute("UPDATE work_states SET tasks_json=? WHERE id=?",
                           (json.dumps(legacy), state["id"]))
    assert states.get(state["id"])["tasks"][0]["due_status"] == "altbestand_ohne_snapshot"


def test_project_deadline_added_isolated_and_deleted_task(tmp_path):
    root, db, _, states, _, _, _, _, _ = setup_project(tmp_path)
    tasks = TaskStore(db)
    task = tasks.create("Später terminieren", "", None, "Europe/Paris")
    state = states.create("Atlas-Fristen", "", "", "Termin prüfen", [], [task["id"]], [])
    other = states.create("Beta-Fristen", "", "", "Weiter", [], [], [])
    assert states.get(state["id"])["tasks"][0]["due_status"] == "ohne_frist"
    tasks.update(task["id"], task["title"], "", "2031-05-20T12:00", "Europe/Paris")
    added = states.get(state["id"])["tasks"][0]
    assert added["due_status"] == "hinzugefuegt" and added["due_utc"] is None
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root))) as client:
        client.get("/session")
        other_response = client.get(f"/work-states/{other['id']}/connections")
        assert other_response.status_code == 200, other_response.text
        assert other_response.json()["tasks"] == []
        response = client.get(f"/work-states/{state['id']}/connections")
        assert response.status_code == 200, response.text
        assert len(response.json()["tasks"]) == 1
    tasks.delete(task["id"])
    assert states.get(state["id"])["tasks"][0]["due_status"] == "aufgabe_geloescht"
    dated = tasks.create("Frist vor Löschung", "", "2031-06-15T10:00", "Europe/Paris")
    dated_state = states.create("Gamma-Fristen", "", "", "Sichern", [], [dated["id"]], [])
    tasks.delete(dated["id"])
    deleted = states.get(dated_state["id"])["tasks"][0]
    assert deleted["due_status"] == "aufgabe_geloescht"
    assert deleted["due_local"] == "2031-06-15T10:00"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root))) as client:
        client.get("/session")
        graph = client.get(f"/work-states/{dated_state['id']}/graph").json()
        assert any(node["id"] == f"deadline:{dated['id']}" and
                   node["status"] == "aufgabe_geloescht" for node in graph["nodes"])
