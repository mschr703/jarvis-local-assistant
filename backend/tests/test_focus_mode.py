"""Fokuszeiten, priorisierte Sichtbarkeit und persistente Sammlung."""

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.focus_mode import FocusConflict, FocusStore
from jarvis.tasks import TaskStore


def stores(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    return FocusStore(db), TaskStore(db)


def test_manual_focus_defers_normal_and_keeps_important_visible(tmp_path):
    focus, tasks = stores(tmp_path)
    normal = tasks.create("Normal", "", "2027-01-05T09:00", "Europe/Paris", priority=3)
    important = tasks.create("Wichtig", "", "2027-01-05T09:00", "Europe/Paris", priority=5)
    focus.update(True, False, "09:00", "17:00", "Europe/Paris", 1,
                 "2027-01-05T07:00:00+00:00")
    assert tasks.fire_due("2027-01-05T08:00:00+00:00") == 2
    notices = focus.notifications(tasks.notifications(), "2027-01-05T08:01:00+00:00")
    low = next(item for item in notices if item["task_id"] == normal["id"])
    high = next(item for item in notices if item["task_id"] == important["id"])
    assert low["focus_suppressed"] and low["focus_deferred_at"]
    assert not high["focus_suppressed"] and high["focus_deferred_at"] is None
    assert len(tasks.notifications()) == 2
    reopened = FocusStore(Database(tmp_path / "jarvis.db"))
    assert reopened.state()["manual_enabled"] is True
    reopened.update(False, False, "09:00", "17:00", "Europe/Paris", 2,
                    "2027-01-05T08:02:00+00:00")
    after = reopened.notifications(tasks.notifications(), "2027-01-05T08:03:00+00:00")
    assert not any(item["focus_suppressed"] for item in after)
    assert next(item for item in after if item["task_id"] == normal["id"])["focus_deferred_at"]
    tasks.mark_read(low["id"])
    assert next(item for item in tasks.notifications() if item["id"] == low["id"])["read_at"]


def test_focus_schedule_handles_overnight_and_both_autumn_hours(tmp_path):
    focus, _ = stores(tmp_path)
    focus.update(False, True, "22:00", "07:00", "Europe/Paris", 1,
                 "2027-01-05T00:00:00+00:00")
    assert focus.state("2027-01-05T22:00:00+01:00")["active"]
    assert focus.state("2027-01-06T06:59:00+01:00")["active"]
    assert not focus.state("2027-01-06T07:00:00+01:00")["active"]
    focus.update(False, True, "02:00", "03:00", "Europe/Paris", 2,
                 "2027-10-30T00:00:00+00:00")
    assert focus.state("2027-10-31T02:30:00+02:00")["active"]
    assert focus.state("2027-10-31T02:30:00+01:00")["active"]
    assert not focus.state("2027-10-31T03:00:00+01:00")["active"]
    assert not focus.state("2027-03-28T03:00:00+02:00")["active"]
    with pytest.raises(FocusConflict):
        focus.update(True, False, "09:00", "17:00", "Europe/Paris", 2)
    with pytest.raises(ValueError, match="verschieden"):
        focus.update(True, False, "09:00", "09:00", "Europe/Paris", 3)
    with pytest.raises(ValueError, match="IANA"):
        focus.update(True, False, "09:00", "17:00", "Invalid/Zone", 3)


def test_existing_notice_enters_focus_and_snooze_redelivery_is_new(tmp_path):
    focus, tasks = stores(tmp_path)
    task = tasks.create("Später", "", "2027-01-05T09:00", "Europe/Paris", priority=2)
    assert tasks.fire_due("2027-01-05T08:00:00+00:00") == 1
    assert tasks.notifications()[0]["focus_deferred_at"] is None
    focus.update(True, False, "09:00", "17:00", "Europe/Paris", 1,
                 "2027-01-05T08:01:00+00:00")
    notice = focus.notifications(tasks.notifications(), "2027-01-05T08:02:00+00:00")[0]
    assert notice["task_id"] == task["id"] and notice["focus_suppressed"]
    shifted = tasks.snooze(notice["id"], 15, "2027-01-05T08:03:00+00:00")
    assert shifted["focus_deferred_at"] is None
    focus.update(False, False, "09:00", "17:00", "Europe/Paris", 2,
                 "2027-01-05T08:04:00+00:00")
    assert tasks.fire_due(shifted["snoozed_until"]) == 1
    after = focus.notifications(tasks.notifications(), shifted["snoozed_until"])[0]
    assert not after["focus_suppressed"] and after["focus_deferred_at"] is None


def test_focus_api_priority_change_and_backup_restore(tmp_path):
    root = tmp_path / "source"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root,
                                       JARVIS_OLLAMA_MODEL="fixture"))) as client:
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        state = client.get("/focus").json()
        assert not state["active"] and state["version"] == 1
        payload = {"manual_enabled": True, "schedule_enabled": False,
                   "start_time": "09:00", "end_time": "17:00",
                   "timezone": "Europe/Paris", "expected_version": 1}
        assert client.put("/focus", json=payload, headers=headers).json()["active"]
        assert client.put("/focus", json=payload, headers=headers).status_code == 409
        assert client.put("/focus", json=dict(payload, manual_enabled="yes",
                                               expected_version=2), headers=headers).status_code == 422
        task = client.post("/tasks", headers=headers,
                           json={"title": "Sammlung", "priority": 2,
                                 "local_due": "2027-01-05T09:00",
                                 "timezone": "Europe/Paris"}).json()
        assert client.app.state.tasks.fire_due("2027-01-05T08:00:00+00:00") == 1
        notice = client.get("/notifications").json()[0]
        assert notice["focus_suppressed"] and notice["focus_deferred_at"]
        changed = client.put(f"/tasks/{task['id']}", headers=headers,
                             json={"title": "Sammlung", "priority": 5,
                                   "local_due": "2027-01-05T09:00",
                                   "timezone": "Europe/Paris"}).json()
        assert changed["priority"] == 5
        promoted = client.get("/notifications").json()[0]
        assert not promoted["focus_suppressed"] and promoted["focus_deferred_at"] is None
        lowered = client.put(f"/tasks/{task['id']}", headers=headers,
                             json={"title": "Sammlung", "priority": 2,
                                   "local_due": "2027-01-05T09:00",
                                   "timezone": "Europe/Paris"}).json()
        assert lowered["priority"] == 2
        assert client.get("/notifications").json()[0]["focus_suppressed"]
    archive = tmp_path / "backup.zip"
    assert create_backup(root, archive)["schema"] == 55
    target = tmp_path / "restored"
    restore_backup(archive, target)
    restored_focus = FocusStore(Database(target / "jarvis.db"))
    assert restored_focus.state()["manual_enabled"] is True
    assert TaskStore(Database(target / "jarvis.db")).notifications()[0]["focus_deferred_at"]
