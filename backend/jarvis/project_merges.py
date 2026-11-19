"""Bewusste, atomare und rückgängig machbare Projektpersonen-Zusammenführung."""

import json
import sqlite3
from uuid import uuid4

from jarvis.db import Database, utcnow
from jarvis.project_aliases import MAX_ALIASES_PER_PERSON, MAX_ALIASES_PER_STATE, ProjectAliasStore
from jarvis.project_people import MAX_PEOPLE_PER_STATE, ProjectPeopleStore, ProjectPersonConflict
from jarvis.work_states import WorkStateStore

MAX_MERGES_PER_STATE = 100


class ProjectMergeConflict(ValueError):
    """Ein späterer Eingriff lässt ein verlustfreies Trennen nicht mehr zu."""


class ProjectMergeStore:
    def __init__(self, db: Database, work_states: WorkStateStore):
        self.db = db
        self.work_states = work_states

    @staticmethod
    def _state(db: sqlite3.Connection, state_id: str, expected_version: int) -> None:
        try:
            ProjectPeopleStore._check_state(db, state_id, expected_version)
        except ProjectPersonConflict as exc:
            raise ProjectMergeConflict(str(exc)) from exc

    def list_for_state(self, state_id: str) -> list[dict]:
        if self.work_states.get(state_id) is None:
            raise LookupError("Arbeitsstand nicht gefunden")
        with self.db.connect() as db:
            rows = db.execute("""SELECT id,state_id,source_person_id,target_person_id,
                canonical_alias_id,target_version_after,created_at,split_at,
                json_extract(source_snapshot_json,'$.name') AS source_name,
                json_extract(source_snapshot_json,'$.relation_type') AS source_relation_type,
                (SELECT name FROM project_people WHERE id=target_person_id) AS target_name,
                json_array_length(moved_aliases_json) AS moved_alias_count,
                json_array_length(moved_links_json) AS moved_link_count
                FROM project_person_merges WHERE state_id=? ORDER BY created_at DESC,id DESC""",
                (state_id,))
            return [dict(row) for row in rows]

    def merge(self, state_id: str, state_version: int, source_id: str,
              source_version: int, target_id: str, target_version: int) -> dict:
        if source_id == target_id:
            raise ValueError("Quell- und Zielperson müssen verschieden sein")
        merge_id, alias_id, now = str(uuid4()), str(uuid4()), utcnow()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._state(db, state_id, state_version)
            source = db.execute("SELECT * FROM project_people WHERE id=? AND state_id=?",
                                (source_id, state_id)).fetchone()
            target = db.execute("SELECT * FROM project_people WHERE id=? AND state_id=?",
                                (target_id, state_id)).fetchone()
            if source is None or target is None:
                raise ValueError("Beide Personen müssen demselben Projekt angehören")
            if source["version"] != source_version or target["version"] != target_version:
                raise ProjectMergeConflict("Personenbeziehung wurde zwischenzeitlich geändert")
            active = db.execute("""SELECT 1 FROM project_person_merges
                WHERE state_id=? AND split_at IS NULL AND target_person_id IN (?,?) LIMIT 1""",
                (state_id, source_id, target_id)).fetchone()
            if active:
                raise ProjectMergeConflict("Eine der Personen ist bereits Teil einer aktiven Zusammenführung")
            history_count = db.execute("""SELECT count(*) FROM project_person_merges
                WHERE state_id=?""", (state_id,)).fetchone()[0]
            if history_count >= MAX_MERGES_PER_STATE:
                raise ValueError("Höchstens 100 Zusammenführungen pro Projekt")
            aliases = [dict(row) for row in db.execute("""SELECT id,version FROM project_person_aliases
                WHERE state_id=? AND person_id=? ORDER BY id""", (state_id, source_id))]
            links = [dict(row) for row in db.execute("""SELECT id,version,task_id,relation_type
                FROM project_person_tasks WHERE state_id=? AND person_id=? ORDER BY id""",
                (state_id, source_id))]
            target_alias_count = db.execute("""SELECT count(*) FROM project_person_aliases
                WHERE state_id=? AND person_id=?""", (state_id, target_id)).fetchone()[0]
            state_alias_count = db.execute("""SELECT count(*) FROM project_person_aliases
                WHERE state_id=?""", (state_id,)).fetchone()[0]
            if target_alias_count + len(aliases) + 1 > MAX_ALIASES_PER_PERSON or (
                    state_alias_count + 1 > MAX_ALIASES_PER_STATE):
                raise ValueError("Aliasgrenze erreicht: 5 je Person, 30 je Projekt")
            for link in links:
                duplicate = db.execute("""SELECT 1 FROM project_person_tasks
                    WHERE state_id=? AND person_id=? AND task_id=? AND relation_type=?""",
                    (state_id, target_id, link["task_id"], link["relation_type"])).fetchone()
                if duplicate:
                    raise ValueError("Beide Personen haben dieselbe Aufgabenbeziehung")
            db.execute("""UPDATE project_person_aliases SET person_id=?,version=version+1,updated_at=?
                WHERE state_id=? AND person_id=?""", (target_id, now, state_id, source_id))
            db.execute("""UPDATE project_person_tasks SET person_id=?,version=version+1,updated_at=?
                WHERE state_id=? AND person_id=?""", (target_id, now, state_id, source_id))
            db.execute("DELETE FROM project_people WHERE id=? AND state_id=?", (source_id, state_id))
            ProjectAliasStore._check_duplicate(db, state_id, source["name"])
            db.execute("""INSERT INTO project_person_aliases
                (id,state_id,person_id,alias,source_note,source_document_id,source_path,
                 source_sha256,confidence,valid_from,valid_until,version,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                (alias_id, state_id, target_id, source["name"], source["source_note"],
                 source["source_document_id"], source["source_path"], source["source_sha256"],
                 source["confidence"], source["valid_from"], source["valid_until"], now, now))
            db.execute("""UPDATE project_people SET version=version+1,updated_at=?
                WHERE id=? AND state_id=?""", (now, target_id, state_id))
            db.execute("""INSERT INTO project_person_merges
                (id,state_id,source_person_id,target_person_id,source_snapshot_json,
                 moved_aliases_json,moved_links_json,canonical_alias_id,target_version_after,
                 created_at,split_at) VALUES(?,?,?,?,?,?,?,?,?,?,NULL)""",
                (merge_id, state_id, source_id, target_id, json.dumps(dict(source)),
                 json.dumps(aliases), json.dumps(links), alias_id, target_version + 1, now))
        return next(row for row in self.list_for_state(state_id) if row["id"] == merge_id)

    def split(self, state_id: str, merge_id: str, state_version: int,
              target_version: int) -> dict:
        now = utcnow()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._state(db, state_id, state_version)
            merge = db.execute("SELECT * FROM project_person_merges WHERE id=? AND state_id=?",
                               (merge_id, state_id)).fetchone()
            if merge is None:
                raise LookupError("Zusammenführung nicht gefunden")
            if merge["split_at"] is not None:
                raise ProjectMergeConflict("Zusammenführung wurde bereits getrennt")
            target = db.execute("SELECT version FROM project_people WHERE id=? AND state_id=?",
                                (merge["target_person_id"], state_id)).fetchone()
            if target is None or target["version"] != target_version or (
                    target["version"] != merge["target_version_after"]):
                raise ProjectMergeConflict("Zielperson wurde seit der Zusammenführung geändert")
            source = json.loads(merge["source_snapshot_json"])
            aliases = json.loads(merge["moved_aliases_json"])
            links = json.loads(merge["moved_links_json"])
            canonical = db.execute("""SELECT * FROM project_person_aliases
                WHERE id=? AND state_id=? AND person_id=?""",
                (merge["canonical_alias_id"], state_id, merge["target_person_id"])).fetchone()
            if canonical is None or canonical["version"] != 1 or (
                    canonical["alias"] != source["name"]):
                raise ProjectMergeConflict("Ursprünglicher Name wurde seit der Zusammenführung geändert")
            if db.execute("SELECT 1 FROM project_people WHERE id=?", (source["id"],)).fetchone():
                raise ProjectMergeConflict("Ursprüngliche Personen-ID ist bereits belegt")
            if db.execute("SELECT count(*) FROM project_people WHERE state_id=?",
                          (state_id,)).fetchone()[0] >= MAX_PEOPLE_PER_STATE:
                raise ProjectMergeConflict("Personengrenze erreicht")
            if any(row["name"].casefold() == source["name"].casefold()
                   for row in db.execute("SELECT name FROM project_people WHERE state_id=?", (state_id,))):
                raise ProjectMergeConflict("Ursprünglicher Name ist inzwischen belegt")
            if any(row["alias"].casefold() == source["name"].casefold()
                   for row in db.execute("""SELECT alias FROM project_person_aliases
                       WHERE state_id=? AND id<>?""", (state_id, canonical["id"]))):
                raise ProjectMergeConflict("Ursprünglicher Name ist inzwischen als Alias belegt")
            for table, moved in (("project_person_aliases", aliases), ("project_person_tasks", links)):
                for item in moved:
                    row = db.execute(f"SELECT person_id,version FROM {table} WHERE id=? AND state_id=?",
                                     (item["id"], state_id)).fetchone()
                    if row is None or row["person_id"] != merge["target_person_id"] or (
                            row["version"] != item["version"] + 1):
                        raise ProjectMergeConflict("Eine übertragene Beziehung wurde geändert oder entfernt")
            columns = list(source)
            values = [source[key] for key in columns]
            values[columns.index("version")] += 1
            values[columns.index("updated_at")] = now
            db.execute(f"INSERT INTO project_people ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                       values)
            db.execute("DELETE FROM project_person_aliases WHERE id=?", (canonical["id"],))
            for table, moved in (("project_person_aliases", aliases), ("project_person_tasks", links)):
                for item in moved:
                    db.execute(f"""UPDATE {table} SET person_id=?,version=version+1,updated_at=?
                        WHERE id=? AND state_id=?""", (source["id"], now, item["id"], state_id))
            db.execute("""UPDATE project_people SET version=version+1,updated_at=?
                WHERE id=? AND state_id=?""", (now, merge["target_person_id"], state_id))
            db.execute("UPDATE project_person_merges SET split_at=? WHERE id=?", (now, merge_id))
        return next(row for row in self.list_for_state(state_id) if row["id"] == merge_id)
