"""Ausdrücklich belegte Personenbeziehungen zu lokalen Projekt-Arbeitsständen."""

from collections.abc import Callable
from datetime import date
from uuid import uuid4

from jarvis.db import Database, utcnow
from jarvis.project_validity import clean_metadata, local_date, validity_status
from jarvis.work_states import WorkStateStore

RELATIONS = {"lead", "contributor", "contact", "reviewer", "other"}
MAX_PEOPLE_PER_STATE = 20


class ProjectPersonConflict(ValueError):
    """Der Projektstand oder die Beziehung wurde zwischenzeitlich geändert."""


class ProjectPeopleStore:
    def __init__(self, db: Database, work_states: WorkStateStore,
                 timezone_provider: Callable[[], str] | None = None):
        self.db = db
        self.work_states = work_states
        self.timezone_provider = timezone_provider or (lambda: "Europe/Paris")

    @staticmethod
    def _clean(name: str, relation_type: str, source_note: str) -> tuple[str, str, str]:
        name = " ".join(name.split())
        source_note = " ".join(source_note.split())
        if not 1 <= len(name) <= 160:
            raise ValueError("Personenname muss 1–160 Zeichen enthalten")
        if relation_type not in RELATIONS:
            raise ValueError("Unbekannte Projektbeziehung")
        if not 5 <= len(source_note) <= 500:
            raise ValueError("Herkunftsnotiz muss 5–500 Zeichen enthalten")
        return name, relation_type, source_note

    def _state_and_source(self, state_id: str, expected_state_version: int,
                          source_document_id: str | None) -> tuple[dict, tuple[str | None, str | None]]:
        state = self.work_states.get(state_id)
        if state is None:
            raise LookupError("Arbeitsstand nicht gefunden")
        if state["version"] != expected_state_version:
            raise ProjectPersonConflict("Arbeitsstand wurde zwischenzeitlich geändert")
        if source_document_id is None:
            return state, (None, None)
        source = next((item for item in state["documents"]
                       if item["id"] == source_document_id), None)
        if source is None:
            raise ValueError("Quelldatei ist diesem Projekt nicht zugeordnet")
        if source["status"] != "unveraendert":
            raise ProjectPersonConflict("Quelldatei wurde verändert oder ihre Freigabe entzogen")
        return state, (source["path"], source["sha256"])

    @staticmethod
    def _check_state(db, state_id: str, expected_state_version: int) -> None:
        row = db.execute("SELECT version FROM work_states WHERE id=?", (state_id,)).fetchone()
        if row is None:
            raise LookupError("Arbeitsstand nicht gefunden")
        if row["version"] != expected_state_version:
            raise ProjectPersonConflict("Arbeitsstand wurde zwischenzeitlich geändert")

    @staticmethod
    def _check_duplicate(db, state_id: str, name: str, except_id: str | None = None) -> None:
        rows = db.execute("SELECT id,name FROM project_people WHERE state_id=?", (state_id,))
        if any(row["id"] != except_id and row["name"].casefold() == name.casefold()
               for row in rows):
            raise ValueError("Diese Person ist dem Projekt bereits zugeordnet")
        if any(row["alias"].casefold() == name.casefold()
               for row in db.execute("SELECT alias FROM project_person_aliases WHERE state_id=?",
                                     (state_id,))):
            raise ValueError("Personenname ist bereits als Alias im Projekt eingetragen")

    @staticmethod
    def _decorate(row: dict, state: dict, as_of: date) -> dict:
        source_id = row["source_document_id"]
        if source_id is None:
            status = "eigene_angabe"
        else:
            current = next((item for item in state["documents"] if item["id"] == source_id), None)
            if current is None:
                status = "nicht_mehr_zugeordnet"
            elif (current["path"], current["sha256"]) != (
                    row["source_path"], row["source_sha256"]):
                status = "quelle_erneuert"
            else:
                status = current["status"]
        return {**row, "source_status": status,
                "validity_status": validity_status(row["valid_from"], row["valid_until"], as_of)}

    def list_for_state(self, state_id: str, state: dict | None = None,
                       as_of: date | None = None) -> list[dict]:
        state = state if state is not None else self.work_states.get(state_id)
        if state is None:
            raise LookupError("Arbeitsstand nicht gefunden")
        if state["id"] != state_id:
            raise ValueError("Arbeitsstand und Projekt-ID stimmen nicht überein")
        with self.db.connect() as db:
            rows = [dict(row) for row in db.execute("""SELECT * FROM project_people
                WHERE state_id=? ORDER BY created_at,id""", (state_id,))]
        as_of = as_of or local_date(self.timezone_provider())
        return [self._decorate(row, state, as_of) for row in rows]

    def add(self, state_id: str, expected_state_version: int, name: str,
            relation_type: str, source_note: str, source_document_id: str | None,
            confidence: str = "unverified", valid_from: str | None = None,
            valid_until: str | None = None) -> dict:
        name, relation_type, source_note = self._clean(name, relation_type, source_note)
        confidence, valid_from, valid_until = clean_metadata(confidence, valid_from, valid_until)
        _, (source_path, source_sha256) = self._state_and_source(
            state_id, expected_state_version, source_document_id)
        person_id, now = str(uuid4()), utcnow()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_state(db, state_id, expected_state_version)
            count = db.execute("SELECT count(*) FROM project_people WHERE state_id=?",
                               (state_id,)).fetchone()[0]
            if count >= MAX_PEOPLE_PER_STATE:
                raise ValueError("Höchstens 20 Personenbeziehungen pro Arbeitsstand")
            self._check_duplicate(db, state_id, name)
            db.execute("""INSERT INTO project_people
                (id,state_id,name,relation_type,source_note,source_document_id,
                source_path,source_sha256,version,created_at,updated_at,
                confidence,valid_from,valid_until)
                VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?)""",
                (person_id, state_id, name, relation_type, source_note,
                 source_document_id, source_path, source_sha256, now, now,
                 confidence, valid_from, valid_until))
        return next(item for item in self.list_for_state(state_id) if item["id"] == person_id)

    def update(self, state_id: str, person_id: str, expected_state_version: int,
               expected_version: int, name: str, relation_type: str,
               source_note: str, source_document_id: str | None,
               confidence: str = "unverified", valid_from: str | None = None,
               valid_until: str | None = None) -> dict:
        name, relation_type, source_note = self._clean(name, relation_type, source_note)
        confidence, valid_from, valid_until = clean_metadata(confidence, valid_from, valid_until)
        with self.db.connect() as db:
            if db.execute("SELECT 1 FROM project_people WHERE id=? AND state_id=?",
                          (person_id, state_id)).fetchone() is None:
                raise LookupError("Personenbeziehung nicht gefunden")
        _, (source_path, source_sha256) = self._state_and_source(
            state_id, expected_state_version, source_document_id)
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_state(db, state_id, expected_state_version)
            row = db.execute("SELECT version FROM project_people WHERE id=? AND state_id=?",
                             (person_id, state_id)).fetchone()
            if row is None:
                raise LookupError("Personenbeziehung nicht gefunden")
            if row["version"] != expected_version:
                raise ProjectPersonConflict("Personenbeziehung wurde zwischenzeitlich geändert")
            self._check_duplicate(db, state_id, name, person_id)
            db.execute("""UPDATE project_people SET name=?,relation_type=?,source_note=?,
                source_document_id=?,source_path=?,source_sha256=?,confidence=?,
                valid_from=?,valid_until=?,version=version+1,
                updated_at=? WHERE id=? AND state_id=?""",
                (name, relation_type, source_note, source_document_id, source_path,
                 source_sha256, confidence, valid_from, valid_until,
                 utcnow(), person_id, state_id))
        return next(item for item in self.list_for_state(state_id) if item["id"] == person_id)

    def delete(self, state_id: str, person_id: str, expected_state_version: int,
               expected_version: int) -> None:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_state(db, state_id, expected_state_version)
            row = db.execute("SELECT version FROM project_people WHERE id=? AND state_id=?",
                             (person_id, state_id)).fetchone()
            if row is None:
                raise LookupError("Personenbeziehung nicht gefunden")
            if row["version"] != expected_version:
                raise ProjectPersonConflict("Personenbeziehung wurde zwischenzeitlich geändert")
            if db.execute("""SELECT 1 FROM project_person_merges
                WHERE state_id=? AND target_person_id=? AND split_at IS NULL LIMIT 1""",
                          (state_id, person_id)).fetchone():
                raise ProjectPersonConflict("Aktive Zusammenführung zuerst trennen")
            db.execute("DELETE FROM project_people WHERE id=? AND state_id=?",
                       (person_id, state_id))
