"""Lokale Projektprüfung und einmalige Vorbereitung eines bestätigten Schritts."""

import hashlib
import json
import re
from uuid import uuid4

from jarvis.db import Database, utcnow
from jarvis.tasks import TaskStore
from jarvis.work_states import WorkStateConflict, WorkStateStore


def step_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class ProjectCheckStore:
    def __init__(self, db: Database, work_states: WorkStateStore, tasks: TaskStore):
        self.db = db
        self.work_states = work_states
        self.tasks = tasks

    def check(self, state_id: str) -> dict | None:
        state = self.work_states.get(state_id)
        if state is None:
            return None
        blockers = []
        if len(state["next_step"]) > 300:
            blockers.append("Nächster Schritt ist als Aufgabentitel länger als 300 Zeichen")
        if not state["documents"]:
            blockers.append("Keine freigegebene Datei im Arbeitsstand ausgewählt")
        for document in state["documents"]:
            if document["status"] != "unveraendert":
                blockers.append(f"Datei nicht unverändert: {document['path']} ({document['status']})")
        with self.db.connect() as db:
            action = db.execute("""SELECT task_id,created_at FROM project_actions
                WHERE state_id=? AND step_hash=?""", (state_id, step_hash(state["next_step"]))).fetchone()
        task_id = action["task_id"] if action else None
        if task_id is None:
            for item in state["tasks"]:
                if step_hash(item["title"]) == step_hash(state["next_step"]):
                    task_id = item["id"]
                    break
        if task_id is None and len(state["tasks"]) >= 10:
            blockers.append("Höchstens zehn Aufgabenverweise im Arbeitsstand möglich")
        prepared_task = self.tasks.get_task(task_id) if task_id else None
        return {"state": state, "ready": not blockers, "blockers": blockers,
                "prepared_task_id": task_id, "prepared_task": prepared_task,
                "already_prepared": task_id is not None,
                "open_tasks": sum(item["status"] == "open" for item in state["tasks"]),
                "done_tasks": sum(item["status"] == "done" for item in state["tasks"]),
                "missing_tasks": sum(item["status"] == "geloescht" for item in state["tasks"])}

    def prepare_next_task(self, state_id: str, expected_version: int) -> dict:
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("Gültige Arbeitsstand-Version wird benötigt")
        task_id: str | None = None
        reused = False
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM work_states WHERE id=?", (state_id,)).fetchone()
            if row is None:
                raise ValueError("Arbeitsstand nicht gefunden")
            digest = step_hash(row["next_step"])
            prior = db.execute("SELECT task_id FROM project_actions WHERE state_id=? AND step_hash=?",
                               (state_id, digest)).fetchone()
            if prior:
                task_id, reused = prior["task_id"], True
            else:
                if row["version"] != expected_version:
                    raise WorkStateConflict("Arbeitsstand wurde zwischenzeitlich geändert; erneut laden")
                current = self.check(state_id)
                if current is None:
                    raise ValueError("Arbeitsstand nicht gefunden")
                if current["blockers"]:
                    raise ValueError("Projektquellen prüfen: " + "; ".join(current["blockers"]))
                if current["prepared_task_id"]:
                    task_id, reused = current["prepared_task_id"], True
                else:
                    task_id, now = str(uuid4()), utcnow()
                    notes = (f"Projekt: {row['project']}\nArbeitsstand: {state_id}\n"
                             f"Bestätigter nächster Schritt: {row['next_step']}")
                    db.execute("""INSERT INTO tasks(id,title,notes,status,due_utc,due_local,
                        timezone,created_at,updated_at,priority)
                        VALUES(?,?,?,'open',NULL,NULL,NULL,?,?,3)""",
                        (task_id, row["next_step"], notes, now, now))
                    task_refs = json.loads(row["tasks_json"])
                    task_refs.append({"id": task_id, "title": row["next_step"]})
                    db.execute("""UPDATE work_states SET tasks_json=?,version=version+1,
                        updated_at=? WHERE id=?""",
                        (json.dumps(task_refs, ensure_ascii=False), now, state_id))
                db.execute("""INSERT INTO project_actions(state_id,step_hash,task_id,created_at)
                    VALUES(?,?,?,?)""", (state_id, digest, task_id, utcnow()))
        if not reused:
            self.db.log("project_task_prepared", f"Projekt {state_id}: Aufgabe {task_id}")
        return {"task": self.tasks.get_task(task_id), "task_id": task_id,
                "reused": reused, "check": self.check(state_id)}
