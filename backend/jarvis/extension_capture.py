"""Bewusstes, lokal gekoppeltes Übernehmen eines Browser-Tabs als Aufgabe."""

import hashlib
import json
import re
import secrets
import threading
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from jarvis.db import Database, utcnow

EXTENSION_ORIGIN = re.compile(r"^chrome-extension://[a-p]{32}$")
PAIRING_LIFETIME = timedelta(minutes=5)


class ExtensionAuthError(ValueError):
    pass


class ExtensionRateLimit(ExtensionAuthError):
    pass


class ExtensionConflict(ValueError):
    pass


def extension_id_from_origin(origin: str | None) -> str:
    if not origin or not EXTENSION_ORIGIN.fullmatch(origin):
        raise ExtensionAuthError("Nur eine Edge-Erweiterung darf diesen Zugang nutzen")
    return origin.removeprefix("chrome-extension://")


def checked_source_url(value: str) -> str:
    if len(value) > 2000 or any(ord(char) < 32 for char in value):
        raise ValueError("Quellenlink ist ungültig oder zu lang")
    try:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or any(char.isspace() for char in parts.netloc)
                or parts.username or parts.password
                or parts.port is not None and not 1 <= parts.port <= 65535):
            raise ValueError("Nur HTTP(S)-Tabs ohne Zugangsdaten sind erlaubt")
    except ValueError as exc:
        raise ValueError("Quellenlink ist ungültig") from exc
    return value


class ExtensionCaptureStore:
    def __init__(self, db: Database):
        self.db = db
        self._lock = threading.Lock()
        self._pairing: tuple[str, datetime] | None = None
        self._failed_pairs: dict[str, list[datetime]] = {}

    def issue_code(self) -> dict:
        code = secrets.token_urlsafe(18)
        expires = datetime.now(UTC) + PAIRING_LIFETIME
        with self._lock:
            self._pairing = (code, expires)
            self._failed_pairs.clear()
        return {"code": code, "expires_at": expires.isoformat()}

    def pair(self, origin: str | None, code: str) -> dict:
        extension_id = extension_id_from_origin(origin)
        with self._lock:
            now = datetime.now(UTC)
            failures = [at for at in self._failed_pairs.get(extension_id, [])
                        if now - at < PAIRING_LIFETIME]
            if len(failures) >= 8:
                self._failed_pairs[extension_id] = failures
                raise ExtensionRateLimit("Zu viele falsche Kopplungsversuche; neuen Code in Jarvis erzeugen")
            pending = self._pairing
            if (pending is None or now >= pending[1]
                    or not secrets.compare_digest(code, pending[0])):
                failures.append(now)
                self._failed_pairs[extension_id] = failures
                raise ExtensionAuthError("Kopplungscode ungültig oder abgelaufen")
            self._pairing = None
            self._failed_pairs.pop(extension_id, None)
            token = secrets.token_urlsafe(32)
            with self.db.connect() as db:
                db.execute("""INSERT INTO extension_pairs(extension_id,token_sha256,created_at)
                    VALUES(?,?,?) ON CONFLICT(extension_id) DO UPDATE SET
                    token_sha256=excluded.token_sha256,created_at=excluded.created_at""",
                    (extension_id, hashlib.sha256(token.encode()).hexdigest(), utcnow()))
        self.db.log("extension_paired", f"Edge-Erweiterung {extension_id} gekoppelt")
        return {"token": token, "extension_id": extension_id}

    def list_pairs(self) -> list[dict]:
        with self.db.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT extension_id,created_at FROM extension_pairs ORDER BY created_at DESC")]

    def revoke(self, extension_id: str) -> bool:
        if not re.fullmatch(r"[a-p]{32}", extension_id):
            raise ValueError("Erweiterungs-ID ungültig")
        with self.db.connect() as db:
            removed = db.execute("DELETE FROM extension_pairs WHERE extension_id=?",
                                 (extension_id,)).rowcount > 0
        if removed:
            self.db.log("extension_revoked", f"Edge-Erweiterung {extension_id} getrennt")
        return removed

    def capture(self, origin: str | None, token: str, request_id: str,
                title: str, url: str, source_title: str, selection: str,
                page_text: str) -> dict:
        extension_id = extension_id_from_origin(origin)
        checked_source_url(url)
        if not title.strip():
            raise ValueError("Aufgabentitel wird benötigt")
        try:
            parsed = UUID(request_id)
            if parsed.version != 4 or str(parsed) != request_id.lower():
                raise ValueError("UUIDv4 erwartet")
            request_id = str(parsed)
        except ValueError as exc:
            raise ValueError("Übernahme-ID ungültig") from exc
        content = {"title": title.strip(), "url": url, "source_title": source_title.strip(),
                   "selection": selection.strip(), "page_text": page_text.strip()}
        digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        task_id = str(uuid4())
        now = utcnow()
        notes = f"Quelle: {content['source_title'] or 'Browser-Tab'}\n{url}"
        if content["selection"]:
            notes += f"\n\nAuswahl:\n{content['selection']}"
        if content["page_text"]:
            notes += f"\n\nAusdrücklich übernommener Seitentext:\n{content['page_text']}"
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            pair = db.execute("SELECT token_sha256 FROM extension_pairs WHERE extension_id=?",
                              (extension_id,)).fetchone()
            if not pair or not secrets.compare_digest(
                    pair["token_sha256"], hashlib.sha256(token.encode()).hexdigest()):
                raise ExtensionAuthError("Erweiterung ist nicht gekoppelt oder wurde getrennt")
            previous = db.execute("""SELECT content_sha256,task_id FROM extension_captures
                WHERE extension_id=? AND request_id=?""", (extension_id, request_id)).fetchone()
            if previous:
                if previous["content_sha256"] != digest:
                    raise ExtensionConflict("Übernahme-ID wurde bereits mit anderem Inhalt verwendet")
                return {"task_id": previous["task_id"], "created": False}
            db.execute("""INSERT INTO tasks(id,title,notes,status,due_utc,due_local,timezone,
                created_at,updated_at,priority,source_url)
                VALUES(?,?,?,'open',NULL,NULL,NULL,?,?,3,?)""",
                (task_id, content["title"], notes, now, now, url))
            db.execute("""INSERT INTO extension_captures
                (extension_id,request_id,content_sha256,task_id,created_at)
                VALUES(?,?,?,?,?)""", (extension_id, request_id, digest, task_id, now))
        self.db.log("extension_capture", f"Aufgabe {task_id} aus Edge-Tab übernommen")
        return {"task_id": task_id, "created": True}
