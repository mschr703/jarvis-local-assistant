import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.tasks import TaskStore
from jarvis.work_sessions import WorkSessionStore
from jarvis.work_states import WorkStateConflict, WorkStateStore


def sample(tmp_path):
    folder_path = tmp_path / "sources"
    folder_path.mkdir()
    path = folder_path / "plan.md"
    path.write_text("Freigegebene Quelle", encoding="utf-8")
    db = Database(tmp_path / "data" / "jarvis.db")
    documents = DocumentStore(db)
    folder = documents.add_folder(str(folder_path), "local_only")
    documents.index_folder(folder["id"])
    document = documents.documents()[0]
    tasks = TaskStore(db)
    task = tasks.create("Angebot prüfen", "", None, "Europe/Paris")
    states = WorkStateStore(db, documents, tasks)
    state = states.create("Atlas", "Plan liegt vor", "", "Angebot prüfen",
                          [document["id"]], [task["id"]], ["https://example.org/plan"])
    return db, documents, tasks, states, WorkSessionStore(db, states), state, document, task, path


def add_note(sessions, state, document, task):
    return sessions.create(state["id"], state["version"], "Angebot durchgesehen",
                           "Preis noch offen", "Maria fragen", [document["id"]],
                           [task["id"]], ["https://example.org/plan"])


def test_session_note_snapshots_references_and_detects_drift(tmp_path):
    _db, documents, tasks, states, sessions, state, document, task, path = sample(tmp_path)
    note = add_note(sessions, state, document, task)
    assert note["project_snapshot"] == "Atlas" and note["state_version"] == 1
    assert note["documents"][0]["status"] == "unveraendert"
    assert note["tasks"][0]["current_title"] == "Angebot prüfen"
    assert note["tasks"][0]["saved_status"] == "open"
    assert note["links"] == [{"url": "https://example.org/plan", "in_state": True,
                              "status": "nicht_geprueft"}]
    tasks.update(task["id"], "Neues Angebot prüfen", "", None, "Europe/Paris")
    path.write_text("Quelle geändert", encoding="utf-8")
    updated = states.update(state["id"], 1, "Atlas II", "", "", "Weiter",
                            [], [], [])
    result = sessions.list(state["id"])[0]
    assert result["project_snapshot"] == "Atlas" and result["state_changed"]
    assert result["documents"][0]["status"] == "geaendert"
    assert not result["documents"][0]["in_state"]
    assert result["tasks"][0]["title"] == "Angebot prüfen"
    assert result["tasks"][0]["current_title"] == "Neues Angebot prüfen"
    assert not result["tasks"][0]["in_state"]
    assert not result["links"][0]["in_state"]
    documents.set_excluded(document["id"], True)
    tasks.complete(task["id"])
    assert sessions.get(state["id"], note["id"])["documents"][0]["status"] == "ausgeschlossen"
    assert sessions.get(state["id"], note["id"])["tasks"][0]["status"] == "done"
    assert sessions.get(state["id"], note["id"])["tasks"][0]["saved_status"] == "open"
    sessions.delete(state["id"], note["id"])
    assert sessions.list(state["id"]) == []
    assert updated["version"] == 2


def test_session_rejects_stale_state_or_invalid_references(tmp_path):
    _, _, _, states, sessions, state, document, task, path = sample(tmp_path)
    with pytest.raises(ValueError, match="Arbeitsstand"):
        sessions.create(state["id"], 1, "Ergebnis", "", "Weiter", ["fremd"], [], [])
    with pytest.raises(ValueError, match="verschiedene"):
        sessions.create(state["id"], 1, "Ergebnis", "", "Weiter",
                        [document["id"], document["id"]], [], [])
    with pytest.raises(ValueError, match="Pflicht"):
        sessions.create(state["id"], 1, " ", "", "Weiter", [], [], [])
    path.write_text("Verändert", encoding="utf-8")
    with pytest.raises(WorkStateConflict, match="Datei"):
        sessions.create(state["id"], 1, "Ergebnis", "", "Weiter", [document["id"]], [], [])
    states.update(state["id"], 1, "Atlas", "", "", "Weiter", [], [], [])
    with pytest.raises(WorkStateConflict, match="zwischenzeitlich"):
        sessions.create(state["id"], 1, "Ergebnis", "", "Weiter", [], [task["id"]], [])
    with pytest.raises(LookupError, match="Sitzungsnotiz"):
        sessions.delete(state["id"], "fehlend")


