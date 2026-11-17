"""Belegbare, lokale Abfrage der ausdrücklich erfassten Projektbeziehungen."""

from jarvis.db import utcnow
from jarvis.project_aliases import ProjectAliasStore
from jarvis.project_memories import ProjectMemoryStore
from jarvis.project_people import ProjectPeopleStore
from jarvis.project_person_tasks import ProjectPersonTaskStore
from jarvis.project_validity import local_date
from jarvis.work_states import WorkStateStore


class ProjectConnections:
    def __init__(self, work_states: WorkStateStore, people: ProjectPeopleStore,
                 person_tasks: ProjectPersonTaskStore, aliases: ProjectAliasStore,
                 memories: ProjectMemoryStore | None = None):
        self.work_states = work_states
        self.people = people
        self.person_tasks = person_tasks
        self.aliases = aliases
        self.memories = memories

    @staticmethod
    def _relationship_revision(db, state_id: str) -> tuple:
        """Erkennt Änderungen zwischen den getrennten, begrenzten lokalen Leseschritten."""
        result = []
        for table in ("project_people", "project_person_tasks", "project_person_aliases",
                      "project_memory_links"):
            result.append(tuple(tuple(row) for row in db.execute(
                f"SELECT id,version FROM {table} WHERE state_id=? ORDER BY id", (state_id,))))
        result.append(tuple(tuple(row) for row in db.execute(
            "SELECT id,split_at FROM project_person_merges WHERE state_id=? ORDER BY id",
            (state_id,))))
        return tuple(result)

    def for_state(self, state_id: str) -> dict:
        state = self.work_states.get(state_id)
        if state is None:
            raise LookupError("Arbeitsstand nicht gefunden")
        timezone = self.people.timezone_provider()
        as_of = local_date(timezone)
        with self.work_states.db.connect() as db:
            revision = self._relationship_revision(db, state_id)
        people = self.people.list_for_state(state_id, state, as_of)
        person_tasks = self.person_tasks.list_for_state(state_id, state, people, as_of)
        aliases = self.aliases.list_for_state(state_id, state, people, as_of)
        memories = self.memories.list_for_state(state_id, state) if self.memories else []
        # Ein paralleles Projekt-Update darf keine Personenbelege aus zwei
        # verschiedenen Projektfassungen in einer Antwort vermischen.
        with self.work_states.db.connect() as db:
            latest = db.execute("SELECT version FROM work_states WHERE id=?",
                                (state_id,)).fetchone()
            latest_revision = self._relationship_revision(db, state_id)
        if latest is None or latest["version"] != state["version"] or latest_revision != revision:
            raise RuntimeError("Arbeitsstand oder Projektbeziehungen haben sich während der Abfrage geändert; erneut abfragen")
        return {
            "project_id": state["id"],
            "project": state["project"],
            "state_version": state["version"],
            "checked_at": utcnow(),
            "timezone": timezone,
            "as_of_local_date": as_of.isoformat(),
            "people": [{
                "id": person["id"],
                "name": person["name"],
                "relation_type": person["relation_type"],
                "source_note": person["source_note"],
                "source_kind": ("own_statement" if person["source_document_id"] is None
                                else "current_document" if person["source_status"] == "unveraendert"
                                else "stale_document"),
                "source_status": person["source_status"],
                "source_document_id": person["source_document_id"],
                "source_path": person["source_path"],
                "source_sha256": person["source_sha256"],
                "version": person["version"],
                "confidence": person["confidence"],
                "valid_from": person["valid_from"],
                "valid_until": person["valid_until"],
                "validity_status": person["validity_status"],
            } for person in people],
            "documents": [{key: document[key] for key in ("id", "path", "sha256", "status")}
                          for document in state["documents"]],
            "tasks": [{key: task.get(key) for key in (
                "id", "title", "current_title", "status", "due_utc", "due_local",
                "timezone", "due_offset", "due_fold", "current_due_utc",
                "current_due_local", "current_timezone", "current_due_offset",
                "current_due_fold", "due_status")}
                      for task in state["tasks"]],
            "person_tasks": person_tasks,
            "aliases": aliases,
            "memories": memories,
        }
