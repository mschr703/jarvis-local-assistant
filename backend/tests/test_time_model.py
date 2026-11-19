import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.time_model import (
    ExceptionChange,
    ExceptionInput,
    RuleChange,
    RuleInput,
    TimeModelConflict,
    TimeModelStore,
)


def model(tmp_path):
    return TimeModelStore(Database(tmp_path / "jarvis.db"))


def rule(title, weekdays, start, end, kind="other", before=0, after=0):
    return RuleInput(title=title, kind=kind, weekdays=weekdays, start_time=start,
                     end_time=end, timezone="Europe/Paris",
                     buffer_before=before, buffer_after=after)


def test_recurring_blocks_overlap_buffers_and_daily_exceptions(tmp_path):
    store = model(tmp_path)
    work = store.create_rule(rule("Arbeit", [0], "09:00", "17:00", "work", 15, 15))
    store.create_rule(rule("Pendeln", [0], "08:00", "09:00", "commute"))
    monday = store.availability("2027-01-04", "Europe/Paris")
    assert monday["total_minutes"] == 1440
    assert monday["blocked_minutes"] == 555
    assert monday["free_minutes"] == 885
    assert len(monday["busy"]) == 2
    assert monday["free"][0]["end_local"].startswith("2027-01-04T08:00")
    block = store.create_exception(ExceptionInput(
        date_local="2027-01-04", mode="block", title="Sport", kind="other",
        start_time="19:00", end_time="20:00", timezone="Europe/Paris",
        buffer_before=10, buffer_after=5))
    assert store.availability("2027-01-04", "Europe/Paris")["free_minutes"] == 810
    skipped = store.create_exception(ExceptionInput(
        date_local="2027-01-04", mode="skip", rule_id=work["id"]))
    assert store.availability("2027-01-04", "Europe/Paris")["free_minutes"] == 1305
    with pytest.raises(TimeModelConflict, match="bereits"):
        store.create_exception(ExceptionInput(date_local="2027-01-04", mode="skip",
                                               rule_id=work["id"]))
    store.delete_exception(skipped["id"], skipped["version"])
    store.delete_exception(block["id"], block["version"])
    assert store.availability("2027-01-04", "Europe/Paris")["free_minutes"] == 885


def test_overnight_sleep_and_dst_are_actual_elapsed_minutes(tmp_path):
    store = model(tmp_path)
    sleep = store.create_rule(rule("Schlaf", [5], "23:00", "08:00", "sleep"))
    spring = store.availability("2027-03-28", "Europe/Paris")
    autumn = store.availability("2027-10-31", "Europe/Paris")
    assert (spring["total_minutes"], spring["blocked_minutes"], spring["free_minutes"]) == (1380, 420, 960)
    assert (autumn["total_minutes"], autumn["blocked_minutes"], autumn["free_minutes"]) == (1500, 540, 960)
    store.create_exception(ExceptionInput(date_local="2027-03-27", mode="skip",
                                          rule_id=sleep["id"]))
    assert store.availability("2027-03-28", "Europe/Paris")["free_minutes"] == 1380


def test_ambiguous_hour_is_blocked_conservatively(tmp_path):
    store = model(tmp_path)
    store.create_rule(rule("Nachtbereitschaft", [6], "02:15", "02:45"))
    autumn = store.availability("2027-10-31", "Europe/Paris")
    assert autumn["total_minutes"] == 1500
    assert autumn["blocked_minutes"] == 90
    assert autumn["free_minutes"] == 1410
    assert autumn["busy"][0]["start_local"].endswith("+02:00")
    assert autumn["busy"][0]["end_local"].endswith("+01:00")


