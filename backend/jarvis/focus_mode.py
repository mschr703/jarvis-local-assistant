"""Lokaler Fokusmodus für planbare, nachvollziehbar zurückgehaltene Hinweise."""

import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jarvis.db import Database


class FocusConflict(ValueError):
    pass


def _checked_clock(value: str) -> str:
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
        raise ValueError("Fokuszeit im Format HH:MM angeben")
    return value


def focus_active(db, instant: datetime) -> tuple[bool, dict]:
    row = db.execute("SELECT * FROM focus_settings WHERE id=1").fetchone()
    if row is None:
        raise RuntimeError("Fokuseinstellungen fehlen")
    values = dict(row)
    manual = bool(values["manual_enabled"])
    scheduled = False
    if values["schedule_enabled"]:
        local = instant.astimezone(ZoneInfo(values["timezone"])).strftime("%H:%M")
        start, end = values["start_time"], values["end_time"]
        scheduled = start <= local < end if start < end else local >= start or local < end
    return manual or scheduled, values


class FocusStore:
    def __init__(self, db: Database):
        self.db = db

    def state(self, now_utc: str | None = None) -> dict:
        instant = _instant(now_utc)
        with self.db.connect() as db:
            active, values = focus_active(db, instant)
        values["manual_enabled"] = bool(values["manual_enabled"])
        values["schedule_enabled"] = bool(values["schedule_enabled"])
        values["active"] = active
        return values

    def update(self, manual_enabled: bool, schedule_enabled: bool,
               start_time: str, end_time: str, timezone: str,
               expected_version: int, now_utc: str | None = None) -> dict:
        if type(manual_enabled) is not bool or type(schedule_enabled) is not bool:
            raise ValueError("Fokus-Schalter muss Ja oder Nein sein")
        _checked_clock(start_time)
        _checked_clock(end_time)
        if start_time == end_time:
            raise ValueError("Beginn und Ende des Fokusfensters müssen verschieden sein")
        try:
            ZoneInfo(timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Ungültige IANA-Zeitzone") from exc
        instant = _instant(now_utc)
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            updated = db.execute("""UPDATE focus_settings SET manual_enabled=?,schedule_enabled=?,
                start_time=?,end_time=?,timezone=?,version=version+1,updated_at=?
                WHERE id=1 AND version=?""", (
                    int(manual_enabled), int(schedule_enabled), start_time, end_time,
                    timezone, instant.isoformat(), expected_version)).rowcount
            if not updated:
                raise FocusConflict("Fokusmodus wurde anderswo geändert; neu laden")
            active, _ = focus_active(db, instant)
            if active:
                _defer_unread(db, instant.isoformat())
        self.db.log("focus_changed", "Fokusmodus geändert")
        return self.state(instant.isoformat())

    def notifications(self, items: list[dict], now_utc: str | None = None) -> list[dict]:
        instant = _instant(now_utc)
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active, _ = focus_active(db, instant)
            if active:
                _defer_unread(db, instant.isoformat())
            deferred = {row["id"]: row["focus_deferred_at"] for row in db.execute(
                "SELECT id,focus_deferred_at FROM notifications")}
        for item in items:
            item["focus_deferred_at"] = deferred.get(item["id"])
            item["focus_suppressed"] = bool(
                active and item["priority"] < 4 and item["task_status"] == "open"
                and not item["read_at"] and not item["acknowledged_at"]
                and not item["snoozed_until"])
        return items


def _instant(now_utc: str | None) -> datetime:
    instant = datetime.fromisoformat(now_utc) if now_utc else datetime.now(UTC)
    if instant.tzinfo is None:
        raise ValueError("UTC-Zeit mit Offset angeben")
    return instant.astimezone(UTC)


def _defer_unread(db, now: str) -> int:
    return db.execute("""UPDATE notifications SET focus_deferred_at=COALESCE(focus_deferred_at,?)
        WHERE id IN (SELECT n.id FROM notifications n JOIN tasks t ON t.id=n.task_id
        WHERE n.read_at IS NULL AND n.acknowledged_at IS NULL AND n.snoozed_until IS NULL
        AND n.focus_deferred_at IS NULL AND t.status='open' AND t.priority<4)""",
        (now,)).rowcount
