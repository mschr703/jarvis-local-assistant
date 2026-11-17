"""Manuelle Wochenverpflichtungen, Ausnahmen und verfügbare lokale Tageszeit."""

import json
import re
import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jarvis.db import Database, utcnow

Kind = Literal["work", "school", "commute", "break", "sleep", "other"]
WALL_TIME = r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"


def valid_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("Gültige IANA-Zeitzone angeben") from exc
    return value


def valid_day(value: str) -> str:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Datum im Format JJJJ-MM-TT angeben")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Ungültiges Kalenderdatum") from exc
    if not 1900 <= parsed.year <= 2100:
        raise ValueError("Datum muss zwischen 1900 und 2100 liegen")
    return parsed.isoformat()


class RuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    kind: Kind
    weekdays: list[int] = Field(min_length=1, max_length=7)
    start_time: str = Field(pattern=WALL_TIME)
    end_time: str = Field(pattern=WALL_TIME)
    timezone: str = Field(min_length=1, max_length=100)
    buffer_before: int = Field(default=0, ge=0, le=180)
    buffer_after: int = Field(default=0, ge=0, le=180)

    @field_validator("title")
    @classmethod
    def nonempty_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Titel wird benötigt")
        return value.strip()

    @field_validator("weekdays")
    @classmethod
    def normalized_weekdays(cls, value: list[int]) -> list[int]:
        if any(type(day) is not int or day not in range(7) for day in value):
            raise ValueError("Wochentage müssen 0 (Montag) bis 6 (Sonntag) sein")
        if len(set(value)) != len(value):
            raise ValueError("Wochentage nicht doppelt angeben")
        return sorted(value)

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        return valid_timezone(value)


class RuleChange(RuleInput):
    version: int = Field(ge=1)


class ExceptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_local: str
    mode: Literal["skip", "block"]
    rule_id: str | None = None
    title: str | None = Field(default=None, max_length=160)
    kind: Kind | None = None
    start_time: str | None = Field(default=None, pattern=WALL_TIME)
    end_time: str | None = Field(default=None, pattern=WALL_TIME)
    timezone: str | None = Field(default=None, max_length=100)
    buffer_before: int = Field(default=0, ge=0, le=180)
    buffer_after: int = Field(default=0, ge=0, le=180)

    @field_validator("date_local")
    @classmethod
    def calendar_day(cls, value: str) -> str:
        return valid_day(value)

    @model_validator(mode="after")
    def matching_fields(self):
        if self.mode == "skip":
            if not self.rule_id or any((self.title, self.kind, self.start_time,
                                        self.end_time, self.timezone, self.buffer_before,
                                        self.buffer_after)):
                raise ValueError("Ausfall braucht nur Regel und Datum")
        else:
            if self.rule_id or not self.title or not self.title.strip() or not all((
                    self.kind, self.start_time, self.end_time, self.timezone)):
                raise ValueError("Zusatzblock braucht Titel, Art, Zeiten und Zeitzone")
            self.title = self.title.strip()
            self.timezone = valid_timezone(self.timezone)
        return self


class ExceptionChange(ExceptionInput):
    version: int = Field(ge=1)


class TimeModelConflict(ValueError):
    pass


def boundary(day: date, clock: str, zone: ZoneInfo, *, end: bool = False) -> datetime:
    """Löst Wandzeit konservativ auf: Start früh, Ende spät; Lücken vorwärts."""
    hour, minute = map(int, clock.split(":"))
    naive = datetime.combine(day, time(hour, minute))
    for _ in range(1441):
        candidates = []
        for fold in (0, 1):
            utc = naive.replace(tzinfo=zone, fold=fold).astimezone(UTC)
            if utc.astimezone(zone).replace(tzinfo=None) == naive:
                candidates.append(utc)
        if candidates:
            return max(candidates) if end else min(candidates)
        naive += timedelta(minutes=1)
    raise ValueError("Lokale Zeit kann nicht aufgelöst werden")


def interval(day: date, start_time: str, end_time: str, zone: ZoneInfo,
             before: int, after: int) -> tuple[datetime, datetime]:
    next_day = day + timedelta(days=1) if end_time <= start_time else day
    start = boundary(day, start_time, zone) - timedelta(minutes=before)
    end = boundary(next_day, end_time, zone, end=True) + timedelta(minutes=after)
    if end <= start:
        raise ValueError("Zeitblock hat keine positive Dauer")
    return start, end


