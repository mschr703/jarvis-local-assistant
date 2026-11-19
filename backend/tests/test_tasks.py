from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.tasks import TaskStore, resolve_due


def test_dst_gap_and_ambiguous_hour():
    with pytest.raises(ValueError, match="existiert"):
        resolve_due("2027-03-28T02:30", "Europe/Paris")
    with pytest.raises(ValueError, match="zweimal"):
        resolve_due("2027-10-31T02:30", "Europe/Paris")
    first, _ = resolve_due("2027-10-31T02:30", "Europe/Paris", 0)
    second, _ = resolve_due("2027-10-31T02:30", "Europe/Paris", 1)
    assert (datetime.fromisoformat(second) - datetime.fromisoformat(first)).total_seconds() == 3600
    with pytest.raises(ValueError, match="Format"):
        resolve_due("2027-11-01T08:00+01:00", "Europe/Paris")


@pytest.mark.parametrize("local_due", [
    "2027-11-01", "2027-11-01T08", "2027-11-01 08:00",
    "2027-11-01T08:00:00", "2027-11-01T08:00Z",
])
def test_due_requires_explicit_local_minute(local_due):
    with pytest.raises(ValueError, match="Format"):
        resolve_due(local_due, "Europe/Paris")


def test_boolean_fold_does_not_mean_second_occurrence():
    with pytest.raises(ValueError, match="fold"):
        resolve_due("2027-10-31T02:30", "Europe/Paris", True)


def test_task_list_distinguishes_both_wintertime_occurrences(tmp_path):
    store = TaskStore(Database(tmp_path / "jarvis.db"))
    first = store.create("Erstes", "", "2027-10-31T02:30", "Europe/Paris", fold=0)
    second = store.create("Zweites", "", "2027-10-31T02:30", "Europe/Paris", fold=1)
    listed = {task["id"]: task for task in store.list_tasks()}
    assert listed[first["id"]]["due_offset"] == "+02:00"
    assert listed[first["id"]]["due_fold"] == 0
    assert listed[second["id"]]["due_offset"] == "+01:00"
    assert listed[second["id"]]["due_fold"] == 1
    assert listed[first["id"]]["reminder_job_id"] != listed[second["id"]]["reminder_job_id"]
    updated = store.update(second["id"], "Zweites", "Notiz", second["due_local"],
                           "Europe/Paris", fold=1)
    assert updated["due_utc"] == second["due_utc"]
    with store.db.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM reminder_jobs WHERE task_id=?",
                          (second["id"],)).fetchone()[0] == 1


