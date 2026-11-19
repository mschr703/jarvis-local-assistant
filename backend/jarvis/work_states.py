"""Manuell bestätigte Arbeitsstände mit überprüfbaren Datei- und Aufgabenverweisen."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from jarvis.db import Database, utcnow
from jarvis.documents import MAX_FILE_BYTES, DocumentStore
from jarvis.tasks import TaskStore


class WorkStateConflict(ValueError):
    """Ein anderer Stand wurde nach dem Öffnen gespeichert."""


class WorkStateStore:
    def __init__(self, db: Database, documents: DocumentStore, tasks: TaskStore):
        self.db = db
        self.documents = documents
        self.tasks = tasks

    @staticmethod
    def _validate_text(project: str, confirmed: str, open_questions: str,
                       next_step: str) -> tuple[str, str, str, str]:
        values = tuple(value.strip() for value in
                       (project, confirmed, open_questions, next_step))
        if not values[0] or not values[3]:
            raise ValueError("Projekt und nächster Schritt werden benötigt")
        if any(len(value) > limit for value, limit in zip(values, (200, 10_000, 10_000, 2_000),
                                                       strict=True)):
            raise ValueError("Arbeitsstand enthält zu langen Text")
        return values

    @staticmethod
    def _unique_ids(ids: list[str], label: str) -> list[str]:
        if len(ids) > 10 or len(set(ids)) != len(ids):
            raise ValueError(f"{label}: höchstens zehn verschiedene Verweise")
        return ids

    @staticmethod
    def _links(links: list[str]) -> list[str]:
        if len(links) > 10:
            raise ValueError("Höchstens zehn Links")
        cleaned = []
        for item in links:
            item = item.strip()
            if not item or len(item) > 2048 or any(char.isspace() for char in item):
                raise ValueError("Link ist leer, zu lang oder enthält Leerzeichen")
            parsed = urlsplit(item)
            try:
                valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                valid = valid and parsed.username is None and parsed.password is None
                _ = parsed.port
            except ValueError as exc:
                raise ValueError("Ungültiger HTTP(S)-Link") from exc
            if not valid:
                raise ValueError("Nur HTTP(S)-Links ohne Zugangsdaten sind erlaubt")
            cleaned.append(item)
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("Doppelte Links sind nicht erlaubt")
        return cleaned

    def _granted_file(self, document: dict) -> Path:
        folder = self.documents.get_folder(document["folder_id"])
        if folder is None or document["excluded"] or document["status"] != "indexed":
            raise ValueError("Datei ist nicht mehr freigegeben oder indexiert")
        root = self.documents.granted_root(folder)
        path = Path(document["path"])
        if not path.is_absolute() or not path.is_relative_to(root):
            raise ValueError("Datei liegt außerhalb der Freigabe")
        current = root
        for component in path.relative_to(root).parts:
            current = current / component
            if current.is_symlink() or current.is_junction():
                raise ValueError("Verknüpfte Dateien oder Ordner sind nicht erlaubt")
        if path.resolve(strict=True) != path or not path.is_file():
            raise ValueError("Datei wurde verschoben oder umgeleitet")
        return path

    @staticmethod
    def _hash_file(path: Path) -> str:
        before = path.stat()
        if before.st_size > MAX_FILE_BYTES:
            raise ValueError("Datei ist für den Arbeitsstand zu groß")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(block)
                if size > MAX_FILE_BYTES:
                    raise ValueError("Datei ist für den Arbeitsstand zu groß")
                digest.update(block)
        after = path.stat()
        if ((before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev)
                != (after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev)):
            raise ValueError("Datei wurde beim Prüfen verändert")
        return digest.hexdigest()

    def _capture_document(self, document_id: str) -> dict:
        document = self.documents.get_document(document_id)
        if document is None:
            raise ValueError("Ausgewählte Datei nicht gefunden")
        try:
            path = self._granted_file(document)
            digest = self._hash_file(path)
        except OSError as exc:
            raise ValueError("Ausgewählte Datei ist nicht lesbar") from exc
        if digest != document["sha256"]:
            raise WorkStateConflict("Ausgewählte Datei wurde seit der Indexierung verändert; Index aktualisieren")
        return {"id": document_id, "path": str(path), "sha256": digest}

    def _capture_task(self, task_id: str) -> dict:
        task = self.tasks.get_task(task_id)
        if task is None or task["status"] != "open":
            raise ValueError("Nur vorhandene offene Aufgaben können neu ausgewählt werden")
        return {key: task[key] for key in (
            "id", "title", "due_utc", "due_local", "timezone", "due_offset", "due_fold")}

    @staticmethod
    def _task_status(saved: dict, current: dict | None) -> dict:
        if current is None:
            due_status = "aufgabe_geloescht"
        elif "due_utc" not in saved:
            due_status = "altbestand_ohne_snapshot"
        elif saved["due_utc"] == current["due_utc"] and saved["timezone"] == current["timezone"]:
            due_status = "unveraendert" if saved["due_utc"] else "ohne_frist"
        elif saved["due_utc"] is None:
            due_status = "hinzugefuegt"
        elif current["due_utc"] is None:
            due_status = "entfernt"
        else:
            due_status = "geaendert"
        return {**saved, "status": current["status"] if current else "geloescht",
                "current_title": current["title"] if current else None,
                "current_due_utc": current["due_utc"] if current else None,
                "current_due_local": current["due_local"] if current else None,
                "current_timezone": current["timezone"] if current else None,
                "current_due_offset": current["due_offset"] if current else None,
                "current_due_fold": current["due_fold"] if current else None,
                "due_status": due_status}

    def _references(self, document_ids: list[str], task_ids: list[str],
                    existing: dict | None = None) -> tuple[list[dict], list[dict]]:
        self._unique_ids(document_ids, "Dateien")
        self._unique_ids(task_ids, "Aufgaben")
        old_documents = {item["id"]: item for item in
                         json.loads(existing["documents_json"])} if existing else {}
        old_tasks = {item["id"]: item for item in
                     json.loads(existing["tasks_json"])} if existing else {}
        documents = [old_documents[item] if item in old_documents else
                     self._capture_document(item) for item in document_ids]
        tasks = [old_tasks[item] if item in old_tasks else
                 self._capture_task(item) for item in task_ids]
        return documents, tasks

    def list(self) -> list[dict]:
        with self.db.connect() as db:
            return [dict(row) for row in db.execute("""SELECT id,project,next_step,version,
                        created_at,updated_at FROM work_states ORDER BY updated_at DESC,id DESC""")]

    def _document_status(self, saved: dict) -> dict:
        document = self.documents.get_document(saved["id"])
        status = "freigabe_entfernt"
        if document is not None:
            if document["excluded"]:
                status = "ausgeschlossen"
            elif document["path"] != saved["path"]:
                status = "pfad_geaendert"
            elif document["status"] != "indexed":
                status = "index_ausstehend"
            else:
                try:
                    path = self._granted_file(document)
                    status = ("unveraendert" if self._hash_file(path) == saved["sha256"]
                              else "geaendert")
                except FileNotFoundError:
                    status = "fehlt"
                except (OSError, ValueError):
                    status = "nicht_verfuegbar"
        return {**saved, "status": status}

    def get(self, state_id: str) -> dict | None:
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM work_states WHERE id=?", (state_id,)).fetchone()
        if row is None:
            return None
        state = dict(row)
        state["documents"] = [self._document_status(item)
                              for item in json.loads(state.pop("documents_json"))]
        state["tasks"] = [self._task_status(item, task)
                          for item in json.loads(state.pop("tasks_json"))
                          for task in [self.tasks.get_task(item["id"])]]
        state["links"] = json.loads(state.pop("links_json"))
        return state

    def create(self, project: str, confirmed: str, open_questions: str, next_step: str,
               document_ids: list[str], task_ids: list[str], links: list[str]) -> dict:
        values = self._validate_text(project, confirmed, open_questions, next_step)
        documents, tasks = self._references(document_ids, task_ids)
        links = self._links(links)
        state_id, now = str(uuid4()), utcnow()
        with self.db.connect() as db:
            db.execute("""INSERT INTO work_states VALUES(?,?,?,?,?,?,?,?,1,?,?)""",
                       (state_id, *values, json.dumps(documents), json.dumps(tasks),
                        json.dumps(links), now, now))
        self.db.log("work_state_created", f"Arbeitsstand {state_id}")
        return self.get(state_id)

    def update(self, state_id: str, version: int, project: str, confirmed: str,
               open_questions: str, next_step: str, document_ids: list[str],
               task_ids: list[str], links: list[str]) -> dict:
        values = self._validate_text(project, confirmed, open_questions, next_step)
        links = self._links(links)
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM work_states WHERE id=?", (state_id,)).fetchone()
            if existing is None:
                raise ValueError("Arbeitsstand nicht gefunden")
            if existing["version"] != version:
                raise WorkStateConflict("Arbeitsstand wurde zwischenzeitlich geändert; erneut laden")
            documents, tasks = self._references(document_ids, task_ids, existing)
            db.execute("""UPDATE work_states SET project=?,confirmed=?,open_questions=?,
                       next_step=?,documents_json=?,tasks_json=?,links_json=?,
                       version=version+1,updated_at=? WHERE id=?""",
                       (*values, json.dumps(documents), json.dumps(tasks),
                        json.dumps(links), utcnow(), state_id))
        self.db.log("work_state_updated", f"Arbeitsstand {state_id}")
        return self.get(state_id)

    def delete(self, state_id: str, version: int) -> None:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT version FROM work_states WHERE id=?", (state_id,)).fetchone()
            if row is None:
                raise ValueError("Arbeitsstand nicht gefunden")
            if row["version"] != version:
                raise WorkStateConflict("Arbeitsstand wurde zwischenzeitlich geändert; erneut laden")
            db.execute("DELETE FROM work_states WHERE id=?", (state_id,))
        self.db.log("work_state_deleted", f"Arbeitsstand {state_id}")

    def confirm_document(self, state_id: str, document_id: str, expected_version: int) -> dict:
        """Übernimmt einen erneut indexierten Datei-Hash nur nach ausdrücklichem Klick."""
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT version,documents_json FROM work_states WHERE id=?",
                             (state_id,)).fetchone()
            if row is None:
                raise ValueError("Arbeitsstand nicht gefunden")
            if row["version"] != expected_version:
                raise WorkStateConflict("Arbeitsstand wurde zwischenzeitlich geändert; erneut laden")
            documents = json.loads(row["documents_json"])
            for index, item in enumerate(documents):
                if item["id"] == document_id:
                    documents[index] = self._capture_document(document_id)
                    break
            else:
                raise ValueError("Datei gehört nicht zu diesem Arbeitsstand")
            db.execute("""UPDATE work_states SET documents_json=?,version=version+1,
                updated_at=? WHERE id=?""",
                (json.dumps(documents, ensure_ascii=False), utcnow(), state_id))
        self.db.log("work_state_document_confirmed", f"Arbeitsstand {state_id}: {document_id}")
        return self.get(state_id)