def date_range(first: date, last: date):
    current = first
    while current <= last:
        yield current
        current += timedelta(days=1)


class TimeModelStore:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _rule(row) -> dict:
        item = dict(row)
        item["weekdays"] = json.loads(item.pop("weekdays_json"))
        return item

    def list_rules(self) -> list[dict]:
        with self.db.connect() as db:
            return [self._rule(row) for row in db.execute(
                "SELECT * FROM time_rules ORDER BY start_time,title,id")]

    def create_rule(self, payload: RuleInput) -> dict:
        rule_id, now = str(uuid4()), utcnow()
        with self.db.connect() as db:
            db.execute("""INSERT INTO time_rules VALUES(?,?,?,?,?,?,?,?,?,1,?,?)""", (
                rule_id, payload.title, payload.kind, json.dumps(payload.weekdays),
                payload.start_time, payload.end_time, payload.timezone,
                payload.buffer_before, payload.buffer_after, now, now))
        self.db.log("time_rule_created", f"Zeitregel {rule_id}")
        return next(item for item in self.list_rules() if item["id"] == rule_id)

    def update_rule(self, rule_id: str, payload: RuleChange) -> dict:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("""UPDATE time_rules SET title=?,kind=?,weekdays_json=?,
                start_time=?,end_time=?,timezone=?,buffer_before=?,buffer_after=?,
                version=version+1,updated_at=? WHERE id=? AND version=?""", (
                payload.title, payload.kind, json.dumps(payload.weekdays), payload.start_time,
                payload.end_time, payload.timezone, payload.buffer_before,
                payload.buffer_after, utcnow(), rule_id, payload.version)).rowcount
            if not changed:
                raise TimeModelConflict("Zeitregel wurde zwischenzeitlich geändert oder gelöscht")
        self.db.log("time_rule_updated", f"Zeitregel {rule_id}")
        return next(item for item in self.list_rules() if item["id"] == rule_id)

    def delete_rule(self, rule_id: str, version: int) -> None:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("DELETE FROM time_rules WHERE id=? AND version=?",
                              (rule_id, version)).rowcount:
                raise TimeModelConflict("Zeitregel wurde zwischenzeitlich geändert oder gelöscht")
        self.db.log("time_rule_deleted", f"Zeitregel {rule_id}")

    def list_exceptions(self) -> list[dict]:
        with self.db.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM time_exceptions ORDER BY date_local DESC,mode,title,id")]

    @staticmethod
    def _check_skip(db, payload: ExceptionInput) -> None:
        if payload.mode != "skip":
            return
        row = db.execute("SELECT weekdays_json FROM time_rules WHERE id=?",
                         (payload.rule_id,)).fetchone()
        if not row:
            raise ValueError("Zeitregel nicht gefunden")
        if date.fromisoformat(payload.date_local).weekday() not in json.loads(row["weekdays_json"]):
            raise ValueError("Die Regel gilt an diesem Wochentag nicht")

    def create_exception(self, payload: ExceptionInput) -> dict:
        exception_id, now = str(uuid4()), utcnow()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_skip(db, payload)
            try:
                db.execute("""INSERT INTO time_exceptions VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?)""", (
                    exception_id, payload.date_local, payload.mode, payload.rule_id,
                    payload.title, payload.kind, payload.start_time, payload.end_time,
                    payload.timezone, payload.buffer_before, payload.buffer_after, now, now))
            except sqlite3.IntegrityError as exc:
                raise TimeModelConflict("Dieser Regelausfall ist bereits gespeichert") from exc
        self.db.log("time_exception_created", f"Zeitausnahme {exception_id}")
        return next(item for item in self.list_exceptions() if item["id"] == exception_id)

    def update_exception(self, exception_id: str, payload: ExceptionChange) -> dict:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_skip(db, payload)
            try:
                changed = db.execute("""UPDATE time_exceptions SET date_local=?,mode=?,rule_id=?,
                    title=?,kind=?,start_time=?,end_time=?,timezone=?,buffer_before=?,buffer_after=?,
                    version=version+1,updated_at=? WHERE id=? AND version=?""", (
                    payload.date_local, payload.mode, payload.rule_id, payload.title,
                    payload.kind, payload.start_time, payload.end_time, payload.timezone,
                    payload.buffer_before, payload.buffer_after, utcnow(), exception_id,
                    payload.version)).rowcount
            except sqlite3.IntegrityError as exc:
                raise TimeModelConflict("Dieser Regelausfall ist bereits gespeichert") from exc
            if not changed:
                raise TimeModelConflict("Zeitausnahme wurde zwischenzeitlich geändert oder gelöscht")
        self.db.log("time_exception_updated", f"Zeitausnahme {exception_id}")
        return next(item for item in self.list_exceptions() if item["id"] == exception_id)

    def delete_exception(self, exception_id: str, version: int) -> None:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("DELETE FROM time_exceptions WHERE id=? AND version=?",
                              (exception_id, version)).rowcount:
                raise TimeModelConflict("Zeitausnahme wurde zwischenzeitlich geändert oder gelöscht")
        self.db.log("time_exception_deleted", f"Zeitausnahme {exception_id}")

    def availability(self, day_text: str, timezone: str) -> dict:
        day = date.fromisoformat(valid_day(day_text))
        zone = ZoneInfo(valid_timezone(timezone))
        day_start = boundary(day, "00:00", zone)
        day_end = boundary(day + timedelta(days=1), "00:00", zone)
        if day_end <= day_start:
            raise ValueError("Dieses Kalenderdatum existiert in der Zeitzone nicht")
        with self.db.connect() as db:
            rules = [self._rule(row) for row in db.execute("SELECT * FROM time_rules")]
            near_start = (day - timedelta(days=3)).isoformat()
            near_end = (day + timedelta(days=3)).isoformat()
            exceptions = [dict(row) for row in db.execute(
                "SELECT * FROM time_exceptions WHERE date_local BETWEEN ? AND ?",
                (near_start, near_end))]
        skipped = {(item["rule_id"], item["date_local"]) for item in exceptions
                   if item["mode"] == "skip"}
        busy = []

        def add(source: str, item: dict, occurrence: date, start: datetime, end: datetime):
            clipped_start, clipped_end = max(start, day_start), min(end, day_end)
            if clipped_start >= clipped_end:
                return
            busy.append({"source": source, "id": item["id"], "title": item["title"],
                         "kind": item["kind"], "occurrence_date": occurrence.isoformat(),
                         "start_utc": clipped_start.isoformat(), "end_utc": clipped_end.isoformat(),
                         "start_local": clipped_start.astimezone(zone).isoformat(),
                         "end_local": clipped_end.astimezone(zone).isoformat(),
                         "minutes": round((clipped_end - clipped_start).total_seconds() / 60)})

        for rule in rules:
            rule_zone = ZoneInfo(rule["timezone"])
            first = day_start.astimezone(rule_zone).date() - timedelta(days=1)
            last = day_end.astimezone(rule_zone).date() + timedelta(days=1)
            for occurrence in date_range(first, last):
                if occurrence.weekday() not in rule["weekdays"] or (
                        rule["id"], occurrence.isoformat()) in skipped:
                    continue
                start, end = interval(occurrence, rule["start_time"], rule["end_time"],
                                      rule_zone, rule["buffer_before"], rule["buffer_after"])
                add("rule", rule, occurrence, start, end)
        for item in exceptions:
            if item["mode"] != "block":
                continue
            occurrence = date.fromisoformat(item["date_local"])
            start, end = interval(occurrence, item["start_time"], item["end_time"],
                                  ZoneInfo(item["timezone"]), item["buffer_before"],
                                  item["buffer_after"])
            add("exception", item, occurrence, start, end)
        busy.sort(key=lambda item: (item["start_utc"], item["end_utc"], item["title"]))
        merged: list[tuple[datetime, datetime]] = []
        for item in busy:
            start, end = datetime.fromisoformat(item["start_utc"]), datetime.fromisoformat(item["end_utc"])
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        free = []
        cursor = day_start
        for start, end in [*merged, (day_end, day_end)]:
            if cursor < start:
                free.append({"start_utc": cursor.isoformat(), "end_utc": start.isoformat(),
                             "start_local": cursor.astimezone(zone).isoformat(),
                             "end_local": start.astimezone(zone).isoformat(),
                             "minutes": round((start - cursor).total_seconds() / 60)})
            cursor = max(cursor, end)
        total = round((day_end - day_start).total_seconds() / 60)
        free_minutes = sum(item["minutes"] for item in free)
        return {"date_local": day.isoformat(), "timezone": timezone,
                "total_minutes": total, "blocked_minutes": total - free_minutes,
                "free_minutes": free_minutes, "busy": busy, "free": free}
