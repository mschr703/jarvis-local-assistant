"""Deterministische, lokale Abendplanung aus Aufgaben und freier Zeit."""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jarvis.db import Database, utcnow
from jarvis.tasks import TaskStore
from jarvis.time_model import WALL_TIME, TimeModelStore, boundary, valid_day, valid_timezone


class PlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=100)
    estimate_minutes: int = Field(ge=5, le=480)
    priority: int = Field(ge=1, le=5)


class EveningPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_local: str
    timezone: str = Field(min_length=1, max_length=100)
    start_time: str = Field(pattern=WALL_TIME)
    sleep_time: str = Field(pattern=WALL_TIME)
    work_minutes: int = Field(default=45, ge=10, le=120)
    break_minutes: int = Field(default=10, ge=5, le=60)
    items: list[PlanItem] = Field(min_length=1, max_length=20)

    @field_validator("date_local")
    @classmethod
    def calendar_day(cls, value: str) -> str:
        return valid_day(value)

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        return valid_timezone(value)

    @model_validator(mode="after")
    def unique_tasks(self):
        ids = [item.task_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Eine Aufgabe darf nur einmal im Abendplan stehen")
        return self


class EveningPlanChange(EveningPlanInput):
    version: int = Field(ge=1)


class EveningPlanConflict(ValueError):
    pass


class EveningPlanStore:
    def __init__(self, db: Database, tasks: TaskStore, time_model: TimeModelStore):
        self.db = db
        self.tasks = tasks
        self.time_model = time_model

    @staticmethod
    def _segment(kind: str, start: datetime, end: datetime, zone: ZoneInfo,
                 task: dict | None = None) -> dict:
        return {"kind": kind, "task_id": task["id"] if task else None,
                "title": task["title"] if task else "Pause",
                "start_utc": start.isoformat(), "end_utc": end.isoformat(),
                "start_local": start.astimezone(zone).isoformat(),
                "end_local": end.astimezone(zone).isoformat(),
                "minutes": round((end - start).total_seconds() / 60)}

    def preview(self, payload: EveningPlanInput) -> dict:
        day = date.fromisoformat(payload.date_local)
        zone = ZoneInfo(payload.timezone)
        start = boundary(day, payload.start_time, zone)
        sleep_day = day + timedelta(days=payload.sleep_time <= payload.start_time)
        end = boundary(sleep_day, payload.sleep_time, zone, end=True)
        duration = (end - start).total_seconds() / 60
        if not 30 <= duration <= 18 * 60:
            raise ValueError("Zwischen Abendbeginn und Schlafziel müssen 30 Minuten bis 18 Stunden liegen")

        free: list[tuple[datetime, datetime]] = []
        for current_day in (day, day + timedelta(days=1)) if sleep_day > day else (day,):
            availability = self.time_model.availability(current_day.isoformat(), payload.timezone)
            for slot in availability["free"]:
                slot_start = max(start, datetime.fromisoformat(slot["start_utc"]))
                slot_end = min(end, datetime.fromisoformat(slot["end_utc"]))
                if slot_start < slot_end:
                    if free and slot_start <= free[-1][1]:
                        free[-1] = (free[-1][0], max(free[-1][1], slot_end))
                    else:
                        free.append((slot_start, slot_end))

        tasks = {task["id"]: task for task in self.tasks.list_tasks()}
        ordered = sorted(enumerate(payload.items), key=lambda pair: (
            -pair[1].priority,
            tasks.get(pair[1].task_id, {}).get("due_utc") or "9999",
            pair[0]))
        source_tasks = [{"id": item.task_id, "task": tasks.get(item.task_id)}
                        for item in payload.items]
        source_hash = hashlib.sha256(json.dumps(
            {"free": [(start.isoformat(), finish.isoformat()) for start, finish in free],
             "tasks": source_tasks}, sort_keys=True,
            ensure_ascii=False).encode("utf-8")).hexdigest()

        segments = []
        remainder = []
        cursor = start
        worked = 0
        pause_remaining = 0
        free_index = 0

        def next_free() -> tuple[datetime, datetime] | None:
            nonlocal cursor, worked, pause_remaining, free_index
            while free_index < len(free):
                slot_start, slot_end = free[free_index]
                if cursor >= slot_end:
                    free_index += 1
                    continue
                if cursor < slot_start:
                    gap = int((slot_start - cursor).total_seconds() // 60)
                    pause_remaining = max(0, pause_remaining - gap)
                    if gap >= payload.break_minutes or (worked >= payload.work_minutes and pause_remaining == 0):
                        worked = 0
                    cursor = slot_start
                return slot_start, slot_end
            return None

        for _, item in ordered:
            task = tasks.get(item.task_id)
            if not task or task["status"] != "open":
                remainder.append({"task_id": item.task_id,
                                  "title": task["title"] if task else "Gelöschte Aufgabe",
                                  "remaining_minutes": item.estimate_minutes,
                                  "reason": "done" if task else "missing"})
                continue
            remaining = item.estimate_minutes
            due = datetime.fromisoformat(task["due_utc"]).astimezone(UTC) if task["due_utc"] else None
            while remaining:
                slot = next_free()
                if slot is None or cursor >= end or (due and cursor >= due):
                    break
                slot_end = min(slot[1], end)
                if due:
                    slot_end = min(slot_end, due)
                if worked >= payload.work_minutes and pause_remaining == 0:
                    pause_remaining = payload.break_minutes
                if pause_remaining:
                    break_end = min(slot_end, cursor + timedelta(minutes=pause_remaining))
                    if break_end <= cursor:
                        cursor = slot[1]
                        continue
                    segments.append(self._segment("break", cursor, break_end, zone))
                    pause_remaining -= segments[-1]["minutes"]
                    cursor = break_end
                    if pause_remaining == 0:
                        worked = 0
                    continue
                span = min(remaining, payload.work_minutes - worked,
                           int((slot_end - cursor).total_seconds() // 60))
                if span <= 0:
                    cursor = slot[1]
                    continue
                finish = cursor + timedelta(minutes=span)
                segments.append(self._segment("work", cursor, finish, zone, task))
                cursor = finish
                remaining -= span
                worked += span
            if remaining:
                reason = "deadline" if due and (due <= cursor or due <= start) else "no_time"
                remainder.append({"task_id": item.task_id, "title": task["title"],
                                  "remaining_minutes": remaining, "reason": reason})

        return {"date_local": payload.date_local, "timezone": payload.timezone,
                "window_start_local": start.astimezone(zone).isoformat(),
                "sleep_local": end.astimezone(zone).isoformat(),
                "window_minutes": round(duration),
                "available_minutes": sum(round((b - a).total_seconds() / 60) for a, b in free),
                "planned_minutes": sum(s["minutes"] for s in segments if s["kind"] == "work"),
                "break_minutes": sum(s["minutes"] for s in segments if s["kind"] == "break"),
                "segments": segments, "remainder": remainder, "source_hash": source_hash}

    @staticmethod
    def _decode(row) -> dict:
        item = dict(row)
        item["input"] = json.loads(item.pop("input_json"))
        item["result"] = json.loads(item.pop("result_json"))
        return item

    def list(self) -> list[dict]:
        with self.db.connect() as db:
            return [self._decode(row) for row in db.execute(
                "SELECT * FROM evening_plans ORDER BY updated_at DESC LIMIT 100")]

    def get(self, plan_id: str) -> dict | None:
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM evening_plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            return None
        item = self._decode(row)
        current = self.preview(EveningPlanInput.model_validate(item["input"]))
        item["stale"] = current["source_hash"] != item["result"]["source_hash"]
        return item

    def create(self, payload: EveningPlanInput) -> dict:
        result = self.preview(payload)
        plan_id, now = str(uuid4()), utcnow()
        with self.db.connect() as db:
            db.execute("INSERT INTO evening_plans VALUES(?,?,?,1,?,?)", (
                plan_id, payload.model_dump_json(), json.dumps(result, ensure_ascii=False), now, now))
        self.db.log("evening_plan_created", f"Abendplan {plan_id}")
        return self.get(plan_id)

    def update(self, plan_id: str, payload: EveningPlanChange) -> dict:
        result = self.preview(payload)
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("""UPDATE evening_plans SET input_json=?,result_json=?,
                version=version+1,updated_at=? WHERE id=? AND version=?""", (
                payload.model_dump_json(exclude={"version"}),
                json.dumps(result, ensure_ascii=False), utcnow(), plan_id, payload.version)).rowcount
            if not changed:
                raise EveningPlanConflict("Abendplan wurde zwischenzeitlich geändert oder gelöscht")
        self.db.log("evening_plan_updated", f"Abendplan {plan_id}")
        return self.get(plan_id)

    def delete(self, plan_id: str, version: int) -> None:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("DELETE FROM evening_plans WHERE id=? AND version=?",
                              (plan_id, version)).rowcount:
                raise EveningPlanConflict("Abendplan wurde zwischenzeitlich geändert oder gelöscht")
        self.db.log("evening_plan_deleted", f"Abendplan {plan_id}")
