"""Unveränderliche Regelversionen für klar abgegrenzte Modellabläufe."""

from typing import Literal

from jarvis.db import Database, utcnow


class ChatRuleConflict(RuntimeError):
    """Eine andere Änderung hat die angezeigte aktive Fassung überholt."""


class VersionedRuleStore:
    def __init__(self, db: Database, scope: Literal["chat", "draft", "plan"]):
        if scope not in {"chat", "draft", "plan"}:
            raise ValueError("Unbekannter Regelbereich")
        self.db = db
        # Der Bereich wird ausschließlich aus dieser festen Menge gewählt.
        self.versions = f"{scope}_rule_versions"
        self.state = f"{scope}_rule_state"
        self.activations = f"{scope}_rule_activations"
        self.label = {"chat": "Chat-Regel", "draft": "Entwurfsregel",
                      "plan": "Planregel"}[scope]

    def active(self) -> dict:
        with self.db.connect() as connection:
            return dict(connection.execute(f"""SELECT v.version,v.content,s.revision,
                       s.updated_at FROM {self.state} s JOIN {self.versions} v
                       ON v.version=s.active_version WHERE s.id=1""").fetchone())

    def history(self) -> dict:
        with self.db.connect() as connection:
            active = dict(connection.execute(f"SELECT * FROM {self.state} WHERE id=1").fetchone())
            versions = [dict(row) for row in connection.execute(
                f"SELECT * FROM {self.versions} ORDER BY version DESC")]
            activations = [dict(row) for row in connection.execute(
                f"SELECT * FROM {self.activations} ORDER BY id DESC LIMIT 100")]
        return {"active_version": active["active_version"], "revision": active["revision"],
                "versions": versions, "activations": activations}

    def create(self, content: str, expected_revision: int) -> dict:
        content = content.strip()
        if not 1 <= len(content) <= 2000:
            raise ValueError(f"{self.label} muss 1–2.000 Zeichen enthalten")
        now = utcnow()
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                f"SELECT active_version,revision FROM {self.state} WHERE id=1").fetchone()
            if state["revision"] != expected_revision:
                raise ChatRuleConflict(f"{self.label} wurde zwischenzeitlich geändert")
            if connection.execute(f"SELECT content FROM {self.versions} WHERE version=?",
                                  (state["active_version"],)).fetchone()[0] == content:
                raise ValueError(f"Diese {self.label} ist bereits aktiv")
            cursor = connection.execute(
                f"INSERT INTO {self.versions}(content,created_at) VALUES(?,?)", (content, now))
            version = cursor.lastrowid
            connection.execute(f"""UPDATE {self.state} SET active_version=?,
                               revision=revision+1,updated_at=? WHERE id=1""",
                               (version, now))
            connection.execute(f"""INSERT INTO {self.activations}
                               (from_version,to_version,changed_at) VALUES(?,?,?)""",
                               (state["active_version"], version, now))
        return self.history()

    def activate(self, version: int, expected_revision: int) -> dict:
        now = utcnow()
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                f"SELECT active_version,revision FROM {self.state} WHERE id=1").fetchone()
            if state["revision"] != expected_revision:
                raise ChatRuleConflict(f"{self.label} wurde zwischenzeitlich geändert")
            if connection.execute(f"SELECT version FROM {self.versions} WHERE version=?",
                                  (version,)).fetchone() is None:
                raise LookupError(f"{self.label}-Fassung nicht gefunden")
            if state["active_version"] != version:
                connection.execute(f"""UPDATE {self.state} SET active_version=?,
                                   revision=revision+1,updated_at=? WHERE id=1""",
                                   (version, now))
                connection.execute(f"""INSERT INTO {self.activations}
                                   (from_version,to_version,changed_at) VALUES(?,?,?)""",
                                   (state["active_version"], version, now))
        return self.history()


class ChatRuleStore(VersionedRuleStore):
    def __init__(self, db: Database):
        super().__init__(db, "chat")


class DraftRuleStore(VersionedRuleStore):
    def __init__(self, db: Database):
        super().__init__(db, "draft")


class PlanRuleStore(VersionedRuleStore):
    def __init__(self, db: Database):
        super().__init__(db, "plan")


def system_prompt(base: str, content: str) -> str:
    if not content:
        return base
    return (base + "\n\nZusätzliche lokale Chat-Regel des Nutzers:\n" + content
            + "\nDie technischen Freigaben und Werkzeugrechte der App gelten weiterhin.")


def draft_system_prompt(base: str, content: str) -> str:
    if not content:
        return base
    return (base + "\n\nZusätzliche lokale Entwurfsregel des Nutzers:\n" + content
            + "\nQuellen-, Cloud- und Versandgrenzen der App gelten weiterhin.")


def plan_system_prompt(base: str, content: str) -> str:
    if not content:
        return base
    return (base + "\n\nZusätzliche lokale Planregel des Nutzers:\n" + content
            + "\nWerkzeugregister, Freigaben, Laufzeit- und Webgrenzen der App gelten weiterhin.")
