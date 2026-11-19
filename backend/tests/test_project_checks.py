from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.project_checks import ProjectCheckStore
from jarvis.tasks import TaskStore
from jarvis.work_states import WorkStateConflict, WorkStateStore


@pytest.fixture
def project(tmp_path):
    source = tmp_path / "sources"
    source.mkdir()
    file = source / "plan.md"
    file.write_text("# Plan\nBestätigter Stand.\n", encoding="utf-8")
    root = tmp_path / "data"
    db = Database(root / "jarvis.db")
    documents = DocumentStore(db)
    folder = documents.add_folder(str(source), "local_only")
    documents.index_folder(folder["id"])
    document = documents.documents()[0]
    tasks = TaskStore(db)
    previous = tasks.create("Vorarbeit", "", None, "Europe/Paris")
    states = WorkStateStore(db, documents, tasks)
    state = states.create("Atlas", "Ziel bestätigt", "Budget offen", "Maria fragen",
                          [document["id"]], [previous["id"]], [])
    return db, documents, tasks, states, ProjectCheckStore(db, states, tasks), file, document, state


def test_project_check_blocks_stale_source_until_explicit_confirmation(project):
    _, documents, tasks, states, checks, file, document, state = project
    initial = checks.check(state["id"])
    assert initial["ready"] and initial["open_tasks"] == 1
    assert initial["state"]["confirmed"] == "Ziel bestätigt"
    assert initial["state"]["open_questions"] == "Budget offen"
    file.write_text("# Plan\nBestätigter Stand geändert.\n", encoding="utf-8")
    stale = checks.check(state["id"])
    assert not stale["ready"] and "geaendert" in stale["blockers"][0]
    with pytest.raises(ValueError, match="Projektquellen prüfen"):
        checks.prepare_next_task(state["id"], 1)
    assert len(tasks.list_tasks()) == 1
    with pytest.raises(WorkStateConflict, match="Index aktualisieren"):
        states.confirm_document(state["id"], document["id"], 1)
    documents.index_folder(document["folder_id"])
    confirmed = states.confirm_document(state["id"], document["id"], 1)
    assert confirmed["version"] == 2
    assert checks.check(state["id"])["ready"]
    with pytest.raises(WorkStateConflict, match="zwischenzeitlich"):
        states.confirm_document(state["id"], document["id"], 1)
    prepared = checks.prepare_next_task(state["id"], 2)
    assert prepared["task"]["title"] == "Maria fragen"
    assert prepared["check"]["already_prepared"]
    assert prepared["check"]["state"]["version"] == 3


def test_parallel_prepare_restart_completion_and_new_step(project, tmp_path):
    db, _, tasks, _, checks, _, _, state = project
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: checks.prepare_next_task(state["id"], 1), range(2)))
    task_id = results[0]["task_id"]
    assert all(result["task_id"] == task_id for result in results)
    assert sum(not result["reused"] for result in results) == 1
    assert len(tasks.list_tasks()) == 2
    tasks.complete(task_id)
    restarted_db = Database(db.path)
    restarted_tasks = TaskStore(restarted_db)
    restarted_states = WorkStateStore(restarted_db, DocumentStore(restarted_db), restarted_tasks)
    restarted = ProjectCheckStore(restarted_db, restarted_states, restarted_tasks)
    again = restarted.prepare_next_task(state["id"], 1)
    assert again["reused"] and again["task"]["status"] == "done"
    assert len(restarted_tasks.list_tasks()) == 2
    current = restarted_states.get(state["id"])
    updated = restarted_states.update(state["id"], current["version"], current["project"],
                                      current["confirmed"], current["open_questions"],
                                      "Angebot schreiben", [item["id"] for item in current["documents"]],
                                      [item["id"] for item in current["tasks"]], [])
    new = restarted.prepare_next_task(state["id"], updated["version"])
    assert new["task_id"] != task_id and not new["reused"]
    assert len(restarted_tasks.list_tasks()) == 3
    archive = tmp_path / "project-backup.zip"
    assert create_backup(db.path.parent, archive)["schema"] == 61
    target = tmp_path / "restored"
    restore_backup(archive, target)
    restored_db = Database(target / "jarvis.db")
    restored_tasks = TaskStore(restored_db)
    restored_states = WorkStateStore(restored_db, DocumentStore(restored_db), restored_tasks)
    restored = ProjectCheckStore(restored_db, restored_states, restored_tasks)
    assert restored.check(state["id"])["prepared_task_id"] == new["task_id"]
    assert restored.prepare_next_task(state["id"], updated["version"])["reused"]


def test_existing_linked_task_is_reused_and_missing_source_blocks(project):
    _, _, tasks, states, checks, file, document, state = project
    existing = tasks.create("Bereits geplant", "", None, "Europe/Paris")
    updated = states.update(state["id"], 1, "Atlas", "Ziel bestätigt", "",
                            "Bereits geplant", [document["id"]],
                            [state["tasks"][0]["id"], existing["id"]], [])
    reused = checks.prepare_next_task(state["id"], updated["version"])
    assert reused["reused"] and reused["task_id"] == existing["id"]
    assert len(tasks.list_tasks()) == 2
    file.unlink()
    assert not checks.check(state["id"])["ready"]
    assert checks.prepare_next_task(state["id"], updated["version"])["reused"]


def test_project_check_api_and_version_conflict(tmp_path):
    source = tmp_path / "sources"
    source.mkdir()
    (source / "plan.md").write_text("Projektquelle", encoding="utf-8")
    settings = Settings(JARVIS_DATA_DIR=tmp_path / "data", JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        folder = client.post("/folders", headers=headers,
                             json={"path": str(source), "policy": "local_only"}).json()
        client.post(f"/folders/{folder['id']}/index", headers=headers).raise_for_status()
        document = client.get("/documents").json()[0]
        state = client.post("/work-states", headers=headers, json={
            "project": "Atlas", "confirmed": "Quelle gelesen", "open_questions": "",
            "next_step": "Maria fragen", "document_ids": [document["id"]],
            "task_ids": [], "links": [],
        }).json()
        path = f"/work-states/{state['id']}"
        assert client.get(path + "/check").json()["ready"]
        assert client.get("/work-states/missing/check").status_code == 404
        assert client.post(path + "/next-task", json={"expected_version": 1}).status_code == 403
        assert client.post(path + "/next-task", headers=headers,
                           json={"expected_version": 2}).status_code == 409
        first = client.post(path + "/next-task", headers=headers,
                            json={"expected_version": 1})
        assert first.status_code == 200, first.text
        assert first.json()["check"]["already_prepared"]
        repeated = client.post(path + "/next-task", headers=headers,
                               json={"expected_version": 1})
        assert repeated.status_code == 200 and repeated.json()["reused"]
        assert repeated.json()["task_id"] == first.json()["task_id"]
