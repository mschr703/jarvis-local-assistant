"""Geplante Projektchecks: lokaler Kalendertag, Pause, Belege und Wiederanlauf."""

import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.project_checks import ProjectCheckStore
from jarvis.project_reviews import ProjectReviewStore
from jarvis.tasks import TaskStore
from jarvis.work_states import WorkStateStore


def local(year, month, day, hour, minute, fold=0):
    return datetime(year, month, day, hour, minute,
                    tzinfo=ZoneInfo("Europe/Paris"), fold=fold).astimezone(UTC)


@pytest.fixture
def project(tmp_path):
    source = tmp_path / "sources"
    source.mkdir()
    file = source / "plan.md"
    file.write_text("# Plan\nFreigegeben.", encoding="utf-8")
    data = tmp_path / "data"
    db = Database(data / "jarvis.db")
    documents = DocumentStore(db)
    folder = documents.add_folder(str(source), "local_only")
    documents.index_folder(folder["id"])
    document = documents.documents()[0]
    tasks = TaskStore(db)
    task = tasks.create("Vorarbeit", "", None, "Europe/Paris")
    states = WorkStateStore(db, documents, tasks)
    state = states.create("Atlas", "Ziel bestätigt", "Budget offen", "Maria fragen",
                          [document["id"]], [task["id"]], [])
    reviews = ProjectReviewStore(db, ProjectCheckStore(db, states, tasks))
    return db, reviews, states, tasks, documents, file, state, task


def test_daily_schedule_dst_only_once_pause_and_resume(project):
    _, reviews, _, _, _, _, state, _ = project
    state_id = state["id"]
    schedule = reviews.configure(state_id, 1, "02:30", "Europe/Paris", False,
                                 now=local(2027, 10, 30, 12, 0))
    assert schedule["next_local_date"] == "2027-10-31"
    assert reviews.tick(local(2027, 10, 31, 2, 29, fold=0)) == []
    first = reviews.tick(local(2027, 10, 31, 2, 30, fold=0))
    assert len(first) == 1 and first[0]["status"] == "succeeded"
    assert first[0]["summary"]["open_questions"] == "Budget offen"
    assert reviews.tick(local(2027, 10, 31, 2, 30, fold=1)) == []
    assert len(reviews.reports(state_id)) == 1
    assert reviews.status(state_id)["next_local_date"] == "2027-11-01"
    reviews.configure(state_id, 1, "02:30", "Europe/Paris", True,
                      now=local(2027, 11, 1, 1, 0))
    assert reviews.tick(local(2027, 11, 2, 12, 0)) == []
    resumed = reviews.configure(state_id, 1, "13:00", "Europe/Paris", False,
                                now=local(2027, 11, 3, 12, 0))
    assert resumed["next_local_date"] == "2027-11-03"
    assert reviews.tick(local(2027, 11, 3, 12, 59)) == []
    assert len(reviews.tick(local(2027, 11, 3, 13, 0))) == 1
    assert len(reviews.reports(state_id)) == 2


def test_source_task_changes_restart_catchup_and_restore(project, tmp_path):
    db, reviews, _, tasks, _, file, state, task = project
    state_id = state["id"]
    reviews.configure(state_id, 2, "08:00", "Europe/Paris", False,
                      now=local(2027, 1, 1, 7, 0))
    first = reviews.tick(local(2027, 1, 1, 8, 0))[0]
    assert first["changed"] == 1 and first["summary"]["open_tasks"] == 1
    assert reviews.tick(local(2027, 1, 2, 18, 0)) == []
    file.write_text("# Plan\nGeändert.", encoding="utf-8")
    tasks.complete(task["id"])
    restarted_db = Database(db.path)
    restarted_tasks = TaskStore(restarted_db)
    restarted_states = WorkStateStore(restarted_db, DocumentStore(restarted_db), restarted_tasks)
    restarted = ProjectReviewStore(restarted_db,
                                   ProjectCheckStore(restarted_db, restarted_states, restarted_tasks))
    missed = restarted.tick(local(2027, 1, 5, 7, 0))
    assert len(missed) == 1 and missed[0]["changed"] == 1
    assert missed[0]["summary"]["done_tasks"] == 1
    assert "geaendert" in missed[0]["summary"]["blockers"][0]
    assert restarted.tick(local(2027, 1, 5, 20, 0)) == []
    assert restarted.status(state_id)["next_local_date"] == "2027-01-07"
    archive = tmp_path / "reviews.zip"
    assert create_backup(db.path.parent, archive)["schema"] == 56
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    restored_db = Database(restored / "jarvis.db")
    restored_states = WorkStateStore(restored_db, DocumentStore(restored_db), TaskStore(restored_db))
    restored_reviews = ProjectReviewStore(restored_db, ProjectCheckStore(
        restored_db, restored_states, TaskStore(restored_db)))
    assert len(restored_reviews.reports(state_id)) == 2
    assert restored_reviews.status(state_id)["next_local_date"] == "2027-01-07"


