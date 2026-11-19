from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.tasks import TaskStore
from jarvis.work_states import WorkStateConflict, WorkStateStore


@pytest.fixture
def environment(tmp_path):
    source = tmp_path / "sources"
    source.mkdir()
    path = source / "plan.md"
    path.write_text("# Plan\nHeute beginnen.\n", encoding="utf-8")
    db = Database(tmp_path / "data" / "jarvis.db")
    documents = DocumentStore(db)
    folder = documents.add_folder(str(source), "local_only")
    documents.index_folder(folder["id"])
    document = documents.documents()[0]
    tasks = TaskStore(db)
    task = tasks.create("Angebot prüfen", "", None, "Europe/Paris")
    return db, documents, tasks, WorkStateStore(db, documents, tasks), path, document, task


def create_state(store, document, task):
    return store.create("Atlas", "Entwurf geprüft", "Budget offen", "Maria fragen",
                        [document["id"]], [task["id"]], ["https://example.com/projekt"])


def test_work_state_persists_separate_claims_and_detects_file_changes(environment):
    db, documents, tasks, store, path, document, task = environment
    saved = create_state(store, document, task)
    assert saved["version"] == 1
    assert saved["confirmed"] == "Entwurf geprüft"
    assert saved["open_questions"] == "Budget offen"
    assert saved["next_step"] == "Maria fragen"
    assert saved["documents"][0]["status"] == "unveraendert"
    assert saved["documents"][0]["sha256"] == document["sha256"]
    assert saved["tasks"][0]["status"] == "open"

    restarted = WorkStateStore(Database(db.path), DocumentStore(db), TaskStore(db))
    assert restarted.get(saved["id"])["project"] == "Atlas"
    path.write_text("Nachträglich geändert", encoding="utf-8")
    assert restarted.get(saved["id"])["documents"][0]["status"] == "geaendert"
    path.unlink()
    assert restarted.get(saved["id"])["documents"][0]["status"] == "fehlt"
    documents.set_excluded(document["id"], True)
    assert restarted.get(saved["id"])["documents"][0]["status"] == "ausgeschlossen"
    tasks.delete(task["id"])
    assert restarted.get(saved["id"])["tasks"][0]["status"] == "geloescht"


def test_work_state_update_keeps_old_refs_and_checks_version(environment):
    _, documents, tasks, store, _, document, task = environment
    saved = create_state(store, document, task)
    documents.set_excluded(document["id"], True)
    tasks.delete(task["id"])
    updated = store.update(saved["id"], 1, "Atlas", "Fertig", "", "Nächster Schritt",
                           [document["id"]], [task["id"]], [])
    assert updated["version"] == 2
    assert updated["documents"][0]["status"] == "ausgeschlossen"
    assert updated["tasks"][0]["status"] == "geloescht"
    with pytest.raises(WorkStateConflict, match="zwischenzeitlich"):
        store.update(saved["id"], 1, "Alt", "", "", "Test", [], [], [])
    with pytest.raises(WorkStateConflict, match="zwischenzeitlich"):
        store.delete(saved["id"], 1)
    store.delete(saved["id"], 2)
    assert store.get(saved["id"]) is None


def test_work_state_requires_fresh_granted_document_and_open_task(environment):
    _, documents, tasks, store, path, document, task = environment
    path.write_text("Geändert ohne Index", encoding="utf-8")
    with pytest.raises(WorkStateConflict, match="Index aktualisieren"):
        create_state(store, document, task)
    documents.index_folder(document["folder_id"])
    documents.set_excluded(document["id"], True)
    with pytest.raises(ValueError, match="nicht mehr freigegeben"):
        create_state(store, document, task)
    documents.set_excluded(document["id"], False)
    documents.index_folder(document["folder_id"])
    tasks.complete(task["id"])
    with pytest.raises(ValueError, match="offene Aufgaben"):
        create_state(store, document, task)


@pytest.mark.parametrize("url", ["file:///secret", "https://user:pass@example.com",
                                  "http://example.com/a b", "https://[broken", "https://example.com:bad"])
def test_work_state_rejects_invalid_links(environment, url):
    _, _, _, store, _, _, _ = environment
    with pytest.raises(ValueError):
        store.create("Projekt", "", "", "Weiter", [], [], [url])


def test_work_state_backup_restore_keeps_snapshot(environment, tmp_path):
    db, _, _, store, path, document, task = environment
    saved = create_state(store, document, task)
    archive = tmp_path / "work-backup.zip"
    assert create_backup(db.path.parent, archive)["schema"] == 59
    restored_path = tmp_path / "restored"
    restore_backup(archive, restored_path)
    restored_db = Database(restored_path / "jarvis.db")
    restored = WorkStateStore(restored_db, DocumentStore(restored_db), TaskStore(restored_db))
    assert restored.get(saved["id"])["documents"][0]["status"] == "unveraendert"
    assert Path(path).is_file()


def test_work_state_api_create_reload_update_delete_and_conflict(tmp_path):
    source = tmp_path / "sources"
    source.mkdir()
    path = source / "notiz.md"
    path.write_text("Quelle", encoding="utf-8")
    settings = Settings(JARVIS_DATA_DIR=tmp_path / "data", JARVIS_PROVIDER="ollama")
    with TestClient(create_app(settings)) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        folder = client.post("/folders", headers=headers,
                             json={"path": str(source), "policy": "local_only"}).json()
        client.post(f"/folders/{folder['id']}/index", headers=headers)
        document = client.get("/documents").json()[0]
        task = client.post("/tasks", headers=headers,
                           json={"title": "Prüfen", "notes": "", "local_due": None,
                                 "timezone": "Europe/Paris"}).json()
        payload = {"project": "Atlas", "confirmed": "Quelle gelesen", "open_questions": "",
                   "next_step": "Prüfen", "document_ids": [document["id"]],
                   "task_ids": [task["id"]], "links": ["https://example.com/"]}
        assert client.post("/work-states", json=payload).status_code == 403
        created = client.post("/work-states", headers=headers, json=payload)
        assert created.status_code == 200
        state = created.json()
        assert client.get("/work-states").json()[0]["id"] == state["id"]
        assert client.get(f"/work-states/{state['id']}").json()["project"] == "Atlas"
        stale = client.put(f"/work-states/{state['id']}", headers=headers,
                           json={**payload, "expected_version": 2})
        assert stale.status_code == 409
        updated = client.put(f"/work-states/{state['id']}", headers=headers,
                             json={**payload, "confirmed": "Fertig", "expected_version": 1})
        assert updated.status_code == 200 and updated.json()["version"] == 2
        assert client.delete(f"/work-states/{state['id']}?version=1", headers=headers).status_code == 409
        assert client.delete(f"/work-states/{state['id']}?version=2", headers=headers).status_code == 200
        assert client.get(f"/work-states/{state['id']}").status_code == 404
