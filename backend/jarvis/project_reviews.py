"""Zeitgesteuerte, belegte Projektprüfungen ohne automatische Projektaktion."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jarvis.db import Database, utcnow
from jarvis.project_checks import ProjectCheckStore


class ProjectReviewStore:
    def __init__(self, db: Database, checks: ProjectCheckStore,
                 default_timezone: str = "Europe/Paris"):
        self.db = db
        self.checks = checks
        self.default_timezone = default_timezone
        self._lock = threading.Lock()

    @staticmethod
    def _clock(now: datetime | None) -> datetime:
        value = now or datetime.now(UTC)
        if value.tzinfo is None:
            raise ValueError("Prüfzeit benötigt eine Zeitzone")
        return value

    @staticmethod
    def _zone(name: str) -> ZoneInfo:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
            raise ValueError("Ungültige IANA-Zeitzone") from exc

    @staticmethod
    def _initial_date(local: datetime, local_time: str) -> date:
        return local.date() if local.strftime("%H:%M") < local_time else local.date() + timedelta(days=1)

    def configure(self, state_id: str, interval_days: int, local_time: str,
                  timezone: str, paused: bool, now: datetime | None = None) -> dict:
        if type(interval_days) is not int or not 1 <= interval_days <= 30:
            raise ValueError("Prüfintervall muss 1 bis 30 Tage betragen")
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", local_time):
            raise ValueError("Uhrzeit muss HH:MM sein")
        if type(paused) is not bool:
            raise ValueError("Pausiert muss wahr oder falsch sein")
        zone = self._zone(timezone)
        local = self._clock(now).astimezone(zone)
        with self._lock, self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM work_states WHERE id=?", (state_id,)).fetchone() is None:
                raise ValueError("Arbeitsstand nicht gefunden")
            previous = db.execute("SELECT * FROM project_review_schedules WHERE state_id=?",
                                  (state_id,)).fetchone()
            same = (previous is not None and previous["paused"] == int(paused)
                    and previous["interval_days"] == interval_days
                    and previous["local_time"] == local_time and previous["timezone"] == timezone)
            if same:
                next_date = previous["next_local_date"]
            else:
                next_date = self._initial_date(local, local_time).isoformat()
            last_status = previous["last_status"] if same else None
            last_error = previous["last_error"] if same else None
            db.execute("""INSERT INTO project_review_schedules
                (state_id,interval_days,local_time,timezone,next_local_date,paused,
                 last_status,last_error,updated_at) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(state_id) DO UPDATE SET
                interval_days=excluded.interval_days,local_time=excluded.local_time,
                timezone=excluded.timezone,next_local_date=excluded.next_local_date,
                paused=excluded.paused,last_status=excluded.last_status,
                last_error=excluded.last_error,updated_at=excluded.updated_at""",
                       (state_id, interval_days, local_time, timezone, next_date,
                        int(paused), last_status, last_error, utcnow()))
        return self.status(state_id)

    def status(self, state_id: str) -> dict | None:
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM project_review_schedules WHERE state_id=?",
                             (state_id,)).fetchone()
            if row is None:
                return None
            latest = db.execute("""SELECT id,local_date,kind,status,changed,error,created_at
                FROM project_reviews WHERE state_id=? ORDER BY created_at DESC,id DESC LIMIT 1""",
                (state_id,)).fetchone()
        return {**dict(row), "paused": bool(row["paused"]),
                "latest": dict(latest) if latest else None}

    def reports(self, state_id: str, limit: int = 20) -> list[dict]:
        with self.db.connect() as db:
            if db.execute("SELECT 1 FROM work_states WHERE id=?", (state_id,)).fetchone() is None:
                raise ValueError("Arbeitsstand nicht gefunden")
            rows = db.execute("""SELECT * FROM project_reviews WHERE state_id=?
                ORDER BY created_at DESC,id DESC LIMIT ?""", (state_id, min(max(limit, 1), 50)))
            return [{**dict(row), "summary": json.loads(row["summary_json"])
                     if row["summary_json"] else None} for row in rows]

    @staticmethod
    def _summary(check: dict) -> dict:
        state = check["state"]
        return {"project": state["project"], "state_version": state["version"],
                "confirmed": state["confirmed"], "open_questions": state["open_questions"],
                "next_step": state["next_step"], "blockers": check["blockers"],
                "already_prepared": check["already_prepared"],
                "open_tasks": check["open_tasks"], "done_tasks": check["done_tasks"],
                "missing_tasks": check["missing_tasks"],
                "documents": [{"id": item["id"], "path": item["path"],
                               "sha256": item["sha256"], "status": item["status"]}
                              for item in state["documents"]],
                "tasks": [{"id": item["id"], "title": item["title"],
                           "status": item["status"], "current_title": item["current_title"]}
                          for item in state["tasks"]]}

    def recover_incomplete(self) -> None:
        """Nach Prozessabbruch einen begonnenen Tageslauf erneut zulassen."""
        with self._lock, self.db.connect() as db:
            db.execute("""UPDATE project_reviews SET status='failed',
                error='Jarvis wurde während der Projektprüfung beendet'
                WHERE status='running'""")

    def _run(self, state_id: str, local_date: str, kind: str,
             interval_days: int | None = None,
             schedule_updated_at: str | None = None) -> dict | None:
        with self._lock:
            with self.db.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if kind == "scheduled":
                    schedule = db.execute("""SELECT paused,next_local_date,updated_at
                        FROM project_review_schedules
                        WHERE state_id=?""", (state_id,)).fetchone()
                    if (schedule is None or schedule["paused"] or
                            schedule["next_local_date"] > local_date or
                            schedule["updated_at"] != schedule_updated_at):
                        return None
                prior = (db.execute("""SELECT id,status FROM project_reviews
                    WHERE state_id=? AND local_date=? AND kind='scheduled'""",
                    (state_id, local_date)).fetchone() if kind == "scheduled" else None)
                if prior and prior["status"] in {"succeeded", "running"} and kind == "scheduled":
                    return None
                report_id = prior["id"] if prior else str(uuid4())
                prior_fingerprint = (db.execute("SELECT fingerprint FROM project_reviews WHERE id=?",
                                                (report_id,)).fetchone()[0] if prior else None)
                if prior:
                    db.execute("""UPDATE project_reviews SET status='running',error=NULL,
                        created_at=? WHERE id=?""", (utcnow(), report_id))
                else:
                    db.execute("""INSERT INTO project_reviews
                        (id,state_id,local_date,kind,status,created_at)
                        VALUES(?,?,?,?,'running',?)""",
                        (report_id, state_id, local_date, kind, utcnow()))
            summary = None
            fingerprint = None
            error = None
            try:
                check = self.checks.check(state_id)
                if check is None:
                    raise ValueError("Arbeitsstand nicht gefunden")
                summary = self._summary(check)
                fingerprint = hashlib.sha256(json.dumps(
                    summary, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            except Exception as exc:  # noqa: BLE001 - Bericht muss einen sichtbaren Fehler speichern.
                error = f"{type(exc).__name__}: {exc}"[:500]
            with self.db.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                previous = db.execute("""SELECT fingerprint FROM project_reviews
                    WHERE state_id=? AND status='succeeded' AND id<>?
                    ORDER BY created_at DESC,id DESC LIMIT 1""", (state_id, report_id)).fetchone()
                baseline = previous["fingerprint"] if previous else prior_fingerprint
                changed = int(baseline != fingerprint) if not error else None
                db.execute("""UPDATE project_reviews SET status=?,summary_json=?,fingerprint=?,
                    changed=?,error=? WHERE id=?""",
                    ("failed" if error else "succeeded",
                     json.dumps(summary, ensure_ascii=False) if summary else None,
                     fingerprint, changed, error, report_id))
                if kind == "scheduled" and interval_days is not None:
                    next_date = (date.fromisoformat(local_date) + timedelta(days=interval_days)).isoformat()
                    db.execute("""UPDATE project_review_schedules SET next_local_date=?,
                        last_status=?,last_error=?,updated_at=? WHERE state_id=?""",
                        (next_date, "failed" if error else "succeeded", error, utcnow(), state_id))
                elif kind == "manual":
                    db.execute("""UPDATE project_review_schedules SET last_status=?,
                        last_error=?,updated_at=? WHERE state_id=?""",
                        ("failed" if error else "succeeded", error, utcnow(), state_id))
            self.db.log("project_review_failed" if error else "project_review_succeeded",
                        f"Projekt {state_id}: Bericht {report_id}")
            with self.db.connect() as db:
                row = db.execute("SELECT * FROM project_reviews WHERE id=?", (report_id,)).fetchone()
            if row is None:
                raise ValueError("Arbeitsstand wurde während der Prüfung gelöscht")
            return {**dict(row), "summary": summary}

    def run_now(self, state_id: str, now: datetime | None = None) -> dict:
        with self.db.connect() as db:
            if db.execute("SELECT 1 FROM work_states WHERE id=?", (state_id,)).fetchone() is None:
                raise ValueError("Arbeitsstand nicht gefunden")
        schedule = self.status(state_id)
        zone = self._zone(schedule["timezone"] if schedule else self.default_timezone)
        local_date = self._clock(now).astimezone(zone).date().isoformat()
        report = self._run(state_id, local_date, "manual")
        assert report is not None
        return report

    def tick(self, now: datetime | None = None) -> list[dict]:
        current = self._clock(now)
        with self.db.connect() as db:
            schedules = [dict(row) for row in db.execute(
                "SELECT * FROM project_review_schedules WHERE paused=0")]
        results = []
        for schedule in schedules:
            local = current.astimezone(self._zone(schedule["timezone"]))
            today = local.date().isoformat()
            if (today < schedule["next_local_date"] or
                    (today == schedule["next_local_date"] and
                     local.strftime("%H:%M") < schedule["local_time"])):
                continue
            result = self._run(schedule["state_id"], today, "scheduled",
                               schedule["interval_days"], schedule["updated_at"])
            if result is not None:
                results.append(result)
        return results
