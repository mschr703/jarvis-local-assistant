from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.tasks import ReminderConflict, TaskStore


def store(tmp_path):
    return TaskStore(Database(tmp_path / "jarvis.db"))


def test_high_priority_has_at_most_two_followups_after_read_and_restart(tmp_path):
    tasks = store(tmp_path)
    task = tasks.create("Wichtige Abgabe", "", "2027-01-05T09:00", "Europe/Paris",
                        priority=5)
    assert task["priority"] == 5
    assert tasks.fire_due("2027-01-05T08:00:00+00:00") == 1
    notification = tasks.notifications()[0]
    assert notification["followup_due_utc"] == "2027-01-05T09:00:00+00:00"
    tasks.mark_read(notification["id"])
    restarted = store(tmp_path)
    assert restarted.fire_due("2027-01-05T08:59:59+00:00") == 0
    assert restarted.fire_due("2027-01-05T09:00:00+00:00") == 1
    first = restarted.notifications()[0]
    assert first["id"] == notification["id"] and first["read_at"] is None
    assert first["followup_count"] == 1
    assert first["followup_due_utc"] == "2027-01-05T13:00:00+00:00"
    assert restarted.fire_due("2027-01-05T09:00:00+00:00") == 0
    assert restarted.fire_due("2027-01-05T13:00:00+00:00") == 1
    second = restarted.notifications()[0]
    assert second["followup_count"] == 2 and second["followup_due_utc"] is None
    assert restarted.fire_due("2027-01-10T00:00:00+00:00") == 0
    assert len(restarted.notifications()) == 1


def test_acknowledge_and_task_completion_stop_followups(tmp_path):
    tasks = store(tmp_path)
    first_task = tasks.create("Bestätigen", "", "2027-01-05T09:00", "Europe/Paris",
                              priority=4)
    second_task = tasks.create("Erledigen", "", "2027-01-05T09:00", "Europe/Paris",
                               priority=5)
    assert tasks.fire_due("2027-01-05T08:00:00+00:00") == 2
    notices = {notice["task_id"]: notice for notice in tasks.notifications()}
    acknowledged = tasks.acknowledge(notices[first_task["id"]]["id"])
    assert acknowledged["acknowledged_at"] and acknowledged["read_at"]
    assert acknowledged["followup_due_utc"] is None
    assert tasks.acknowledge(acknowledged["id"])["acknowledged_at"] == acknowledged["acknowledged_at"]
    tasks.complete(second_task["id"])
    completed = next(item for item in tasks.notifications() if item["task_id"] == second_task["id"])
    assert completed["acknowledged_at"] and completed["followup_due_utc"] is None
    assert tasks.fire_due("2027-01-06T00:00:00+00:00") == 0


def test_snooze_crosses_dst_fold_once_and_restarts_followup(tmp_path):
    tasks = store(tmp_path)
    tasks.create("Winterzeit", "", "2027-10-31T02:30", "Europe/Paris",
                 fold=0, priority=5)
    assert tasks.fire_due("2027-10-31T00:30:00+00:00") == 1
    original = tasks.notifications()[0]
    snoozed = tasks.snooze(original["id"], 60, "2027-10-31T00:40:00+00:00")
    assert snoozed["snoozed_until"] == "2027-10-31T01:40:00+00:00"
    assert datetime.fromisoformat(snoozed["snoozed_until"]).astimezone(
        ZoneInfo("Europe/Paris")).strftime("%H:%M %z") == "02:40 +0100"
    assert snoozed["read_at"] and snoozed["followup_due_utc"] is None
    assert tasks.fire_due("2027-10-31T01:39:59+00:00") == 0
    assert store(tmp_path).fire_due("2027-10-31T01:40:00+00:00") == 1
    returned = tasks.notifications()[0]
    assert returned["id"] == original["id"] and returned["read_at"] is None
    assert returned["followup_count"] == 0
    assert returned["followup_due_utc"] == "2027-10-31T02:40:00+00:00"
    assert tasks.fire_due("2027-10-31T01:40:00+00:00") == 0


def test_normal_priority_only_repeats_after_manual_snooze(tmp_path):
    tasks = store(tmp_path)
    task = tasks.create("Routine", "", "2027-01-05T09:00", "Europe/Paris")
    assert tasks.fire_due("2027-01-05T08:00:00+00:00") == 1
    notification = tasks.notifications()[0]
    assert notification["priority"] == 3 and notification["followup_due_utc"] is None
    assert tasks.fire_due("2027-01-10T00:00:00+00:00") == 0
    tasks.snooze(notification["id"], 15, "2027-01-10T00:00:00+00:00")
    assert tasks.fire_due("2027-01-10T00:15:00+00:00") == 1
    assert tasks.notifications()[0]["followup_due_utc"] is None
    assert tasks.notifications()[0]["task_id"] == task["id"]