def test_session_rejects_task_deleted_before_capture(tmp_path):
    _, _, tasks, _, sessions, state, _, task, _ = sample(tmp_path)
    tasks.delete(task["id"])
    with pytest.raises(WorkStateConflict, match="Aufgabe"):
        sessions.create(state["id"], state["version"], "Ergebnis", "", "Weiter",
                        [], [task["id"]], [])


def test_session_notes_survive_backup_and_delete_with_state(tmp_path):
    db, _, _, states, sessions, state, document, task, _ = sample(tmp_path)
    note = add_note(sessions, state, document, task)
    archive = tmp_path / "backup.zip"
    assert create_backup(db.path.parent, archive)["schema"] == 64
    restored_root = tmp_path / "restored"
    restore_backup(archive, restored_root)
    restored_db = Database(restored_root / "jarvis.db")
    restored_states = WorkStateStore(restored_db, DocumentStore(restored_db), TaskStore(restored_db))
    restored_sessions = WorkSessionStore(restored_db, restored_states)
    assert restored_sessions.get(state["id"], note["id"])["result"] == "Angebot durchgesehen"
    states.delete(state["id"], state["version"])
    with db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM work_sessions").fetchone()[0] == 0


def test_schema_63_migrates_session_notes_without_losing_projects(tmp_path):
    db, _, _, _, _, state, _, _, _ = sample(tmp_path)
    with db.connect() as connection:
        connection.execute("DROP TABLE work_sessions")
        connection.execute("PRAGMA user_version=63")
    migrated = Database(db.path)
    with migrated.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 64
        assert connection.execute("SELECT project FROM work_states WHERE id=?",
                                  (state["id"],)).fetchone()[0] == "Atlas"
        assert connection.execute("SELECT count(*) FROM work_sessions").fetchone()[0] == 0
    Database(db.path)


def test_session_api_create_read_delete_and_conflicts(tmp_path):
    settings = Settings(JARVIS_DATA_DIR=tmp_path / "data", JARVIS_PROVIDER="ollama")
    with TestClient(create_app(settings)) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        state = client.post("/work-states", headers=headers,
                            json={"project": "Atlas", "confirmed": "", "open_questions": "",
                                  "next_step": "Weiter", "document_ids": [], "task_ids": [],
                                  "links": ["https://example.org/plan"]}).json()
        url = f"/work-states/{state['id']}/sessions"
        body = {"expected_state_version": 1, "result": "Akte geprüft",
                "open_questions": "Rückfrage offen", "next_step": "Maria fragen",
                "document_ids": [], "task_ids": [], "links": ["https://example.org/plan"]}
        created = client.post(url, headers=headers, json=body)
        assert created.status_code == 201
        note = created.json()
        assert client.get(url).json()[0]["id"] == note["id"]
        assert client.post(url, headers=headers, json={**body, "links": ["https://evil.invalid"]}).status_code == 422
        update = client.put(f"/work-states/{state['id']}", headers=headers,
                            json={"project": "Atlas", "confirmed": "", "open_questions": "",
                                  "next_step": "Neu", "document_ids": [], "task_ids": [],
                                  "links": [], "expected_version": 1})
        assert update.status_code == 200
        assert client.post(url, headers=headers, json=body).status_code == 409
        assert client.get(url).json()[0]["links"][0]["in_state"] is False
        assert client.delete(f"{url}/{note['id']}", headers=headers).status_code == 200
        assert client.get(url).json() == []
        assert client.get("/work-states/fehlend/sessions").status_code == 404
        assert client.delete(f"{url}/{note['id']}", headers=headers).status_code == 404