def test_spring_gap_and_different_rule_timezone(tmp_path):
    store = model(tmp_path)
    store.create_rule(rule("Frühschicht", [6], "02:30", "04:00"))
    spring = store.availability("2027-03-28", "Europe/Paris")
    assert spring["blocked_minutes"] == 60
    assert spring["busy"][0]["start_local"].startswith("2027-03-28T03:00")
    store.create_rule(RuleInput(title="New York", kind="other", weekdays=[0],
                                start_time="18:00", end_time="19:00",
                                timezone="America/New_York"))
    tuesday = store.availability("2027-01-05", "Europe/Paris")
    assert tuesday["blocked_minutes"] == 60
    assert tuesday["busy"][0]["start_local"].startswith("2027-01-05T00:00")


def test_exception_edit_and_rule_delete_cascades_skip(tmp_path):
    store = model(tmp_path)
    item = store.create_rule(rule("Arbeit", [0], "09:00", "17:00"))
    skip = store.create_exception(ExceptionInput(date_local="2027-01-04", mode="skip",
                                                 rule_id=item["id"]))
    extra = store.create_exception(ExceptionInput(date_local="2027-01-04", mode="block",
                                                  title="Termin", kind="other", start_time="18:00",
                                                  end_time="19:00", timezone="Europe/Paris"))
    change = ExceptionChange(date_local="2027-01-04", mode="block", title="Termin neu",
                             kind="other", start_time="19:00", end_time="20:00",
                             timezone="Europe/Paris", version=extra["version"])
    updated = store.update_exception(extra["id"], change)
    assert updated["title"] == "Termin neu" and updated["version"] == 2
    with pytest.raises(TimeModelConflict):
        store.update_exception(extra["id"], change)
    store.delete_rule(item["id"], item["version"])
    assert [entry["id"] for entry in store.list_exceptions()] == [extra["id"]]
    assert skip["id"] != extra["id"]


def test_rule_update_delete_versions_and_weekday_validation(tmp_path):
    store = model(tmp_path)
    item = store.create_rule(rule("Arbeit", [0], "09:00", "17:00"))
    change = RuleChange(**rule("Arbeit neu", [0, 2], "10:00", "18:00").model_dump(),
                        version=item["version"])
    updated = store.update_rule(item["id"], change)
    assert updated["version"] == 2
    assert updated["weekdays"] == [0, 2]
    with pytest.raises(TimeModelConflict):
        store.update_rule(item["id"], change)
    with pytest.raises(TimeModelConflict):
        store.delete_rule(item["id"], 1)
    with pytest.raises(ValueError, match="Wochentag"):
        store.create_exception(ExceptionInput(date_local="2027-01-05", mode="skip",
                                              rule_id=item["id"]))
    store.delete_rule(item["id"], 2)
    assert store.list_rules() == []
    with pytest.raises(ValidationError):
        rule("Falsch", [0, 0], "09:00", "17:00")
    with pytest.raises(ValidationError):
        rule("Falsch", [0], "29:00", "17:00")


def test_api_persists_schedule_and_rejects_stale_edit(tmp_path):
    config = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(config)) as client:
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        payload = rule("Schule", [0, 1, 2, 3, 4], "08:00", "15:00", "school").model_dump()
        item = client.post("/time/rules", headers=headers, json=payload).json()
        assert item["version"] == 1
        changed = client.put(f"/time/rules/{item['id']}", headers=headers,
                             json={**payload, "title": "Schule neu", "version": 1})
        assert changed.status_code == 200
        assert client.put(f"/time/rules/{item['id']}", headers=headers,
                          json={**payload, "version": 1}).status_code == 409
        exception = client.post("/time/exceptions", headers=headers, json={
            "date_local": "2027-01-04", "mode": "skip", "rule_id": item["id"]})
        assert exception.status_code == 200
        assert client.get("/time/availability", params={
            "date_local": "2027-01-04", "timezone": "Europe/Paris"}).json()["free_minutes"] == 1440
    with TestClient(create_app(config)) as restarted:
        restarted.get("/session")
        assert restarted.get("/time/rules").json()[0]["title"] == "Schule neu"
        assert len(restarted.get("/time/exceptions").json()) == 1
        assert restarted.get("/time/availability", params={
            "date_local": "2027-02-30", "timezone": "Europe/Paris"}).status_code == 422