def test_priority_change_and_validation(tmp_path):
    tasks = store(tmp_path)
    with pytest.raises(ValueError, match="Priorität"):
        tasks.create("Ungültig", "", None, "Europe/Paris", priority=True)
    task = tasks.create("Wichtig", "", "2027-01-05T09:00", "Europe/Paris", priority=5)
    tasks.fire_due("2027-01-05T08:00:00+00:00")
    preserved = tasks.update(task["id"], "Wichtig", "neu", task["due_local"],
                             "Europe/Paris")
    assert preserved["priority"] == 5
    lowered = tasks.update(task["id"], "Wichtig", "neu", task["due_local"],
                           "Europe/Paris", priority=2)
    assert lowered["priority"] == 2
    assert tasks.notifications()[0]["followup_due_utc"] is None
    assert tasks.fire_due("2027-01-06T00:00:00+00:00") == 0
    tasks.complete(task["id"])
    tasks.update(task["id"], "Wichtig", "erledigt", task["due_local"],
                 "Europe/Paris", priority=5)
    assert tasks.notifications()[0]["followup_due_utc"] is None


def test_changing_due_stops_old_followup_and_snooze(tmp_path):
    tasks = store(tmp_path)
    task = tasks.create("Neue Frist", "", "2027-01-05T09:00", "Europe/Paris",
                        priority=5)
    tasks.fire_due("2027-01-05T08:00:00+00:00")
    old = tasks.notifications()[0]
    updated = tasks.update(task["id"], "Neue Frist", "", "2027-01-06T09:00",
                           "Europe/Paris", priority=5)
    assert updated["reminder_job_id"] != old["job_id"]
    old_after = tasks.notifications()[0]
    assert old_after["followup_due_utc"] is None and old_after["read_at"]
    with pytest.raises(ReminderConflict, match="veraltete"):
        tasks.snooze(old["id"], 15)
    assert tasks.fire_due("2027-01-05T13:00:00+00:00") == 0
    assert tasks.fire_due("2027-01-06T08:00:00+00:00") == 1
    assert len(tasks.notifications()) == 2


def test_parallel_worker_claims_one_initial_and_one_followup(tmp_path):
    first = store(tmp_path)
    second = store(tmp_path)
    first.create("Einmalig", "", "2027-01-05T09:00", "Europe/Paris", priority=5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda current: current.fire_due("2027-01-05T08:00:00+00:00"),
                               (first, second))) == [0, 1]
        assert sorted(pool.map(lambda current: current.fire_due("2027-01-05T09:00:00+00:00"),
                               (first, second))) == [0, 1]
    assert len(first.notifications()) == 1
    assert first.notifications()[0]["followup_count"] == 1


def test_snooze_conflict_api_and_backup_restore(tmp_path):
    root = tmp_path / "source"
    settings = Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        task = client.post("/tasks", json={"title": "API-Abgabe", "priority": 5,
                                           "local_due": "2027-01-05T09:00",
                                           "timezone": "Europe/Paris"}, headers=headers).json()
        assert task["priority"] == 5
        assert client.app.state.tasks.fire_due("2027-01-05T08:00:00+00:00") == 1
        notification = client.get("/notifications").json()[0]
        assert client.post(f"/notifications/{notification['id']}/snooze",
                           json={"minutes": 0}, headers=headers).status_code == 422
        shifted = client.post(f"/notifications/{notification['id']}/snooze",
                              json={"minutes": 15}, headers=headers)
        assert shifted.status_code == 200
        assert shifted.json()["snoozed_until"]
        assert client.post(f"/notifications/{notification['id']}/ack",
                           headers=headers).status_code == 200
        assert client.post(f"/notifications/{notification['id']}/snooze",
                           json={"minutes": 15}, headers=headers).status_code == 409
        assert client.post("/notifications/not-found/ack", headers=headers).status_code == 404
    archive = tmp_path / "backup.zip"
    assert create_backup(root, archive)["schema"] == 56
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    notices = TaskStore(Database(restored / "jarvis.db")).notifications()
    assert notices[0]["id"] == notification["id"]
    assert notices[0]["acknowledged_at"] and notices[0]["priority"] == 5
    with pytest.raises(ReminderConflict):
        TaskStore(Database(restored / "jarvis.db")).snooze(notification["id"], 15)
