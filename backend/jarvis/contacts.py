"""Manually confirmed personal contacts and notes, stored only on this device."""

from __future__ import annotations

import sqlite3
import unicodedata
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
            return {**dict(row), "notes": [dict(note) for note in notes],
                    "plans": [dict(plan) for plan in plans]}

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
