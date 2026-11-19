"""Manually confirmed personal contacts and notes, stored only on this device."""

from __future__ import annotations

import sqlite3
import unicodedata
from datetime import UTC, datetime
from uuid import uuid4

from jarvis.db import Database, utcnow


class ContactConflict(ValueError):
    """The client edited an older version or reused a person's identity."""


def _clean(value: str, label: str, maximum: int, *, required: bool = True) -> str:
    value = value.strip()
    if (required and not value) or len(value) > maximum or any(
        ord(char) < 32 and char not in "\n\t" for char in value
    ):
        raise ValueError(f"{label} fehlt oder ist ungültig (maximal {maximum} Zeichen)")
    return value


def _key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


class ContactStore:
    def __init__(self, db: Database):
        self.db = db

    def list(self, query: str = "") -> list[dict]:
        query = _key(_clean(query, "Suche", 200, required=False))
        with self.db.connect() as db:
            rows = db.execute("SELECT * FROM contacts ORDER BY name_key,context_key,id").fetchall()
            notes = db.execute("SELECT contact_id,content FROM contact_notes").fetchall()
            plans = db.execute("SELECT contact_id,title,details FROM contact_plans").fetchall()
        by_contact: dict[str, list[str]] = {}
        for note in notes:
            by_contact.setdefault(note["contact_id"], []).append(note["content"])
        plan_by_contact: dict[str, list[str]] = {}
        plan_counts: dict[str, int] = {}
        for plan in plans:
            plan_by_contact.setdefault(plan["contact_id"], []).extend(
                (plan["title"], plan["details"]))
            plan_counts[plan["contact_id"]] = plan_counts.get(plan["contact_id"], 0) + 1
        results = []
        for row in rows:
            person = dict(row)
            contents = by_contact.get(person["id"], [])
            if query and not any(query in _key(value) for value in
                                 (person["name"], person["context"],
                                  person["relationship"], *contents,
                                  *plan_by_contact.get(person["id"], []))):
                continue
            person["note_count"] = len(contents)
            person["plan_count"] = plan_counts.get(person["id"], 0)
            results.append(person)
            if len(results) == 200:
                break
        return results

    def get(self, contact_id: str) -> dict:
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
            if row is None:
                raise LookupError("Kontakt nicht gefunden")
            notes = db.execute("SELECT * FROM contact_notes WHERE contact_id=? "
                               "ORDER BY created_at DESC,id DESC", (contact_id,)).fetchall()
            plans = db.execute("SELECT * FROM contact_plans WHERE contact_id=? "
                               "ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, "
                               "updated_at DESC,id DESC", (contact_id,)).fetchall()
            links = db.execute("""SELECT l.*,t.title AS current_title,
                t.status AS current_status,t.due_utc AS current_due_utc,
                t.due_local AS current_due_local,t.timezone AS current_timezone,
                (SELECT j.status FROM reminder_jobs j WHERE j.task_id=t.id
                    AND j.due_utc=t.due_utc ORDER BY j.rowid DESC LIMIT 1)
                    AS current_reminder_status
                FROM contact_plan_reminders l
                JOIN contact_plans p ON p.id=l.plan_id
                LEFT JOIN tasks t ON t.id=l.task_id WHERE p.contact_id=?""",
                (contact_id,)).fetchall()
            reminders = {link["plan_id"]: self._reminder_status(dict(link)) for link in links}
            return {**dict(row), "notes": [dict(note) for note in notes],
                    "plans": [{**dict(plan), "reminder": reminders.get(plan["id"])}
                              for plan in plans]}

    @staticmethod
    def _reminder_status(link: dict) -> dict:
        if link["task_id"] is None:
            status = "task_deleted"
        elif link["current_status"] == "done":
            status = "task_completed"
        elif link["current_due_utc"] is None:
            status = "due_removed"
        elif (link["current_due_utc"] != link["due_utc_snapshot"]
              or link["current_title"] != link["task_title_snapshot"]):
            status = "task_changed"
        elif link["current_reminder_status"] == "fired":
            status = "fired"
        elif link["current_reminder_status"] == "cancelled":
            status = "cancelled"
        else:
            status = "active"
        return {**link, "status": status}

    def create(self, name: str, context: str, relationship: str = "") -> dict:
        name = _clean(name, "Name", 120)
        context = _clean(context, "Kontext", 120)
        relationship = _clean(relationship, "Beziehung", 120, required=False)
        identifier, now = str(uuid4()), utcnow()
        try:
            with self.db.connect() as db:
                db.execute("INSERT INTO contacts(id,name,name_key,context,context_key,"
                           "relationship,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                           (identifier, name, _key(name), context, _key(context),
                            relationship, now, now))
        except sqlite3.IntegrityError as exc:
            raise ContactConflict("Name und Kontext sind bereits vergeben") from exc
        return self.get(identifier)

    def update(self, contact_id: str, version: int, name: str,
               context: str, relationship: str = "") -> dict:
        name = _clean(name, "Name", 120)
        context = _clean(context, "Kontext", 120)
        relationship = _clean(relationship, "Beziehung", 120, required=False)
        try:
            with self.db.connect() as db:
                changed = db.execute("UPDATE contacts SET name=?,name_key=?,context=?,"
                                     "context_key=?,relationship=?,"
                                     "version=version+1,updated_at=? WHERE id=? AND version=?",
                                     (name, _key(name), context, _key(context),
                                      relationship, utcnow(), contact_id, version))
                if not changed.rowcount:
                    self._missing_or_conflict(db, "contacts", contact_id, version)
        except sqlite3.IntegrityError as exc:
            raise ContactConflict("Name und Kontext sind bereits vergeben") from exc
        return self.get(contact_id)

    def delete(self, contact_id: str, version: int) -> None:
        with self.db.connect() as db:
            deleted = db.execute("DELETE FROM contacts WHERE id=? AND version=?",
                                 (contact_id, version))
            if not deleted.rowcount:
                self._missing_or_conflict(db, "contacts", contact_id, version)

    def add_note(self, contact_id: str, content: str) -> dict:
        content = _clean(content, "Notiz", 4000)
        identifier, now = str(uuid4()), utcnow()
        with self.db.connect() as db:
            if db.execute("SELECT 1 FROM contacts WHERE id=?", (contact_id,)).fetchone() is None:
                raise LookupError("Kontakt nicht gefunden")
            db.execute("INSERT INTO contact_notes(id,contact_id,content,source_kind,"
                       "created_at,updated_at) VALUES(?,?,?,'manual',?,?)",
                       (identifier, contact_id, content, now, now))
            db.execute("UPDATE contacts SET version=version+1,updated_at=? WHERE id=?",
                       (now, contact_id))
            return dict(db.execute("SELECT * FROM contact_notes WHERE id=?",
                                   (identifier,)).fetchone())

    def update_note(self, contact_id: str, note_id: str, version: int, content: str) -> dict:
        content = _clean(content, "Notiz", 4000)
        with self.db.connect() as db:
            changed = db.execute("UPDATE contact_notes SET content=?,version=version+1,"
                                 "updated_at=? WHERE id=? AND contact_id=? AND version=?",
                                 (content, utcnow(), note_id, contact_id, version))
            if not changed.rowcount:
                self._missing_or_conflict(db, "contact_notes", note_id, version, contact_id)
            db.execute("UPDATE contacts SET version=version+1,updated_at=? WHERE id=?",
                       (utcnow(), contact_id))
            return dict(db.execute("SELECT * FROM contact_notes WHERE id=?", (note_id,)).fetchone())

    def delete_note(self, contact_id: str, note_id: str, version: int) -> None:
        with self.db.connect() as db:
            deleted = db.execute("DELETE FROM contact_notes WHERE id=? AND contact_id=? "
                                 "AND version=?", (note_id, contact_id, version))
            if not deleted.rowcount:
                self._missing_or_conflict(db, "contact_notes", note_id, version, contact_id)
            db.execute("UPDATE contacts SET version=version+1,updated_at=? WHERE id=?",
                       (utcnow(), contact_id))

    def add_plan(self, contact_id: str, title: str, details: str = "") -> dict:
        title = _clean(title, "Vorhaben", 160)
        details = _clean(details, "Details", 4000, required=False)
        identifier, now = str(uuid4()), utcnow()
        with self.db.connect() as db:
            if db.execute("SELECT 1 FROM contacts WHERE id=?", (contact_id,)).fetchone() is None:
                raise LookupError("Kontakt nicht gefunden")
            db.execute("INSERT INTO contact_plans(id,contact_id,title,details,created_at,"
                       "updated_at) VALUES(?,?,?,?,?,?)",
                       (identifier, contact_id, title, details, now, now))
            db.execute("UPDATE contacts SET version=version+1,updated_at=? WHERE id=?",
                       (now, contact_id))
            return dict(db.execute("SELECT * FROM contact_plans WHERE id=?",
                                   (identifier,)).fetchone())

    def update_plan(self, contact_id: str, plan_id: str, version: int,
                    title: str, details: str, status: str) -> dict:
        title = _clean(title, "Vorhaben", 160)
        details = _clean(details, "Details", 4000, required=False)
        if status not in {"open", "completed"}:
            raise ValueError("Vorhabenstatus muss offen oder erledigt sein")
        with self.db.connect() as db:
            now = utcnow()
            changed = db.execute("UPDATE contact_plans SET title=?,details=?,status=?,"
                                 "version=version+1,updated_at=? WHERE id=? AND contact_id=? "
                                 "AND version=?",
                                 (title, details, status, now, plan_id, contact_id, version))
            if not changed.rowcount:
                self._missing_or_conflict(db, "contact_plans", plan_id, version, contact_id)
            db.execute("UPDATE contacts SET version=version+1,updated_at=? WHERE id=?",
                       (now, contact_id))
            return dict(db.execute("SELECT * FROM contact_plans WHERE id=?", (plan_id,)).fetchone())

    def delete_plan(self, contact_id: str, plan_id: str, version: int) -> None:
        with self.db.connect() as db:
            deleted = db.execute("DELETE FROM contact_plans WHERE id=? AND contact_id=? "
                                 "AND version=?", (plan_id, contact_id, version))
            if not deleted.rowcount:
                self._missing_or_conflict(db, "contact_plans", plan_id, version, contact_id)
            db.execute("UPDATE contacts SET version=version+1,updated_at=? WHERE id=?",
                       (utcnow(), contact_id))

    def link_reminder(self, contact_id: str, plan_id: str, plan_version: int,
                      task_id: str) -> dict:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            plan = db.execute("SELECT version,status FROM contact_plans WHERE id=? AND contact_id=?",
                              (plan_id, contact_id)).fetchone()
            if plan is None:
                raise LookupError("Vorhaben nicht gefunden")
            if plan["version"] != plan_version:
                raise ContactConflict("Vorhaben wurde inzwischen geändert")
            if plan["status"] != "open":
                raise ValueError("Nur offene Vorhaben können eine Erinnerung erhalten")
            task = db.execute("SELECT id,title,status,due_utc,due_local,timezone FROM tasks "
                              "WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise LookupError("Aufgabe nicht gefunden")
            if (task["status"] != "open" or not task["due_utc"]
                    or datetime.fromisoformat(task["due_utc"]) <= datetime.now(UTC)):
                raise ValueError("Nur offene Aufgaben mit zukünftiger Erinnerung können verknüpft werden")
            now = utcnow()
            try:
                db.execute("""INSERT INTO contact_plan_reminders(
                    plan_id,task_id,task_title_snapshot,due_utc_snapshot,
                    due_local_snapshot,timezone_snapshot,linked_at)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(plan_id) DO UPDATE SET
                    task_id=excluded.task_id,task_title_snapshot=excluded.task_title_snapshot,
                    due_utc_snapshot=excluded.due_utc_snapshot,
                    due_local_snapshot=excluded.due_local_snapshot,
                    timezone_snapshot=excluded.timezone_snapshot,
                    linked_at=excluded.linked_at""",
                    (plan_id, task_id, task["title"], task["due_utc"],
                     task["due_local"], task["timezone"], now))
            except sqlite3.IntegrityError as exc:
                raise ContactConflict("Diese Aufgabe ist bereits mit einem Vorhaben verbunden") from exc
            db.execute("UPDATE contact_plans SET version=version+1,updated_at=? WHERE id=?",
                       (now, plan_id))
            db.execute("UPDATE contacts SET version=version+1,updated_at=? WHERE id=?",
                       (now, contact_id))
        return self._plan(contact_id, plan_id)

    def unlink_reminder(self, contact_id: str, plan_id: str, plan_version: int) -> dict:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            plan = db.execute("SELECT version FROM contact_plans WHERE id=? AND contact_id=?",
                              (plan_id, contact_id)).fetchone()
            if plan is None:
                raise LookupError("Vorhaben nicht gefunden")
            if plan["version"] != plan_version:
                raise ContactConflict("Vorhaben wurde inzwischen geändert")
            removed = db.execute("DELETE FROM contact_plan_reminders WHERE plan_id=?",
                                 (plan_id,))
            if not removed.rowcount:
                raise LookupError("Keine Erinnerung mit diesem Vorhaben verbunden")
            now = utcnow()
            db.execute("UPDATE contact_plans SET version=version+1,updated_at=? WHERE id=?",
                       (now, plan_id))
            db.execute("UPDATE contacts SET version=version+1,updated_at=? WHERE id=?",
                       (now, contact_id))
        return self._plan(contact_id, plan_id)

    def _plan(self, contact_id: str, plan_id: str) -> dict:
        return next(plan for plan in self.get(contact_id)["plans"] if plan["id"] == plan_id)

    def recipient_snapshot(self, contact_id: str, expected_version: int,
                           note_ids: list[str]) -> dict:
        if expected_version < 1 or len(note_ids) > 5 or len(set(note_ids)) != len(note_ids):
            raise ValueError("Empfänger braucht eine aktuelle Version und höchstens fünf verschiedene Notizen")
        with self.db.connect() as db:
            db.execute("BEGIN")
            person = db.execute("SELECT id,name,context,relationship,version FROM contacts "
                                "WHERE id=?", (contact_id,)).fetchone()
            if person is None:
                raise LookupError("Kontakt nicht gefunden")
            if person["version"] != expected_version:
                raise ContactConflict("Kontakt wurde inzwischen geändert; Empfänger neu auswählen")
            notes = []
            for note_id in note_ids:
                note = db.execute("SELECT id,content,source_kind,version,updated_at "
                                  "FROM contact_notes WHERE id=? AND contact_id=?",
                                  (note_id, contact_id)).fetchone()
                if note is None:
                    raise ValueError("Ausgewählte Notiz gehört nicht mehr zu diesem Kontakt")
                notes.append(dict(note))
            if sum(len(note["content"]) for note in notes) > 3000:
                raise ValueError("Ausgewählte Kontaktnotizen überschreiten 3000 Zeichen")
            return {**dict(person), "notes": notes}

    @staticmethod
    def _missing_or_conflict(db: sqlite3.Connection, table: str, identifier: str,
                             version: int, contact_id: str | None = None) -> None:
        if version < 1:
            raise ValueError("Version muss positiv sein")
        if table == "contacts":
            row = db.execute("SELECT 1 FROM contacts WHERE id=?", (identifier,)).fetchone()
        elif table == "contact_notes":
            row = db.execute("SELECT 1 FROM contact_notes WHERE id=? AND contact_id=?",
                             (identifier, contact_id)).fetchone()
        else:
            row = db.execute("SELECT 1 FROM contact_plans WHERE id=? AND contact_id=?",
                             (identifier, contact_id)).fetchone()
        if row is None:
            raise LookupError("Kontakt oder Eintrag nicht gefunden")
        raise ContactConflict("Kontakt oder Eintrag wurde inzwischen geändert")