def test_manual_report_change_detection_and_recover_incomplete(project):
    db, reviews, _, _, _, _, state, _ = project
    state_id = state["id"]
    first = reviews.run_now(state_id, datetime(2027, 1, 1, tzinfo=UTC))
    assert first["changed"] == 1
    repeated = reviews.run_now(state_id, datetime(2027, 1, 1, tzinfo=UTC))
    assert repeated["id"] != first["id"] and repeated["changed"] == 0
    assert len(reviews.reports(state_id)) == 2
    with db.connect() as connection:
        connection.execute("UPDATE project_reviews SET status='running' WHERE id=?", (first["id"],))
    reviews.recover_incomplete()
    assert next(item for item in reviews.reports(state_id) if item["id"] == first["id"])["status"] == "failed"
    retried = reviews.run_now(state_id, datetime(2027, 1, 1, tzinfo=UTC))
    assert retried["status"] == "succeeded" and retried["changed"] == 0
    assert len(reviews.reports(state_id)) == 3


def test_schedule_validation_and_api_csrf(project):
    db, reviews, _, _, _, _, state, _ = project
    for days, clock, timezone in ((0, "08:00", "Europe/Paris"),
                                  (1, "25:00", "Europe/Paris"),
                                  (1, "08:00", "Wrong/Zone")):
        with pytest.raises(ValueError):
            reviews.configure(state["id"], days, clock, timezone, False)
    app = create_app(Settings(JARVIS_DATA_DIR=db.path.parent))
    with TestClient(app) as client:
        payload = {"interval_days": 3, "local_time": "09:00",
                   "timezone": "Europe/Paris", "paused": False}
        url = f"/work-states/{state['id']}/reviews"
        assert client.put(url + "/schedule", json=payload).status_code == 401
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        configured = client.put(url + "/schedule", json=payload, headers=headers)
        assert configured.status_code == 200, configured.text
        assert client.get(url + "/schedule").json()["interval_days"] == 3
        run = client.post(url + "/run", headers=headers)
        assert run.status_code == 200 and run.json()["status"] == "succeeded"
        assert len(client.get(url).json()) == 1
    assert len(reviews.reports(state["id"])) == 1


def test_spring_gap_runs_once_after_clock_jumps(project):
    _, reviews, _, _, _, _, state, _ = project
    reviews.configure(state["id"], 1, "02:30", "Europe/Paris", False,
                      now=local(2027, 3, 27, 12, 0))
    assert reviews.tick(local(2027, 3, 28, 1, 59)) == []
    assert len(reviews.tick(local(2027, 3, 28, 3, 0))) == 1
    assert reviews.tick(local(2027, 3, 28, 3, 1)) == []


def test_real_app_worker_catches_up_once_across_restart(project):
    db, reviews, _, _, _, _, state, _ = project
    reviews.configure(state["id"], 1, "00:00", "Europe/Paris", False)
    yesterday = (datetime.now(ZoneInfo("Europe/Paris")).date() - timedelta(days=1)).isoformat()
    with db.connect() as connection:
        connection.execute("UPDATE project_review_schedules SET next_local_date=? WHERE state_id=?",
                           (yesterday, state["id"]))
    settings = Settings(JARVIS_DATA_DIR=db.path.parent, JARVIS_OLLAMA_MODEL="fixture")
    for _ in range(2):
        with TestClient(create_app(settings)):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                current = reviews.reports(state["id"])
                if len(current) == 1 and current[0]["status"] == "succeeded":
                    break
                time.sleep(0.05)
            assert len(reviews.reports(state["id"])) == 1
            assert reviews.reports(state["id"])[0]["status"] == "succeeded"
    assert reviews.status(state["id"])["last_status"] == "succeeded"


def test_worker_error_is_reported_and_manual_retry_succeeds(project, monkeypatch):
    _, reviews, _, _, _, _, state, _ = project
    reviews.configure(state["id"], 1, "08:00", "Europe/Paris", False,
                      now=local(2027, 1, 1, 7, 0))
    original = reviews.checks.check

    def broken(_state_id):
        raise OSError("Quelle zeitweise nicht lesbar")

    monkeypatch.setattr(reviews.checks, "check", broken)
    failed = reviews.tick(local(2027, 1, 1, 8, 0))[0]
    assert failed["status"] == "failed" and "nicht lesbar" in failed["error"]
    assert reviews.status(state["id"])["last_status"] == "failed"
    unchanged = reviews.configure(state["id"], 1, "08:00", "Europe/Paris", False,
                                  now=local(2027, 1, 1, 8, 1))
    assert unchanged["last_status"] == "failed"
    changed = reviews.configure(state["id"], 1, "09:00", "Europe/Paris", False,
                                now=local(2027, 1, 1, 8, 1))
    assert changed["last_status"] is None and changed["last_error"] is None
    monkeypatch.setattr(reviews.checks, "check", original)
    fixed = reviews.run_now(state["id"], local(2027, 1, 1, 8, 1))
    assert fixed["status"] == "succeeded"
    assert reviews.status(state["id"])["last_status"] == "succeeded"
    assert reviews.status(state["id"])["last_error"] is None


def test_schedule_changed_between_due_scan_and_claim_does_not_run(project, monkeypatch):
    _, reviews, _, _, _, _, state, _ = project
    state_id = state["id"]
    reviews.configure(state_id, 1, "08:00", "Europe/Paris", False,
                      now=local(2027, 1, 1, 7, 0))
    original = reviews._run

    def change_before_claim(*args):
        reviews.configure(state_id, 1, "12:00", "Europe/Paris", False,
                          now=local(2027, 1, 1, 7, 59))
        return original(*args)

    monkeypatch.setattr(reviews, "_run", change_before_claim)
    assert reviews.tick(local(2027, 1, 1, 8, 0)) == []
    assert reviews.reports(state_id) == []