def test_due_job_catch_up_exactly_once_across_restart(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    store = TaskStore(db)
    task = store.create("Abgabe", "Test", "2027-01-05T09:00", "Europe/Paris")
    assert task["due_utc"] == "2027-01-05T08:00:00+00:00"
    assert task["reminder_status"] == "pending"
    assert task["reminder_job_id"]
    assert store.fire_due("2027-01-05T07:59:00+00:00") == 0
    assert store.fire_due("2027-01-05T08:10:00+00:00") == 1
    restarted = TaskStore(Database(tmp_path / "jarvis.db"))
    assert restarted.fire_due("2027-01-05T08:11:00+00:00") == 0
    notifications = restarted.notifications()
    assert len(notifications) == 1
    assert notifications[0]["task_id"] == task["id"]
    assert notifications[0]["due_utc"] == task["due_utc"]
    assert notifications[0]["created_at"] != notifications[0]["due_utc"]
    assert restarted.get_task(task["id"])["reminder_status"] == "fired"
    assert restarted.get_task(task["id"])["reminder_job_id"] == task["reminder_job_id"]
    restarted.mark_read(notifications[0]["id"])
    assert restarted.notifications()[0]["read_at"] is not None


def test_task_api_persists_and_completion_cancels_job(tmp_path):
    settings = Settings(JARVIS_DATA_DIR=tmp_path)
    with TestClient(create_app(settings)) as client:
        token = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": token}
        response = client.post("/tasks", json={
            "title": "Vorbereitung", "notes": "Quelle: Gespräch", "local_due": "2027-02-01T10:30",
            "timezone": "Europe/Paris",
        }, headers=headers)
        assert response.status_code == 200, response.text
        task = response.json()
        assert len(client.get("/tasks").json()) == 1
        completed = client.post(f"/tasks/{task['id']}/complete", headers=headers).json()
        assert completed["status"] == "done"
        assert completed["reminder_status"] == "cancelled"
    with TestClient(create_app(settings)) as restarted:
        restarted.get("/session")
        assert restarted.get("/tasks").json()[0]["status"] == "done"
        assert restarted.app.state.tasks.fire_due("2027-02-02T00:00:00+00:00") == 0
        assert restarted.get("/notifications").json() == []


def test_due_preview_resolves_dst_without_creating_task(tmp_path):
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path))) as client:
        token = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": token}
        preview = {"local_due": "2027-10-31T02:30", "timezone": "Europe/Paris"}
        ambiguous = client.post("/tasks/preview", json=preview, headers=headers)
        assert ambiguous.status_code == 422 and "zweimal" in ambiguous.json()["detail"]
        second = client.post("/tasks/preview", json={**preview, "fold": 1}, headers=headers)
        assert second.status_code == 200
        assert second.json()["due_utc"] == "2027-10-31T01:30:00+00:00"
        assert second.json()["offset"] == "+0100"
        assert second.json()["fold"] == 1
        gap = client.post("/tasks/preview", json={
            "local_due": "2027-03-28T02:30", "timezone": "Europe/Paris",
        }, headers=headers)
        assert gap.status_code == 422 and "existiert" in gap.json()["detail"]
        date_only = client.post("/tasks", json={
            "title": "Keine Uhrzeit", "local_due": "2027-11-01", "timezone": "Europe/Paris",
        }, headers=headers)
        assert date_only.status_code == 422 and "Format" in date_only.json()["detail"]
        boolean_fold = client.post("/tasks/preview", json={
            **preview, "fold": True,
        }, headers=headers)
        assert boolean_fold.status_code == 422
        assert client.get("/tasks").json() == []


def test_health_exposes_reminder_worker_failure(tmp_path):
    app = create_app(Settings(JARVIS_DATA_DIR=tmp_path))
    with TestClient(app) as client:
        app.state.worker_error = "RuntimeError"
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "degraded"
        assert "Erinnerungsdienst" in response.json()["message"]


def test_editing_same_ambiguous_due_preserves_fold_and_one_reminder(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    store = TaskStore(db)
    task = store.create("Termin", "vorher", "2027-10-31T02:30", "Europe/Paris", fold=1)
    original_due = task["due_utc"]
    edited = store.update(task["id"], "Termin", "nachher", task["due_local"], "Europe/Paris")
    assert edited["due_utc"] == original_due
    with db.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM reminder_jobs WHERE task_id=?",
                                   (task["id"],)).fetchone()[0]
    assert count == 1
    assert store.fire_due("2027-10-31T02:00:00+00:00") == 1
    store.update(task["id"], "Termin", "nach Erinnerung", task["due_local"], "Europe/Paris")
    assert store.fire_due("2027-10-31T03:00:00+00:00") == 0
    assert len(store.notifications()) == 1


def test_task_list_puts_open_due_tasks_before_undated_and_done(tmp_path):
    store = TaskStore(Database(tmp_path / "jarvis.db"))
    done = store.create("Erledigt", "", None, "Europe/Paris")
    store.complete(done["id"])
    store.create("Ohne Termin", "", None, "Europe/Paris")
    store.create("Mit Termin", "", "2027-10-31T04:00", "Europe/Paris")
    assert [task["title"] for task in store.list_tasks()] == [
        "Mit Termin", "Ohne Termin", "Erledigt",
    ]


def test_due_change_shows_new_job_and_cancels_old_job(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    store = TaskStore(db)
    task = store.create("Termin", "", "2027-11-01T09:00", "Europe/Paris")
    changed = store.update(task["id"], "Termin", "", "2027-11-02T09:00", "Europe/Paris")
    assert changed["reminder_status"] == "pending"
    assert changed["reminder_job_id"] != task["reminder_job_id"]
    with db.connect() as connection:
        old = connection.execute("SELECT status FROM reminder_jobs WHERE id=?",
                                 (task["reminder_job_id"],)).fetchone()
    assert old["status"] == "cancelled"
