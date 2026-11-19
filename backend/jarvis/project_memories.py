"""Bewusste, belegte Verknüpfung einer lokalen Erinnerung mit einem Projekt."""

import hashlib
import json
from uuid import uuid4

from jarvis.db import Database, utcnow
from jarvis.project_validity import clean_metadata, local_date, validity_status
from jarvis.work_states import WorkStateStore

MAX_MEMORY_LINKS_PER_STATE = 10


def memory_sha256(memory: dict) -> str:
    payload = json.dumps([memory["text"], memory["source_ref"], memory["policy"]],
                         ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ProjectMemoryConflict(ValueError):
    """Eine zwischenzeitliche Änderung verhindert das Überschreiben."""


class ProjectMemoryStore:
    def __init__(self, db: Database, work_states: WorkStateStore,
                 timezone_provider=None):
        self.db = db
        self.work_states = work_states
        self.timezone_provider = timezone_provider or (lambda: "Europe/Paris")

    @staticmethod
    def _clean(source_note: str, confidence: str, valid_from: str | None,
               valid_until: str | None) -> tuple[str, str, str | None, str | None]:
        source_note = " ".join(source_note.split())
        if not 5 <= len(source_note) <= 500:
            raise ValueError("Herkunftsnotiz muss 5–500 Zeichen enthalten")
        confidence, valid_from, valid_until = clean_metadata(confidence, valid_from, valid_until)
        return source_note, confidence, valid_from, valid_until

    def _state(self, state_id: str, expected_version: int) -> dict:
        state = self.work_states.get(state_id)
        if state is None:
            raise LookupError("Arbeitsstand nicht gefunden")
        if state["version"] != expected_version:
            raise ProjectMemoryConflict("Arbeitsstand wurde zwischenzeitlich geändert")
        return state

    @staticmethod
    def _check_state(db, state_id: str, expected_version: int) -> None:
        row = db.execute("SELECT version FROM work_states WHERE id=?", (state_id,)).fetchone()
        if row is None:
            raise LookupError("Arbeitsstand nicht gefunden")
        if row["version"] != expected_version:
            raise ProjectMemoryConflict("Arbeitsstand wurde zwischenzeitlich geändert")

    @staticmethod
    def _memory(db, memory_id: str) -> dict | None:
        row = db.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        return dict(row) if row else None

    def list_for_state(self, state_id: str, state: dict | None = None) -> list[dict]:
        state = state if state is not None else self.work_states.get(state_id)
        if state is None:
            raise LookupError("Arbeitsstand nicht gefunden")
        if state["id"] != state_id:
            raise ValueError("Arbeitsstand und Projekt-ID stimmen nicht überein")
        today = local_date(self.timezone_provider())
        with self.db.connect() as db:
            rows = [dict(row) for row in db.execute("""SELECT * FROM project_memory_links
                WHERE state_id=? ORDER BY created_at,id""", (state_id,))]
            current = {row["memory_id"]: self._memory(db, row["memory_id"]) for row in rows}
        result = []
        for row in rows:
            memory = current[row["memory_id"]]
            status = ("geloescht" if memory is None else
                      "unveraendert" if memory_sha256(memory) == row["memory_sha256"]
                      else "geaendert")
            result.append({**row, "memory_status": status,
                           "current_memory_text": memory["text"] if memory else None,
                           "current_memory_source_ref": memory["source_ref"] if memory else None,
                           "current_memory_policy": memory["policy"] if memory else None,
                           "validity_status": validity_status(row["valid_from"],
                                                               row["valid_until"], today)})
        return result

    def add(self, state_id: str, expected_state_version: int, memory_id: str,
            source_note: str, confidence: str = "unverified",
            valid_from: str | None = None, valid_until: str | None = None) -> dict:
        source_note, confidence, valid_from, valid_until = self._clean(
            source_note, confidence, valid_from, valid_until)
        self._state(state_id, expected_state_version)
        link_id, now = str(uuid4()), utcnow()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_state(db, state_id, expected_state_version)
            memory = self._memory(db, memory_id)
            if memory is None:
                raise ValueError("Erinnerung nicht gefunden")
            count = db.execute("SELECT count(*) FROM project_memory_links WHERE state_id=?",
                               (state_id,)).fetchone()[0]
            if count >= MAX_MEMORY_LINKS_PER_STATE:
                raise ValueError("Höchstens zehn Erinnerungen pro Projekt")
            if db.execute("""SELECT 1 FROM project_memory_links
                WHERE state_id=? AND memory_id=?""", (state_id, memory_id)).fetchone():
                raise ValueError("Erinnerung ist bereits mit diesem Projekt verbunden")
            db.execute("""INSERT INTO project_memory_links
                (id,state_id,memory_id,memory_text,memory_source_ref,memory_policy,
                 memory_sha256,source_note,confidence,valid_from,valid_until,
                 version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                (link_id, state_id, memory_id, memory["text"], memory["source_ref"],
                 memory["policy"], memory_sha256(memory), source_note, confidence,
                 valid_from, valid_until, now, now))
        return next(row for row in self.list_for_state(state_id) if row["id"] == link_id)

    def update(self, state_id: str, link_id: str, expected_state_version: int,
               expected_version: int, source_note: str, confidence: str = "unverified",
               valid_from: str | None = None, valid_until: str | None = None) -> dict:
        source_note, confidence, valid_from, valid_until = self._clean(
            source_note, confidence, valid_from, valid_until)
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_state(db, state_id, expected_state_version)
            row = db.execute("SELECT version FROM project_memory_links WHERE id=? AND state_id=?",
                             (link_id, state_id)).fetchone()
            if row is None:
                raise LookupError("Projekt-Erinnerung nicht gefunden")
            if row["version"] != expected_version:
                raise ProjectMemoryConflict("Projekt-Erinnerung wurde zwischenzeitlich geändert")
            db.execute("""UPDATE project_memory_links SET source_note=?,confidence=?,
                valid_from=?,valid_until=?,version=version+1,updated_at=?
                WHERE id=? AND state_id=?""",
                (source_note, confidence, valid_from, valid_until, utcnow(), link_id, state_id))
        return next(row for row in self.list_for_state(state_id) if row["id"] == link_id)

    def confirm(self, state_id: str, link_id: str, expected_state_version: int,
                expected_version: int) -> dict:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_state(db, state_id, expected_state_version)
            row = db.execute("SELECT * FROM project_memory_links WHERE id=? AND state_id=?",
                             (link_id, state_id)).fetchone()
            if row is None:
                raise LookupError("Projekt-Erinnerung nicht gefunden")
            if row["version"] != expected_version:
                raise ProjectMemoryConflict("Projekt-Erinnerung wurde zwischenzeitlich geändert")
            memory = self._memory(db, row["memory_id"])
            if memory is None:
                raise ProjectMemoryConflict("Gelöschte Erinnerung kann nicht bestätigt werden")
            db.execute("""UPDATE project_memory_links SET memory_text=?,memory_source_ref=?,
                memory_policy=?,memory_sha256=?,version=version+1,updated_at=?
                WHERE id=? AND state_id=?""",
                (memory["text"], memory["source_ref"], memory["policy"],
                 memory_sha256(memory), utcnow(), link_id, state_id))
        return next(row for row in self.list_for_state(state_id) if row["id"] == link_id)

    def delete(self, state_id: str, link_id: str, expected_state_version: int,
               expected_version: int) -> None:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_state(db, state_id, expected_state_version)
            row = db.execute("SELECT version FROM project_memory_links WHERE id=? AND state_id=?",
                             (link_id, state_id)).fetchone()
            if row is None:
                raise LookupError("Projekt-Erinnerung nicht gefunden")
            if row["version"] != expected_version:
                raise ProjectMemoryConflict("Projekt-Erinnerung wurde zwischenzeitlich geändert")
            db.execute("DELETE FROM project_memory_links WHERE id=? AND state_id=?",
                       (link_id, state_id))
