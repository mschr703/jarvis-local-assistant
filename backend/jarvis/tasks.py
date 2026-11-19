"""Persistente Aufgaben und einmalige lokale Erinnerungsjobs."""

import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jarvis.db import Database, utcnow
from jarvis.focus_mode import focus_active

FIRST_FOLLOWUP = timedelta(hours=1)
SECOND_FOLLOWUP = timedelta(hours=4)


class ReminderConflict(ValueError):
    pass


def checked_priority(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 5:
        raise ValueError("Priorität muss eine ganze Zahl von 1 bis 5 sein")
    return value


def utc_instant(value: str | None) -> datetime:
    instant = datetime.fromisoformat(value) if value else datetime.now(UTC)
    if instant.tzinfo is None:
        raise ValueError("UTC-Zeit mit Offset angeben")
    return instant.astimezone(UTC)


def resolve_due(local_text: str, timezone: str, fold: int | None = None) -> tuple[str, str]:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}", local_text):
        raise ValueError("Lokale Zeit im Format JJJJ-MM-TTTHH:MM ohne Offset angeben")
    try:
        naive = datetime.fromisoformat(local_text)
        zone = ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("Ungültige lokale Zeit oder IANA-Zeitzone") from exc
    if naive.tzinfo is not None or naive.second or naive.microsecond:
        raise ValueError("Lokale Zeit im Format JJJJ-MM-TTTHH:MM ohne Offset angeben")
    first = naive.replace(tzinfo=zone, fold=0)
    second = naive.replace(tzinfo=zone, fold=1)
    valid = []
    for candidate in (first, second):
        utc = candidate.astimezone(UTC)
        if utc.astimezone(zone).replace(tzinfo=None) == naive:
            valid.append(utc)
    if not valid:
        raise ValueError("Diese lokale Zeit existiert wegen der Zeitumstellung nicht")
    if len(valid) == 2 and valid[0] != valid[1] and fold is None:
        raise ValueError("Diese lokale Zeit kommt zweimal vor: fold=0 (erstes) oder fold=1 (zweites) angeben")
    if fold is not None and (type(fold) is not int or fold not in {0, 1}):
        raise ValueError("fold muss 0 oder 1 sein")
    chosen = valid[fold or 0] if len(valid) == 2 else valid[0]
    if chosen <= datetime.now(UTC):
        raise ValueError("Erinnerungszeit muss in der Zukunft liegen")
    return chosen.isoformat(), naive.isoformat(timespec="minutes")


