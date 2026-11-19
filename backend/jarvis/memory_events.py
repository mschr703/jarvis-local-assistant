"""Confirmed life events for a local, editable timeline."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from uuid import uuid4

from jarvis.db import Database, utcnow


class MemoryEventConflict(ValueError):
    """An event changed after it was opened."""


def _date(value: str, label: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{label} braucht ein Datum im Format JJJJ-MM-TT")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} ist kein gültiges Datum") from exc
    return value


def _one_line(value: str, label: str, maximum: int, *, required: bool = True) -> str:
    value = " ".join(value.split())
    if (required and not value) or len(value) > maximum or any(ord(char) < 32 for char in value):
        raise ValueError(f"{label} fehlt oder ist zu lang (maximal {maximum} Zeichen)")
    return value


def _description(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(value) > 5000 or any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise ValueError("Beschreibung ist zu lang oder enthält ungültige Zeichen")
    return value


def _key(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).casefold()
    return "".join(char for char in folded if not unicodedata.combining(char))


class MemoryEventStore:
    def __init__(self, db: Database):
        self.db = db

    def _filtered(self, query: str = "", from_date: str | None = None,
                  to_date: str | None = None, *, limit: int = 200) -> list[dict]:
        query = _one_line(query, "Suche", 200, required=False)
        if from_date is not None:
            from_date = _date(from_date, "Von")
        if to_date is not None:
            to_date = _date(to_date, "Bis")
        if from_date and to_date and from_date > to_date:
            raise ValueError("Von-Datum liegt nach Bis-Datum")
        with self.db.connect() as db:
            rows = db.execute("SELECT * FROM memory_events WHERE (? IS NULL OR date_local>=?) "
                              "AND (? IS NULL OR date_local<=?) "
                              "ORDER BY date_local DESC,id DESC",
                              (from_date, from_date, to_date, to_date)).fetchall()
        needle = _key(query)
        matched = []
        for row in rows:
            item = dict(row)
            if needle and not any(needle in _key(str(item[field])) for field in
                                  ("date_local", "title", "description", "location")):
                continue
            matched.append(item)
            if len(matched) > limit:
                if limit == 200:
                    return matched[:limit]
                raise ValueError("Für den Export sind zu viele Ereignisse ausgewählt")
        return matched

    def list(self, query: str = "", from_date: str | None = None,
             to_date: str | None = None) -> list[dict]:
        return self._filtered(query, from_date, to_date)

    def get(self, event_id: str) -> dict:
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM memory_events WHERE id=?", (event_id,)).fetchone()
            if row is None:
                raise LookupError("Erinnerungsereignis nicht gefunden")
            return dict(row)

    def create(self, date_local: str, title: str, description: str = "",
               location: str = "") -> dict:
        date_local = _date(date_local, "Ereignis")
        title = _one_line(title, "Titel", 160)
        description = _description(description)
        location = _one_line(location, "Ort", 160, required=False)
        identifier, now = str(uuid4()), utcnow()
        with self.db.connect() as db:
            db.execute("INSERT INTO memory_events(id,date_local,title,description,location,"
                       "created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                       (identifier, date_local, title, description, location, now, now))
        return self.get(identifier)

    def update(self, event_id: str, version: int, date_local: str, title: str,
               description: str = "", location: str = "") -> dict:
        date_local = _date(date_local, "Ereignis")
        title = _one_line(title, "Titel", 160)
        description = _description(description)
        location = _one_line(location, "Ort", 160, required=False)
        with self.db.connect() as db:
            changed = db.execute("UPDATE memory_events SET date_local=?,title=?,description=?,"
                                 "location=?,version=version+1,updated_at=? "
                                 "WHERE id=? AND version=?",
                                 (date_local, title, description, location,
                                  utcnow(), event_id, version))
            if not changed.rowcount:
                self._missing_or_conflict(db, event_id)
        return self.get(event_id)

    def delete(self, event_id: str, version: int) -> None:
        with self.db.connect() as db:
            deleted = db.execute("DELETE FROM memory_events WHERE id=? AND version=?",
                                 (event_id, version))
            if not deleted.rowcount:
                self._missing_or_conflict(db, event_id)

    @staticmethod
    def _missing_or_conflict(db, event_id: str) -> None:
        if db.execute("SELECT 1 FROM memory_events WHERE id=?", (event_id,)).fetchone():
            raise MemoryEventConflict("Ereignis wurde inzwischen geändert")
        raise LookupError("Erinnerungsereignis nicht gefunden")

    def export_markdown(self, query: str = "", from_date: str | None = None,
                        to_date: str | None = None) -> bytes:
        events = self._filtered(query, from_date, to_date, limit=1000)
        lines = ["# Meine Lebenschronik", "", "Manuell bestätigte Ereignisse.\n"]
        for event in events:
            lines.extend([f"## {event['date_local']} · {event['title']}", "",
                          f"Ort: {event['location'] or 'Nicht angegeben'}",
                          f"Quelle: manuell bestätigt · Version {event['version']}", ""])
            if event["description"]:
                lines.extend(f"> {line}" if line else ">" for line in
                             event["description"].split("\n"))
                lines.append("")
        return ("\n".join(lines).rstrip() + "\n").encode("utf-8")
