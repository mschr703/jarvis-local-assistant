from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.evening_plans import (
    EveningPlanChange,
    EveningPlanConflict,
    EveningPlanInput,
    EveningPlanStore,
)
from jarvis.tasks import TaskStore
from jarvis.time_model import ExceptionInput, TimeModelStore


def setup(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    tasks = TaskStore(db)
    time = TimeModelStore(db)
    return db, tasks, time, EveningPlanStore(db, tasks, time)


def payload(items, date_local="2027-01-04", start_time="18:00", sleep_time="23:00",
            work_minutes=45, break_minutes=10):
    return EveningPlanInput(date_local=date_local, timezone="Europe/Paris",
                            start_time=start_time, sleep_time=sleep_time,
                            work_minutes=work_minutes, break_minutes=break_minutes,
                            items=items)


def item(task, minutes=60, priority=3):
    return {"task_id": task["id"], "estimate_minutes": minutes, "priority": priority}


def block(time, date_local, start, end, before=0, after=0):
    return time.create_exception(ExceptionInput(
        date_local=date_local, mode="block", title="Termin", kind="other",
        start_time=start, end_time=end, timezone="Europe/Paris",
        buffer_before=before, buffer_after=after))


def test_priority_breaks_busy_buffers_sleep_and_remainder(tmp_path):
    _, tasks, time, plans = setup(tmp_path)
    low = tasks.create("Niedrig", "", None, "Europe/Paris")
    high = tasks.create("Hoch", "", None, "Europe/Paris")
    block(time, "2027-01-04", "19:00", "20:00", before=10, after=10)
    result = plans.preview(payload([item(low, 120, 1), item(high, 120, 5)],
                                   sleep_time="21:00"))
    assert result["window_minutes"] == 180
    assert result["available_minutes"] == 100
    assert result["planned_minutes"] == 90
    assert result["break_minutes"] == 10
    assert [part["kind"] for part in result["segments"]] == ["work", "break", "work", "break"]
    assert all(part["task_id"] == high["id"] for part in result["segments"]
               if part["kind"] == "work")
    assert result["segments"][-1]["end_local"].startswith("2027-01-04T21:00")
    assert [(rest["title"], rest["remaining_minutes"]) for rest in result["remainder"]] == [
        ("Hoch", 30), ("Niedrig", 120)]


def test_due_date_and_done_task_never_planned(tmp_path):
    db, tasks, _, plans = setup(tmp_path)
    due = tasks.create("Mit Frist", "", None, "Europe/Paris")
    done = tasks.create("Fertig", "", None, "Europe/Paris")
    tasks.complete(done["id"])
    with db.connect() as connection:
        connection.execute("UPDATE tasks SET due_utc=?,due_local=?,timezone=? WHERE id=?",
                           ("2027-01-04T18:30:00+00:00", "2027-01-04T19:30",
                            "Europe/Paris", due["id"]))
    result = plans.preview(payload([item(done, 30, 5), item(due, 120, 3)]))
    assert result["planned_minutes"] == 80
    assert result["remainder"][0]["reason"] == "done"
    assert result["remainder"][1] == {"task_id": due["id"], "title": "Mit Frist",
                                     "remaining_minutes": 40, "reason": "deadline"}
    assert all(part["end_utc"] <= "2027-01-04T18:30:00+00:00"
               for part in result["segments"] if part["kind"] == "work")


def test_overnight_dst_uses_real_utc_minutes(tmp_path):
    _, tasks, time, plans = setup(tmp_path)
    task = tasks.create("Nacht", "", None, "Europe/Paris")
    block(time, "2027-03-27", "23:30", "00:30")
    spring = plans.preview(payload([item(task, 240)], "2027-03-27", "23:00", "04:00"))
    autumn = plans.preview(payload([item(task, 240)], "2027-10-30", "23:00", "04:00"))
    assert spring["window_minutes"] == 240
    assert spring["available_minutes"] == 180
    assert autumn["window_minutes"] == 360
    for result in (spring, autumn):
        assert all(datetime.fromisoformat(part["end_utc"]).astimezone(UTC)
                   > datetime.fromisoformat(part["start_utc"]).astimezone(UTC)
                   for part in result["segments"])
        assert result["planned_minutes"] <= result["available_minutes"]


def test_replan_staleness_version_and_restore(tmp_path):
    source = tmp_path / "source"
    db, tasks, time, plans = setup(source)
    task = tasks.create("Planen", "", None, "Europe/Paris")
    request = payload([item(task, 60)])
    saved = plans.create(request)
    assert saved["stale"] is False
    block(time, "2027-01-04", "18:00", "19:00")
    assert plans.get(saved["id"])["stale"] is True
    changed = plans.update(saved["id"], EveningPlanChange(**request.model_dump(), version=1))
    assert changed["version"] == 2 and changed["stale"] is False
    assert changed["result"]["segments"][0]["start_local"].startswith("2027-01-04T19:00")
    with pytest.raises(EveningPlanConflict):
        plans.update(saved["id"], EveningPlanChange(**request.model_dump(), version=1))
    archive = tmp_path / "backup.zip"
    assert create_backup(db.path.parent, archive)["schema"] == 60
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    other_db = Database(restored / "jarvis.db")
    other = EveningPlanStore(other_db, TaskStore(other_db), TimeModelStore(other_db))
    assert other.get(saved["id"])["result"] == changed["result"]
    with pytest.raises(EveningPlanConflict):
        plans.delete(saved["id"], 1)
    plans.delete(saved["id"], 2)
    assert plans.get(saved["id"]) is None


def test_irrelevant_time_change_is_not_stale_but_task_completion_is(tmp_path):
    _, tasks, time, plans = setup(tmp_path)
    task = tasks.create("Bericht", "", None, "Europe/Paris")
    saved = plans.create(payload([item(task, 30)]))
    block(time, "2027-01-04", "09:00", "10:00")
    assert plans.get(saved["id"])["stale"] is False
    tasks.complete(task["id"])
    assert plans.get(saved["id"])["stale"] is True
    updated = plans.update(saved["id"], EveningPlanChange(**saved["input"], version=1))
    assert updated["result"]["planned_minutes"] == 0
    assert updated["result"]["remainder"][0]["reason"] == "done"


def test_bad_input_and_api_replan(tmp_path):
    with pytest.raises(ValidationError):
        payload([{"task_id": "x", "estimate_minutes": 0, "priority": 3}])
    with pytest.raises(ValidationError):
        payload([{"task_id": "x", "estimate_minutes": 30, "priority": 3}] * 2)
    config = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(config)) as client:
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        task = client.post("/tasks", headers=headers, json={
            "title": "UI Plan", "notes": "", "local_due": None,
            "timezone": "Europe/Paris"}).json()
        body = payload([item(task, 60)]).model_dump()
        assert client.post("/evening-plans/preview", headers=headers,
                           json={**body, "sleep_time": "18:10"}).status_code == 422
        created = client.post("/evening-plans", headers=headers, json=body).json()
        assert created["version"] == 1
        assert client.get(f"/evening-plans/{created['id']}").json()["stale"] is False
        assert client.put(f"/evening-plans/{created['id']}", headers=headers,
                          json={**body, "version": 1}).status_code == 200
        assert client.put(f"/evening-plans/{created['id']}", headers=headers,
                          json={**body, "version": 1}).status_code == 409
