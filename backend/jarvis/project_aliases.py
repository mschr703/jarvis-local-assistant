"""Manuell bestätigte Aliasnamen von Personen eines lokalen Arbeitsstands."""

from datetime import date
from uuid import uuid4

from jarvis.db import Database, utcnow
from jarvis.project_people import ProjectPeopleStore
from jarvis.project_validity import clean_metadata, local_date
from jarvis.work_states import WorkStateStore

MAX_ALIASES_PER_PERSON = 5
MAX_ALIASES_PER_STATE = 30


class ProjectAliasConflict(ValueError):
    """Eine zwischenzeitliche Änderung verhindert das Überschreiben."""


class ProjectAliasStore:
    def __init__(self, db: Database, work_states: WorkStateStore,
                 people: ProjectPeopleStore):
        self.db = db
        self.work_states = work_states
        self.people = people

    @staticmethod
    def _clean(alias: str, source_note: str, confidence: str,
               valid_from: str | None, valid_until: str | None
               ) -> tuple[str, str, str, str | None, str | None]:
        alias = " ".join(alias.split())
        source_note = " ".join(source_note.split())
        if not 1 <= len(alias) <= 160:
            raise ValueError("Aliasname muss 1–160 Zeichen enthalten")
        if not 5 <= len(source_note) <= 500:
            raise ValueError("Herkunftsnotiz muss 5–500 Zeichen enthalten")
        confidence, valid_from, valid_until = clean_metadata(confidence, valid_from, valid_until)
        return alias, source_note, confidence, valid_from, valid_until

    def _references(self, state_id: str, state_version: int, person_id: str,
                    person_version: int, source_document_id: str | None
                    ) -> tuple[dict, tuple[str | None, str | None]]:
        state = self.work_states.get(state_id)
        if state is None:
            raise LookupError("Arbeitsstand nicht gefunden")
        if state["version"] != state_version:
            raise ProjectAliasConflict("Arbeitsstand wurde zwischenzeitlich geändert")
        person = next((item for item in self.people.list_for_state(state_id, state)
                       if item["id"] == person_id), None)
        if person is None:
            raise ValueError("Person gehört nicht zu diesem Arbeitsstand")
        if person["version"] != person_version:
            raise ProjectAliasConflict("Personenbeziehung wurde zwischenzeitlich geändert")
        if source_document_id is None:
            return state, (None, None)
        source = next((item for item in state["documents"]
                       if item["id"] == source_document_id), None)
        if source is None:
            raise ValueError("Quelldatei gehört nicht zu diesem Arbeitsstand")
        if source["status"] != "unveraendert":
            raise ProjectAliasConflict("Quelldatei wurde verändert oder ihre Freigabe entzogen")
        return state, (source["path"], source["sha256"])

    @staticmethod
    def _check_versions(db, state_id: str, state_version: int,
                        person_id: str, person_version: int) -> None:
        state = db.execute("SELECT version FROM work_states WHERE id=?",
                           (state_id,)).fetchone()
        if state is None:
            raise LookupError("Arbeitsstand nicht gefunden")
        if state["version"] != state_version:
            raise ProjectAliasConflict("Arbeitsstand wurde zwischenzeitlich geändert")
        person = db.execute("SELECT version FROM project_people WHERE id=? AND state_id=?",
                            (person_id, state_id)).fetchone()
        if person is None:
            raise ProjectAliasConflict("Person wurde zwischenzeitlich entfernt")
        if person["version"] != person_version:
            raise ProjectAliasConflict("Personenbeziehung wurde zwischenzeitlich geändert")

    @staticmethod
    def _check_duplicate(db, state_id: str, alias: str,
                         except_id: str | None = None) -> None:
        folded = alias.casefold()
        if any(row["name"].casefold() == folded for row in db.execute(
                "SELECT name FROM project_people WHERE state_id=?", (state_id,))):
            raise ValueError("Aliasname entspricht bereits einem Personennamen")
        if any(row["id"] != except_id and row["alias"].casefold() == folded
               for row in db.execute("SELECT id,alias FROM project_person_aliases WHERE state_id=?",
                                     (state_id,))):
            raise ValueError("Dieser Alias ist im Projekt bereits eingetragen")

    def list_for_state(self, state_id: str, state: dict | None = None,
                       person_rows: list[dict] | None = None,
                       as_of: date | None = None) -> list[dict]:
        state = state if state is not None else self.work_states.get(state_id)
        if state is None:
            raise LookupError("Arbeitsstand nicht gefunden")
        if state["id"] != state_id:
            raise ValueError("Arbeitsstand und Projekt-ID stimmen nicht überein")
        as_of = as_of or local_date(self.people.timezone_provider())
        person_rows = (person_rows if person_rows is not None else
                       self.people.list_for_state(state_id, state, as_of))
        people = {item["id"]: item for item in person_rows}
        with self.db.connect() as db:
            rows = [dict(row) for row in db.execute("""SELECT * FROM project_person_aliases
                WHERE state_id=? ORDER BY created_at,id""", (state_id,))]
        result = []
        for row in rows:
            person = people.get(row["person_id"])
            if person is None:
                continue
            decorated = self.people._decorate(row, state, as_of)
            result.append({**decorated, "person_name": person["name"],
                           "person_version": person["version"]})
        return result

    def add(self, state_id: str, state_version: int, person_id: str,
            person_version: int, alias: str, source_note: str,
            source_document_id: str | None, confidence: str = "unverified",
            valid_from: str | None = None, valid_until: str | None = None) -> dict:
        alias, source_note, confidence, valid_from, valid_until = self._clean(
            alias, source_note, confidence, valid_from, valid_until)
        _, (source_path, source_sha256) = self._references(
            state_id, state_version, person_id, person_version, source_document_id)
        alias_id, now = str(uuid4()), utcnow()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_versions(db, state_id, state_version, person_id, person_version)
            count = db.execute("SELECT count(*) FROM project_person_aliases WHERE state_id=?",
                               (state_id,)).fetchone()[0]
            person_count = db.execute("""SELECT count(*) FROM project_person_aliases
                WHERE state_id=? AND person_id=?""", (state_id, person_id)).fetchone()[0]
            if count >= MAX_ALIASES_PER_STATE or person_count >= MAX_ALIASES_PER_PERSON:
                raise ValueError("Aliasgrenze erreicht: 5 je Person, 30 je Projekt")
            self._check_duplicate(db, state_id, alias)
            db.execute("""INSERT INTO project_person_aliases
                (id,state_id,person_id,alias,source_note,source_document_id,source_path,
                 source_sha256,confidence,valid_from,valid_until,version,created_at,updated_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                (alias_id, state_id, person_id, alias, source_note, source_document_id,
                 source_path, source_sha256, confidence, valid_from, valid_until, now, now))
        return next(item for item in self.list_for_state(state_id) if item["id"] == alias_id)

    def update(self, state_id: str, alias_id: str, state_version: int,
               person_id: str, person_version: int, expected_version: int,
               alias: str, source_note: str, source_document_id: str | None,
               confidence: str = "unverified", valid_from: str | None = None,
               valid_until: str | None = None) -> dict:
        alias, source_note, confidence, valid_from, valid_until = self._clean(
            alias, source_note, confidence, valid_from, valid_until)
        with self.db.connect() as db:
            row = db.execute("SELECT person_id FROM project_person_aliases WHERE id=? AND state_id=?",
                             (alias_id, state_id)).fetchone()
            if row is None:
                raise LookupError("Alias nicht gefunden")
            if row["person_id"] != person_id:
                raise ValueError("Alias kann nicht einer anderen Person zugeordnet werden")
        _, (source_path, source_sha256) = self._references(
            state_id, state_version, person_id, person_version, source_document_id)
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_versions(db, state_id, state_version, person_id, person_version)
            row = db.execute("SELECT version,person_id FROM project_person_aliases WHERE id=? AND state_id=?",
                             (alias_id, state_id)).fetchone()
            if row is None:
                raise LookupError("Alias nicht gefunden")
            if row["version"] != expected_version:
                raise ProjectAliasConflict("Alias wurde zwischenzeitlich geändert")
            if row["person_id"] != person_id:
                raise ProjectAliasConflict("Alias wurde zwischenzeitlich umgehängt")
            self._check_duplicate(db, state_id, alias, alias_id)
            db.execute("""UPDATE project_person_aliases SET alias=?,source_note=?,
                source_document_id=?,source_path=?,source_sha256=?,confidence=?,
                valid_from=?,valid_until=?,version=version+1,updated_at=?
                WHERE id=? AND state_id=?""",
                (alias, source_note, source_document_id, source_path, source_sha256,
                 confidence, valid_from, valid_until, utcnow(), alias_id, state_id))
        return next(item for item in self.list_for_state(state_id) if item["id"] == alias_id)

    def delete(self, state_id: str, alias_id: str, state_version: int,
               expected_version: int) -> None:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute("SELECT version FROM work_states WHERE id=?",
                               (state_id,)).fetchone()
            if state is None:
                raise LookupError("Arbeitsstand nicht gefunden")
            if state["version"] != state_version:
                raise ProjectAliasConflict("Arbeitsstand wurde zwischenzeitlich geändert")
            row = db.execute("SELECT version FROM project_person_aliases WHERE id=? AND state_id=?",
                             (alias_id, state_id)).fetchone()
            if row is None:
                raise LookupError("Alias nicht gefunden")
            if row["version"] != expected_version:
                raise ProjectAliasConflict("Alias wurde zwischenzeitlich geändert")
            db.execute("DELETE FROM project_person_aliases WHERE id=? AND state_id=?",
                       (alias_id, state_id))
