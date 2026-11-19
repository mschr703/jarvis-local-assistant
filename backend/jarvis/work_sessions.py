"""Manuell gesicherte Sitzungsnotizen mit unveränderlichen Quellen-Snapshots."""

from __future__ import annotations

import json
from uuid import uuid4

from jarvis.db import Database, utcnow
from jarvis.work_states import WorkStateConflict, WorkStateStore


class WorkSessionStore:
    def __init__(self, db: Database, states: WorkStateStore):
        self.db = db
        self.states = states

    @staticmethod
    def _selected(items: list[str], allowed: set[str], label: str) -> list[str]:
        if len(items) > 10 or len(items) != len(set(items)) or not set(items) <= allowed:
            raise ValueError(f"{label}: nur verschiedene Verweise aus dem Arbeitsstand wählen")
        return items

    def create(self, state_id: str, expected_state_version: int, result: str,
               open_questions: str, next_step: str, document_ids: list[str],
               task_ids: list[str], links: list[str]) -> dict:
        result, open_questions, next_step = (value.strip() for value in
                                             (result, open_questions, next_step))
        if not result or not next_step or len(result) > 5000 or len(open_questions) > 3000 \
                or len(next_step) > 2000:
            raise ValueError("Ergebnis und nächster Schritt sind Pflicht; Text ist zu lang")
        state = self.states.get(state_id)
        if state is None:
            raise LookupError("Arbeitsstand nicht gefunden")
        if state["version"] != expected_state_version:
            raise WorkStateConflict("Arbeitsstand wurde zwischenzeitlich geändert; erneut laden")
        self._selected(document_ids, {item["id"] for item in state["documents"]}, "Dateien")
        self._selected(task_ids, {item["id"] for item in state["tasks"]}, "Aufgaben")
        self._selected(links, set(state["links"]), "Links")
        documents = [item for item in state["documents"] if item["id"] in document_ids]
        tasks = [item for item in state["tasks"] if item["id"] in task_ids]
        if any(item["status"] != "unveraendert" for item in documents):
            raise WorkStateConflict("Ausgewählte Datei ist nicht mehr unverändert verfügbar")
        if any(item["status"] == "geloescht" for item in tasks):
            raise WorkStateConflict("Ausgewählte Aufgabe existiert nicht mehr")
        references = {
            "documents": [{key: item[key] for key in ("id", "path", "sha256")}
                          for item in documents],
            "tasks": [{**{key: item.get(key) for key in
                         ("id", "title", "due_utc", "due_local", "timezone",
                          "due_offset", "due_fold")}, "saved_status": item["status"]}
                      for item in tasks],
            "links": links,
        }
        session_id, now = str(uuid4()), utcnow()
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT version FROM work_states WHERE id=?",
                                         (state_id,)).fetchone()
            if current is None:
                raise LookupError("Arbeitsstand nicht gefunden")
            if current["version"] != expected_state_version:
                raise WorkStateConflict("Arbeitsstand wurde zwischenzeitlich geändert; erneut laden")
            connection.execute("""INSERT INTO work_sessions
                (id,state_id,state_version,project_snapshot,result,open_questions,
                 next_step,references_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (session_id, state_id, expected_state_version, state["project"], result,
                 open_questions, next_step, json.dumps(references, ensure_ascii=False), now))
        self.db.log("work_session_created", f"Arbeitsstand {state_id}: Notiz {session_id}")
        return self.get(state_id, session_id)

    def get(self, state_id: str, session_id: str) -> dict | None:
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM work_sessions WHERE state_id=? AND id=?",
                                     (state_id, session_id)).fetchone()
        if row is None:
            return None
        return self._decorate(dict(row))

    def list(self, state_id: str, limit: int = 30) -> list[dict]:
        state = self.states.get(state_id)
        if state is None:
            raise LookupError("Arbeitsstand nicht gefunden")
        with self.db.connect() as connection:
            rows = [dict(row) for row in connection.execute("""SELECT * FROM work_sessions
                WHERE state_id=? ORDER BY created_at DESC,id DESC LIMIT ?""",
                (state_id, min(max(limit, 1), 50)))]
        file_cache: dict[tuple[str, str, str], dict] = {}
        task_cache: dict[str, dict | None] = {}
        return [self._decorate(row, state, file_cache, task_cache) for row in rows]

    def _decorate(self, row: dict, state: dict | None = None,
                  file_cache: dict[tuple[str, str, str], dict] | None = None,
                  task_cache: dict[str, dict | None] | None = None) -> dict:
        state = state or self.states.get(row["state_id"])
        if state is None:
            raise LookupError("Arbeitsstand nicht gefunden")
        refs = json.loads(row.pop("references_json"))
        current_docs = {item["id"] for item in state["documents"]}
        current_tasks = {item["id"] for item in state["tasks"]}
        file_cache = file_cache if file_cache is not None else {}
        task_cache = task_cache if task_cache is not None else {}
        documents = []
        for item in refs["documents"]:
            key = (item["id"], item["path"], item["sha256"])
            if key not in file_cache:
                file_cache[key] = self.states._document_status(item)
            documents.append({**file_cache[key], "in_state": item["id"] in current_docs})
        row["documents"] = documents
        tasks = []
        for item in refs["tasks"]:
            if item["id"] not in task_cache:
                task_cache[item["id"]] = self.states.tasks.get_task(item["id"])
            tasks.append({**self.states._task_status(item, task_cache[item["id"]]),
                          "in_state": item["id"] in current_tasks})
        row["tasks"] = tasks
        row["links"] = [{"url": link, "in_state": link in state["links"],
                         "status": "nicht_geprueft"} for link in refs["links"]]
        row["state_changed"] = state["version"] != row["state_version"]
        return row

    def delete(self, state_id: str, session_id: str) -> None:
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute("DELETE FROM work_sessions WHERE state_id=? AND id=?",
                                        (state_id, session_id))
            if result.rowcount != 1:
                raise LookupError("Sitzungsnotiz nicht gefunden")
        self.db.log("work_session_deleted", f"Arbeitsstand {state_id}: Notiz {session_id}")
