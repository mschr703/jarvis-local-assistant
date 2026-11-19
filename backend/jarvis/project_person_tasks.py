"""Explizite, belegte Beziehungen zwischen Projektpersonen und Projektaufgaben."""

import json
from collections.abc import Callable
from datetime import date
from uuid import uuid4

from jarvis.db import Database, utcnow
from jarvis.project_people import ProjectPeopleStore
from jarvis.project_validity import clean_metadata, local_date, validity_status
from jarvis.work_states import WorkStateStore

RELATIONS = {"responsible", "involved", "reviewer"}
MAX_LINKS_PER_STATE = 30


class ProjectPersonTaskConflict(ValueError):
    """Ein veralteter Stand oder Link darf nicht überschrieben werden."""


class ProjectPersonTaskStore:
    def __init__(self, db: Database, work_states: WorkStateStore,
                 people: ProjectPeopleStore,
                 timezone_provider: Callable[[], str] | None = None):
        self.db = db
        self.work_states = work_states
        self.people = people
        self.timezone_provider = timezone_provider or (lambda: "Europe/Paris")

    @staticmethod
    def _clean(relation_type: str, source_note: str) -> tuple[str, str]:
        source_note = " ".join(source_note.split())
        if relation_type not in RELATIONS:
            raise ValueError("Unbekannte Aufgabenbeziehung")
        if not 5 <= len(source_note) <= 500:
            raise ValueError("Herkunftsnotiz muss 5–500 Zeichen enthalten")
        return relation_type, source_note

    def _references(self, state_id: str, expected_state_version: int,
                    person_id: str, task_id: str, source_document_id: str | None
                    ) -> tuple[dict, dict, dict, tuple[str | None, str | None]]:
        state = self.work_states.get(state_id)
        if state is None:
            raise LookupError("Arbeitsstand nicht gefunden")
        if state["version"] != expected_state_version:
            raise ProjectPersonTaskConflict("Arbeitsstand wurde zwischenzeitlich geändert")
        person = next((item for item in self.people.list_for_state(state_id)
                       if item["id"] == person_id), None)
        if person is None:
            raise ValueError("Person gehört nicht zu diesem Arbeitsstand")
        task = next((item for item in state["tasks"] if item["id"] == task_id), None)
        if task is None or task["status"] == "geloescht":
            raise ValueError("Aufgabe gehört nicht mehr zu diesem Arbeitsstand")
        if source_document_id is None:
            return state, person, task, (None, None)
        source = next((item for item in state["documents"]
                       if item["id"] == source_document_id), None)
        if source is None:
            raise ValueError("Quelldatei gehört nicht zu diesem Arbeitsstand")
        if source["status"] != "unveraendert":
            raise ProjectPersonTaskConflict("Quelldatei wurde verändert oder ihre Freigabe entzogen")
        return state, person, task, (source["path"], source["sha256"])

    @staticmethod
    def _check_state(db, state_id: str, version: int) -> None:
        row = db.execute("SELECT version FROM work_states WHERE id=?", (state_id,)).fetchone()
        if row is None:
            raise LookupError("Arbeitsstand nicht gefunden")
        if row["version"] != version:
            raise ProjectPersonTaskConflict("Arbeitsstand wurde zwischenzeitlich geändert")

    @staticmethod
    def _check_duplicate(db, state_id: str, person_id: str, task_id: str,
                         relation_type: str, except_id: str | None = None) -> None:
        row = db.execute("""SELECT id FROM project_person_tasks
            WHERE state_id=? AND person_id=? AND task_id=? AND relation_type=?""",
                         (state_id, person_id, task_id, relation_type)).fetchone()
        if row is not None and row["id"] != except_id:
            raise ValueError("Diese Aufgabenbeziehung ist bereits eingetragen")

    @staticmethod
    def _source_status(row: dict, state: dict) -> str:
        source_id = row["source_document_id"]
        if source_id is None:
            return "eigene_angabe"
        current = next((item for item in state["documents"] if item["id"] == source_id), None)
        if current is None:
            return "nicht_mehr_zugeordnet"
        if (current["path"], current["sha256"]) != (
                row["source_path"], row["source_sha256"]):
            return "quelle_erneuert"
        return current["status"]

    def list_for_state(self, state_id: str, state: dict | None = None,
                       person_rows: list[dict] | None = None,
                       as_of: date | None = None) -> list[dict]:
        state = state if state is not None else self.work_states.get(state_id)
        if state is None:
            raise LookupError("Arbeitsstand nicht gefunden")
        if state["id"] != state_id:
            raise ValueError("Arbeitsstand und Projekt-ID stimmen nicht überein")
        as_of = as_of or local_date(self.timezone_provider())
        person_rows = (person_rows if person_rows is not None else
                       self.people.list_for_state(state_id, state, as_of))
        people = {item["id"]: item for item in person_rows}
        tasks = {item["id"]: item for item in state["tasks"]}
        with self.db.connect() as db:
            rows = [dict(row) for row in db.execute("""SELECT * FROM project_person_tasks
                WHERE state_id=? ORDER BY created_at,id""", (state_id,))]
            merges = [dict(row) for row in db.execute("""SELECT source_snapshot_json,moved_links_json
                FROM project_person_merges WHERE state_id=? AND split_at IS NULL""", (state_id,))]
        merged_sources = {}
        for merge in merges:
            source = self.people._decorate(json.loads(merge["source_snapshot_json"]), state, as_of)
            for moved in json.loads(merge["moved_links_json"]):
                merged_sources[moved["id"]] = source
        result = []
        for row in rows:
            person = people.get(row["person_id"])
            # Bei normalem Löschen der Person entfernt der FK den Link. Ein
            # inkonsistenter Fremdschlüssel darf keine Zuordnung vortäuschen.
            if person is None:
                continue
            task = tasks.get(row["task_id"])
            source_status = self._source_status(row, state)
            validity = validity_status(row["valid_from"], row["valid_until"], as_of)
            merged_from = merged_sources.get(row["id"])
            if task is None:
                status = "aufgabe_nicht_mehr_zugeordnet"
            elif task["status"] == "geloescht":
                status = "aufgabe_geloescht"
            elif source_status not in {"eigene_angabe", "unveraendert"}:
                status = "beleg_pruefen"
            elif person["source_status"] not in {"eigene_angabe", "unveraendert"}:
                status = "person_beleg_pruefen"
            elif person["validity_status"] != "aktuell":
                status = "person_nicht_gueltig"
            elif merged_from and merged_from["source_status"] not in {"eigene_angabe", "unveraendert"}:
                status = "zusammengefuehrter_person_beleg_pruefen"
            elif merged_from and merged_from["validity_status"] != "aktuell":
                status = "zusammengefuehrte_person_nicht_gueltig"
            elif validity != "aktuell":
                status = validity
            else:
                status = "aktuell"
            result.append({**row, "person_name": person["name"],
                           "person_source_status": person["source_status"],
                           "task_status": task["status"] if task else "nicht_mehr_zugeordnet",
                           "current_task_title": task["current_title"] if task else None,
                           "source_status": source_status, "status": status,
                           "person_validity_status": person["validity_status"],
                           "merged_from_person_name": merged_from["name"] if merged_from else None,
                           "merged_from_source_status": merged_from["source_status"] if merged_from else None,
                           "merged_from_validity_status": merged_from["validity_status"] if merged_from else None,
                           "merged_from_confidence": merged_from["confidence"] if merged_from else None,
                           "validity_status": validity})
        return result

    def add(self, state_id: str, expected_state_version: int, person_id: str,
            task_id: str, relation_type: str, source_note: str,
            source_document_id: str | None, confidence: str = "unverified",
            valid_from: str | None = None, valid_until: str | None = None) -> dict:
        relation_type, source_note = self._clean(relation_type, source_note)
        confidence, valid_from, valid_until = clean_metadata(confidence, valid_from, valid_until)
        _, _, task, (source_path, source_sha256) = self._references(
            state_id, expected_state_version, person_id, task_id, source_document_id)
        link_id, now = str(uuid4()), utcnow()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_state(db, state_id, expected_state_version)
            if db.execute("SELECT 1 FROM project_people WHERE id=? AND state_id=?",
                          (person_id, state_id)).fetchone() is None:
                raise ProjectPersonTaskConflict("Person wurde zwischenzeitlich entfernt")
            count = db.execute("SELECT count(*) FROM project_person_tasks WHERE state_id=?",
                               (state_id,)).fetchone()[0]
            if count >= MAX_LINKS_PER_STATE:
                raise ValueError("Höchstens 30 Aufgabenbeziehungen pro Arbeitsstand")
            self._check_duplicate(db, state_id, person_id, task_id, relation_type)
            db.execute("""INSERT INTO project_person_tasks
                (id,state_id,person_id,task_id,task_title,relation_type,source_note,
                 source_document_id,source_path,source_sha256,version,created_at,updated_at,
                 confidence,valid_from,valid_until)
                 VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?)""",
                (link_id, state_id, person_id, task_id, task["title"], relation_type,
                 source_note, source_document_id, source_path, source_sha256, now, now,
                 confidence, valid_from, valid_until))
        return next(item for item in self.list_for_state(state_id) if item["id"] == link_id)

    def update(self, state_id: str, link_id: str, expected_state_version: int,
               expected_version: int, person_id: str, task_id: str,
               relation_type: str, source_note: str,
               source_document_id: str | None, confidence: str = "unverified",
               valid_from: str | None = None, valid_until: str | None = None) -> dict:
        relation_type, source_note = self._clean(relation_type, source_note)
        confidence, valid_from, valid_until = clean_metadata(confidence, valid_from, valid_until)
        with self.db.connect() as db:
            if db.execute("SELECT 1 FROM project_person_tasks WHERE id=? AND state_id=?",
                          (link_id, state_id)).fetchone() is None:
                raise LookupError("Aufgabenbeziehung nicht gefunden")
        _, _, task, (source_path, source_sha256) = self._references(
            state_id, expected_state_version, person_id, task_id, source_document_id)
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_state(db, state_id, expected_state_version)
            row = db.execute("SELECT version FROM project_person_tasks WHERE id=? AND state_id=?",
                             (link_id, state_id)).fetchone()
            if row is None:
                raise LookupError("Aufgabenbeziehung nicht gefunden")
            if row["version"] != expected_version:
                raise ProjectPersonTaskConflict("Aufgabenbeziehung wurde zwischenzeitlich geändert")
            if db.execute("SELECT 1 FROM project_people WHERE id=? AND state_id=?",
                          (person_id, state_id)).fetchone() is None:
                raise ProjectPersonTaskConflict("Person wurde zwischenzeitlich entfernt")
            self._check_duplicate(db, state_id, person_id, task_id, relation_type, link_id)
            db.execute("""UPDATE project_person_tasks SET person_id=?,task_id=?,task_title=?,
                relation_type=?,source_note=?,source_document_id=?,source_path=?,
                source_sha256=?,confidence=?,valid_from=?,valid_until=?,
                version=version+1,updated_at=? WHERE id=? AND state_id=?""",
                (person_id, task_id, task["title"], relation_type, source_note,
                 source_document_id, source_path, source_sha256, confidence,
                 valid_from, valid_until, utcnow(), link_id, state_id))
        return next(item for item in self.list_for_state(state_id) if item["id"] == link_id)

    def delete(self, state_id: str, link_id: str, expected_state_version: int,
               expected_version: int) -> None:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_state(db, state_id, expected_state_version)
            row = db.execute("SELECT version FROM project_person_tasks WHERE id=? AND state_id=?",
                             (link_id, state_id)).fetchone()
            if row is None:
                raise LookupError("Aufgabenbeziehung nicht gefunden")
            if row["version"] != expected_version:
                raise ProjectPersonTaskConflict("Aufgabenbeziehung wurde zwischenzeitlich geändert")
            db.execute("DELETE FROM project_person_tasks WHERE id=? AND state_id=?",
                       (link_id, state_id))
