"""Explizite Projekt-Erinnerungen behalten Quelle, Policy und alten Text."""

import sqlite3

import pytest
from fastapi.testclient import TestClient
from test_project_people import setup_project

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.project_memories import ProjectMemoryConflict, ProjectMemoryStore


def test_memory_link_source_edit_confirm_delete_and_restore(tmp_path):
    root, db, documents, states, _, _, _, _, state = setup_project(tmp_path)
    store = ProjectMemoryStore(db, states)
    memory = documents.add_memory("Atlas hat drei Prüfschritte", "Protokoll Abschnitt 2", "local_only")
    link = store.add(state["id"], 1, memory["id"], "Für Atlas bewusst ausgewählt",
                     "confirmed", "2026-01-01", None)
    assert link["memory_status"] == "unveraendert"
    assert link["memory_policy"] == "local_only"
    assert len(link["memory_sha256"]) == 64
    assert link["memory_source_ref"] == "Protokoll Abschnitt 2"
    assert link["confidence"] == "confirmed"
    documents.update_memory(memory["id"], "Atlas hat vier Prüfschritte",
                            "Neue Notiz", "cloud_allowed")
    changed = store.list_for_state(state["id"])[0]
    assert changed["memory_status"] == "geaendert"
    assert changed["memory_text"] == "Atlas hat drei Prüfschritte"
    assert changed["current_memory_text"] == "Atlas hat vier Prüfschritte"
    assert changed["memory_policy"] == "local_only"
    confirmed = store.confirm(state["id"], link["id"], 1, 1)
    assert confirmed["version"] == 2 and confirmed["memory_status"] == "unveraendert"
    assert confirmed["memory_policy"] == "cloud_allowed"
    documents.delete_memory(memory["id"])
    deleted = store.list_for_state(state["id"])[0]
    assert deleted["memory_status"] == "geloescht"
    assert deleted["memory_text"] == "Atlas hat vier Prüfschritte"
    with pytest.raises(ProjectMemoryConflict, match="Gelöschte"):
        store.confirm(state["id"], link["id"], 1, 2)
    archive = tmp_path / "profile.jarvis"
    assert create_backup(root, archive)["schema"] == 63
    restored = tmp_path / "restored"
    assert restore_backup(archive, restored)["schema"] == 63
    with Database(restored / "jarvis.db").connect() as connection:
        row = connection.execute("SELECT memory_text,memory_policy FROM project_memory_links").fetchone()
        assert tuple(row) == ("Atlas hat vier Prüfschritte", "cloud_allowed")
    store.delete(state["id"], link["id"], 1, 2)
    assert store.list_for_state(state["id"]) == []


def test_memory_link_api_versions_csrf_project_isolation_and_graph(tmp_path):
    root, db, documents, states, _, _, _, _, state = setup_project(tmp_path)
    memory = documents.add_memory("Atlas-Risiko: unklare Frist", "Eigene Besprechung", "local_only")
    other = states.create("Beta", "", "", "Weiter", [], [], [])
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root))) as client:
        csrf = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        url = f"/work-states/{state['id']}/memory-links"
        body = {"expected_state_version": 1, "memory_id": memory["id"],
                "source_note": "Besprechung für Atlas", "confidence": "plausible",
                "valid_from": None, "valid_until": None}
        assert client.post(url, json=body).status_code == 403
        assert client.post(url, headers=csrf, json={**body,
            "expected_state_version": 9}).status_code == 409
        assert client.post(url, headers=csrf, json={**body,
            "memory_id": "fremd"}).status_code == 422
        response = client.post(url, headers=csrf, json=body)
        assert response.status_code == 200, response.text
        link = response.json()
        assert client.post(url, headers=csrf, json=body).status_code == 422
        assert client.get(url).json()[0]["id"] == link["id"]
        connections = client.get(f"/work-states/{state['id']}/connections").json()
        assert connections["memories"][0]["memory_text"] == "Atlas-Risiko: unklare Frist"
        assert connections["memories"][0]["memory_policy"] == "local_only"
        assert client.get(f"/work-states/{other['id']}/memory-links").json() == []
        assert client.get(f"/work-states/{other['id']}/graph").json()["nodes"] == [
            {"id": f"project:{other['id']}", "kind": "project", "label": "Beta"}]
        graph = client.get(f"/work-states/{state['id']}/graph").json()
        assert any(node["id"] == f"memory:{memory['id']}" for node in graph["nodes"])
        assert any(edge["relation"] == "project_memory" for edge in graph["edges"])
        detail = f"{url}/{link['id']}"
        update_body = {key: value for key, value in body.items() if key != "memory_id"}
        assert client.put(detail, headers=csrf, json={**update_body,
            "expected_version": 2}).status_code == 409
        revised = client.put(detail, headers=csrf, json={**update_body,
            "expected_version": 1, "source_note": "Korrigierter Projektbeleg"})
        assert revised.status_code == 200 and revised.json()["version"] == 2
        documents.update_memory(memory["id"], "Atlas-Risiko: neuer Termin",
                                "Folgebesprechung", "cloud_allowed")
        assert client.get(url).json()[0]["memory_status"] == "geaendert"
        assert client.post(f"{detail}/confirm", json={
            "expected_state_version": 1, "expected_version": 2}).status_code == 403
        confirmed = client.post(f"{detail}/confirm", headers=csrf, json={
            "expected_state_version": 1, "expected_version": 2})
        assert confirmed.status_code == 200 and confirmed.json()["version"] == 3
        assert confirmed.json()["memory_policy"] == "cloud_allowed"
        assert confirmed.json()["memory_status"] == "unveraendert"
        assert client.delete(detail, headers=csrf, params={
            "expected_state_version": 1, "expected_version": 1}).status_code == 409
        assert client.delete(detail, headers=csrf, params={
            "expected_state_version": 1, "expected_version": 3}).status_code == 200
        assert client.get(url).json() == []
        assert client.get("/work-states/missing/memory-links").status_code == 404
    assert documents.get_memory(memory["id"]) is not None
    with db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM project_memory_links").fetchone()[0] == 0


def test_memory_link_limit_and_schema46_migration(tmp_path):
    _, db, documents, states, _, _, _, _, state = setup_project(tmp_path)
    store = ProjectMemoryStore(db, states)
    for index in range(10):
        memory = documents.add_memory(f"Notiz {index}", "Eigene Notiz", "local_only")
        store.add(state["id"], 1, memory["id"], "Bewusst zugeordnet")
    extra = documents.add_memory("Weitere Notiz", "Eigene Notiz", "local_only")
    with pytest.raises(ValueError, match="Höchstens zehn"):
        store.add(state["id"], 1, extra["id"], "Bewusst zugeordnet")
    with db.connect() as connection:
        connection.execute("DROP TABLE draft_named_style_usage")
        connection.execute("DROP TABLE named_style_profile_revisions")
        connection.execute("DROP TABLE named_style_profiles")
        connection.execute("DROP TABLE project_memory_links")
        connection.execute("DROP TABLE learning_sessions")
        connection.execute("DROP TABLE memory_proposal_events")
        connection.execute("DROP TABLE memory_proposals")
        connection.execute("DROP TABLE memory_context_uses")
        connection.execute("PRAGMA user_version=46")
    migrated = Database(db.path)
    with sqlite3.connect(migrated.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 63
        assert connection.execute("SELECT count(*) FROM project_memory_links").fetchone()[0] == 0
