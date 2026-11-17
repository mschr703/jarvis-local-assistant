"""Benannte, versionierte Zusatzanweisungen für bewusst ausgewählte Chats."""

from hashlib import sha256
from uuid import uuid4

from jarvis.db import Database, utcnow


class SkillConflict(RuntimeError):
    """Eine andere Änderung hat die angezeigte Revision überholt."""


class SkillStore:
    def __init__(self, db: Database):
        self.db = db

    def list(self) -> list[dict]:
        with self.db.connect() as connection:
            skills = [dict(row) for row in connection.execute(
                "SELECT * FROM skills ORDER BY name COLLATE NOCASE,id")]
            for skill in skills:
                skill["versions"] = [dict(row) for row in connection.execute(
                    """SELECT version,content,content_hash,created_at FROM skill_versions
                       WHERE skill_id=? ORDER BY version DESC""", (skill["id"],))]
                skill["activations"] = [dict(row) for row in connection.execute(
                    """SELECT from_version,to_version,changed_at FROM skill_activations
                       WHERE skill_id=? ORDER BY id DESC LIMIT 100""", (skill["id"],))]
            return skills

    def selected(self, skill_id: str) -> dict:
        with self.db.connect() as connection:
            row = connection.execute("""SELECT s.id,s.name,v.version,v.content,v.content_hash
                FROM skills s LEFT JOIN skill_versions v
                ON v.skill_id=s.id AND v.version=s.active_version WHERE s.id=?""",
                (skill_id,)).fetchone()
            if row is None:
                raise LookupError("Skill nicht gefunden")
            if row["version"] is None:
                raise SkillConflict("Skill ist inaktiv")
            if self._hash(row["content"]) != row["content_hash"]:
                raise SkillConflict("Skill-Inhalt stimmt nicht mit dem Versionsbeleg überein")
            return dict(row)

    def create(self, name: str, content: str) -> dict:
        name, content = self._validate(name, content)
        now, skill_id = utcnow(), str(uuid4())
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM skills WHERE name=? COLLATE NOCASE",
                                  (name,)).fetchone():
                raise SkillConflict("Skill-Name ist bereits vergeben")
            connection.execute("INSERT INTO skills VALUES(?,?,1,1,?,?)",
                               (skill_id, name, now, now))
            connection.execute("INSERT INTO skill_versions VALUES(?,?,?,?,?)",
                               (skill_id, 1, content, self._hash(content), now))
            connection.execute("INSERT INTO skill_activations(skill_id,from_version,to_version,changed_at) VALUES(?,NULL,1,?)",
                               (skill_id, now))
        return next(skill for skill in self.list() if skill["id"] == skill_id)

    def revise(self, skill_id: str, content: str, expected_revision: int) -> dict:
        _, content = self._validate("Skill", content)
        now = utcnow()
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute("SELECT * FROM skills WHERE id=?",
                                       (skill_id,)).fetchone()
            if state is None:
                raise LookupError("Skill nicht gefunden")
            if state["revision"] != expected_revision:
                raise SkillConflict("Skill wurde zwischenzeitlich geändert")
            latest = connection.execute("""SELECT version,content FROM skill_versions
                WHERE skill_id=? ORDER BY version DESC LIMIT 1""", (skill_id,)).fetchone()
            if latest["content"] == content:
                raise ValueError("Diese Skill-Fassung existiert bereits als neueste Fassung")
            version = latest["version"] + 1
            connection.execute("INSERT INTO skill_versions VALUES(?,?,?,?,?)",
                               (skill_id, version, content, self._hash(content), now))
            connection.execute("""UPDATE skills SET active_version=?,revision=revision+1,
                updated_at=? WHERE id=?""", (version, now, skill_id))
            connection.execute("INSERT INTO skill_activations(skill_id,from_version,to_version,changed_at) VALUES(?,?,?,?)",
                               (skill_id, state["active_version"], version, now))
        return next(skill for skill in self.list() if skill["id"] == skill_id)

    def activate(self, skill_id: str, version: int | None, expected_revision: int) -> dict:
        now = utcnow()
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute("SELECT * FROM skills WHERE id=?",
                                       (skill_id,)).fetchone()
            if state is None:
                raise LookupError("Skill nicht gefunden")
            if state["revision"] != expected_revision:
                raise SkillConflict("Skill wurde zwischenzeitlich geändert")
            if version is not None and connection.execute(
                    "SELECT 1 FROM skill_versions WHERE skill_id=? AND version=?",
                    (skill_id, version)).fetchone() is None:
                raise LookupError("Skill-Fassung nicht gefunden")
            if state["active_version"] != version:
                connection.execute("""UPDATE skills SET active_version=?,revision=revision+1,
                    updated_at=? WHERE id=?""", (version, now, skill_id))
                connection.execute("INSERT INTO skill_activations(skill_id,from_version,to_version,changed_at) VALUES(?,?,?,?)",
                                   (skill_id, state["active_version"], version, now))
        return next(skill for skill in self.list() if skill["id"] == skill_id)

    @staticmethod
    def _validate(name: str, content: str) -> tuple[str, str]:
        name, content = name.strip(), content.strip()
        if not 1 <= len(name) <= 80 or "\n" in name or "\r" in name:
            raise ValueError("Skill-Name muss 1–80 Zeichen in einer Zeile enthalten")
        if not 1 <= len(content) <= 2000:
            raise ValueError("Skill-Inhalt muss 1–2.000 Zeichen enthalten")
        return name, content

    @staticmethod
    def _hash(content: str) -> str:
        return sha256(content.encode("utf-8")).hexdigest()


def skill_prompt(base: str, selected: dict | None) -> str:
    if selected is None:
        return base
    return (base + "\n\nBewusst ausgewählter lokaler Chat-Skill „" + selected["name"]
            + "“ (zusätzliche Nutzeranweisung):\n" + selected["content"]
            + "\nSystemregeln, Quellenfreigaben und Werkzeugrechte der App gelten weiterhin.")