class TaskStore:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _with_due_display(row) -> dict:
        task = dict(row)
        task["due_offset"] = None
        task["due_fold"] = None
        if task["due_utc"]:
            local = datetime.fromisoformat(task["due_utc"]).astimezone(ZoneInfo(task["timezone"]))
            offset = local.strftime("%z")
            task["due_offset"] = f"{offset[:3]}:{offset[3:]}"
            if local.replace(fold=0).utcoffset() != local.replace(fold=1).utcoffset():
                task["due_fold"] = local.fold
        return task

    def list_tasks(self) -> list[dict]:
        with self.db.connect() as db:
            return [self._with_due_display(row) for row in db.execute(
                """SELECT t.*,j.id AS reminder_job_id,j.status AS reminder_status
                   FROM tasks t LEFT JOIN reminder_jobs j ON j.id=(
                       SELECT id FROM reminder_jobs
                       WHERE task_id=t.id AND due_utc=t.due_utc ORDER BY rowid DESC LIMIT 1
                   ) ORDER BY CASE WHEN t.status='open' THEN 0 ELSE 1 END,
                       CASE WHEN t.due_utc IS NULL THEN 1 ELSE 0 END,
                       t.due_utc,t.created_at""")]

    def get_task(self, task_id: str) -> dict | None:
        with self.db.connect() as db:
            row = db.execute("""SELECT t.*,j.id AS reminder_job_id,j.status AS reminder_status
                            FROM tasks t LEFT JOIN reminder_jobs j ON j.id=(
                                SELECT id FROM reminder_jobs
                                WHERE task_id=t.id AND due_utc=t.due_utc ORDER BY rowid DESC LIMIT 1
                            ) WHERE t.id=?""", (task_id,)).fetchone()
            return self._with_due_display(row) if row else None

    def create(self, title: str, notes: str, local_due: str | None,
               timezone: str, fold: int | None = None, priority: int = 3) -> dict:
        if not title.strip():
            raise ValueError("Aufgabentitel wird benötigt")
        priority = checked_priority(priority)
        due_utc, due_local = resolve_due(local_due, timezone, fold) if local_due else (None, None)
        task_id, now = str(uuid4()), utcnow()
        with self.db.connect() as db:
            db.execute("""INSERT INTO tasks(id,title,notes,status,due_utc,due_local,timezone,
                created_at,updated_at,priority) VALUES(?,?,?,'open',?,?,?,?,?,?)""",
                       (task_id, title.strip(), notes.strip(), due_utc, due_local,
                        timezone if due_utc else None, now, now, priority))
            if due_utc:
                db.execute("INSERT INTO reminder_jobs VALUES(?,?,?,'pending',?,NULL)",
                           (str(uuid4()), task_id, due_utc, now))
        self.db.log("task_created", f"Aufgabe {task_id}")
        return self.get_task(task_id)

    def update(self, task_id: str, title: str, notes: str, local_due: str | None,
               timezone: str, fold: int | None = None, priority: int | None = None) -> dict:
        if not title.strip():
            raise ValueError("Aufgabentitel wird benötigt")
        now = utcnow()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,due_utc,due_local,timezone,priority FROM tasks WHERE id=?",
                             (task_id,)).fetchone()
            if not row:
                raise ValueError("Aufgabe nicht gefunden")
            new_priority = row["priority"] if priority is None else checked_priority(priority)
            same_local = bool(local_due and local_due == row["due_local"]
                              and timezone == row["timezone"])
            same_due = False
            if same_local:
                stored_local = datetime.fromisoformat(row["due_utc"]).astimezone(ZoneInfo(timezone))
                unambiguous = (stored_local.replace(fold=0).utcoffset()
                               == stored_local.replace(fold=1).utcoffset())
                same_due = fold is None or fold == stored_local.fold or unambiguous
            if same_due:
                due_utc, due_local = row["due_utc"], row["due_local"]
            else:
                due_utc, due_local = resolve_due(local_due, timezone, fold) if local_due else (None, None)
            if row["status"] == "done" and due_utc and not same_due:
                raise ValueError("Erledigte Aufgabe kann keine neue Erinnerung haben")
            db.execute("""UPDATE tasks SET title=?,notes=?,due_utc=?,due_local=?,timezone=?,
                       priority=?,updated_at=? WHERE id=?""", (
                           title.strip(), notes.strip(), due_utc, due_local,
                           timezone if due_utc else None, new_priority, now, task_id))
            if new_priority < 4 and row["priority"] >= 4:
                db.execute("UPDATE notifications SET followup_due_utc=NULL WHERE task_id=?",
                           (task_id,))
            elif new_priority >= 4 and row["priority"] < 4 and row["status"] == "open":
                db.execute("UPDATE notifications SET focus_deferred_at=NULL WHERE task_id=?",
                           (task_id,))
                db.execute("""UPDATE notifications SET followup_due_utc=?
                    WHERE task_id=? AND acknowledged_at IS NULL AND snoozed_until IS NULL
                    AND followup_count<2""", (
                        (utc_instant(now) + FIRST_FOLLOWUP).isoformat(), task_id))
            if not same_due:
                db.execute("""UPDATE notifications SET followup_due_utc=NULL,
                    snoozed_until=NULL,read_at=COALESCE(read_at,?)
                    WHERE task_id=? AND acknowledged_at IS NULL""", (now, task_id))
                db.execute("UPDATE reminder_jobs SET status='cancelled' WHERE task_id=? AND status='pending'",
                           (task_id,))
                if due_utc:
                    db.execute("INSERT INTO reminder_jobs VALUES(?,?,?,'pending',?,NULL)",
                               (str(uuid4()), task_id, due_utc, now))
        self.db.log("task_updated", f"Aufgabe {task_id}")
        return self.get_task(task_id)

    def complete(self, task_id: str) -> dict:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone():
                raise ValueError("Aufgabe nicht gefunden")
            now = utcnow()
            db.execute("UPDATE tasks SET status='done',updated_at=? WHERE id=?", (now, task_id))
            db.execute("UPDATE reminder_jobs SET status='cancelled' WHERE task_id=? AND status='pending'",
                       (task_id,))
            db.execute("""UPDATE notifications SET acknowledged_at=COALESCE(acknowledged_at,?),
                read_at=COALESCE(read_at,?),snoozed_until=NULL,followup_due_utc=NULL
                WHERE task_id=?""", (now, now, task_id))
        self.db.log("task_completed", f"Aufgabe {task_id}")
        return self.get_task(task_id)

    def delete(self, task_id: str) -> None:
        with self.db.connect() as db:
            db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        self.db.log("task_deleted", f"Aufgabe {task_id}")

    def fire_due(self, now_utc: str | None = None) -> int:
        """Erstzustellung und begrenztes Nachhaken transaktional nachreichen."""
        instant = utc_instant(now_utc)
        now = instant.isoformat()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            focus_enabled, _ = focus_active(db, instant)
            jobs = db.execute("""SELECT j.id,j.task_id,t.title,t.priority FROM reminder_jobs j
                               JOIN tasks t ON t.id=j.task_id
                               WHERE j.status='pending' AND j.due_utc<=? AND t.status='open'""",
                              (now,)).fetchall()
            for job in jobs:
                followup = (instant + FIRST_FOLLOWUP).isoformat() if job["priority"] >= 4 else None
                db.execute("""INSERT OR IGNORE INTO notifications(
                    id,job_id,task_id,title,created_at,read_at,acknowledged_at,snoozed_until,
                    followup_due_utc,followup_count,last_delivered_at,focus_deferred_at)
                    VALUES(?,?,?,?,?,NULL,NULL,NULL,?,0,?,?)""", (
                        str(uuid4()), job["id"], job["task_id"], job["title"], now,
                        followup, now, now if focus_enabled and job["priority"] < 4 else None))
                db.execute("UPDATE reminder_jobs SET status='fired',fired_at=? WHERE id=?",
                           (now, job["id"]))
            repeats = db.execute("""SELECT n.id,n.snoozed_until,n.followup_count,t.priority
                FROM notifications n JOIN tasks t ON t.id=n.task_id
                WHERE t.status='open' AND n.acknowledged_at IS NULL AND (
                    (n.snoozed_until IS NOT NULL AND n.snoozed_until<=?) OR
                    (n.snoozed_until IS NULL AND n.followup_due_utc IS NOT NULL
                     AND n.followup_due_utc<=? AND t.priority>=4))""", (now, now)).fetchall()
            for row in repeats:
                if row["snoozed_until"] is not None:
                    next_due = ((instant + (FIRST_FOLLOWUP if row["followup_count"] == 0
                                            else SECOND_FOLLOWUP)).isoformat()
                                if row["priority"] >= 4 and row["followup_count"] < 2 else None)
                    db.execute("""UPDATE notifications SET snoozed_until=NULL,read_at=NULL,
                        last_delivered_at=?,followup_due_utc=?,
                        focus_deferred_at=COALESCE(focus_deferred_at,?) WHERE id=?""",
                               (now, next_due,
                                now if focus_enabled and row["priority"] < 4 else None,
                                row["id"]))
                else:
                    count = row["followup_count"] + 1
                    next_due = (instant + SECOND_FOLLOWUP).isoformat() if count < 2 else None
                    db.execute("""UPDATE notifications SET read_at=NULL,last_delivered_at=?,
                        followup_count=?,followup_due_utc=? WHERE id=?""",
                               (now, count, next_due, row["id"]))
        delivered = len(jobs) + len(repeats)
        if delivered:
            self.db.log("reminders_fired", f"{delivered} Erinnerung(en) zugestellt")
        return delivered

    def notifications(self) -> list[dict]:
        with self.db.connect() as db:
            return [dict(row) for row in db.execute(
                """SELECT n.*,j.due_utc,t.priority,t.status AS task_status FROM notifications n
                   JOIN reminder_jobs j ON j.id=n.job_id
                   JOIN tasks t ON t.id=n.task_id
                   ORDER BY COALESCE(n.last_delivered_at,n.created_at) DESC""")]

    def mark_read(self, notification_id: str) -> None:
        with self.db.connect() as db:
            db.execute("UPDATE notifications SET read_at=COALESCE(read_at,?) WHERE id=?",
                       (utcnow(), notification_id))

    def acknowledge(self, notification_id: str) -> dict:
        now = utcnow()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM notifications WHERE id=?",
                              (notification_id,)).fetchone():
                raise ValueError("Erinnerung nicht gefunden")
            db.execute("""UPDATE notifications SET acknowledged_at=COALESCE(acknowledged_at,?),
                read_at=COALESCE(read_at,?),snoozed_until=NULL,followup_due_utc=NULL
                WHERE id=?""", (now, now, notification_id))
        self.db.log("reminder_acknowledged", f"Erinnerung {notification_id}")
        return next(item for item in self.notifications() if item["id"] == notification_id)

    def snooze(self, notification_id: str, minutes: int, now_utc: str | None = None) -> dict:
        if type(minutes) is not int or not 5 <= minutes <= 1440:
            raise ValueError("Verschieben: 5 bis 1440 Minuten angeben")
        instant = utc_instant(now_utc)
        now = instant.isoformat()
        until = (instant + timedelta(minutes=minutes)).isoformat()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT n.acknowledged_at,t.status,t.due_utc,
                j.due_utc AS notification_due FROM notifications n
                JOIN tasks t ON t.id=n.task_id JOIN reminder_jobs j ON j.id=n.job_id
                WHERE n.id=?""", (notification_id,)).fetchone()
            if row is None:
                raise ValueError("Erinnerung nicht gefunden")
            if (row["acknowledged_at"] or row["status"] != "open"
                    or row["due_utc"] != row["notification_due"]):
                raise ReminderConflict("Bestätigte, erledigte oder veraltete Erinnerung kann nicht verschoben werden")
            db.execute("""UPDATE notifications SET snoozed_until=?,followup_due_utc=NULL,
                focus_deferred_at=NULL,read_at=COALESCE(read_at,?) WHERE id=?""",
                (until, now, notification_id))
        self.db.log("reminder_snoozed", f"Erinnerung {notification_id} bis {until}")
        return next(item for item in self.notifications() if item["id"] == notification_id)
