"""Bewusst bestätigte Änderungen an freigegebenen lokalen Markdown-Dateien."""

import difflib
import hashlib
import hmac
import os
import re
import stat
import tempfile
import threading
from pathlib import Path
from uuid import uuid4

from jarvis.db import Database, utcnow
from jarvis.documents import DocumentStore

MAX_MARKDOWN_BYTES = 200_000


class MarkdownConflict(ValueError):
    """Die Datei wurde seit der Vorschau verändert."""


class MarkdownNotes:
    def __init__(self, db: Database, documents: DocumentStore, secret: bytes):
        self.db = db
        self.documents = documents
        self.secret = secret
        self._write_lock = threading.Lock()

    def _path(self, document_id: str) -> tuple[dict, dict, Path]:
        document = self.documents.get_document(document_id)
        if (document is None or document["excluded"] or document["status"] != "indexed"
                or Path(document["path"]).suffix.lower() not in {".md", ".markdown"}):
            raise ValueError("Nur indexierte, freigegebene Markdown-Dateien sind bearbeitbar")
        folder = self.documents.get_folder(document["folder_id"])
        if folder is None:
            raise ValueError("Freigabeordner fehlt")
        root = self.documents.granted_root(folder)
        path = Path(document["path"])
        if not path.is_absolute() or not path.is_relative_to(root):
            raise ValueError("Datei liegt nicht im freigegebenen Ordner")
        current = root
        for component in path.relative_to(root).parts:
            current = current / component
            if current.is_symlink() or current.is_junction():
                raise ValueError("Verknüpfte Dateien und Ordner sind nicht bearbeitbar")
        if path.resolve(strict=True) != path or not path.is_file():
            raise ValueError("Datei wurde verschoben oder umgeleitet")
        return document, folder, path

    @staticmethod
    def _read(path: Path) -> tuple[bytes, str, str]:
        before = path.stat()
        if before.st_size > MAX_MARKDOWN_BYTES:
            raise ValueError("Markdown-Datei ist für die Bearbeitung zu groß")
        with path.open("rb") as stream:
            data = stream.read(MAX_MARKDOWN_BYTES + 1)
        after = path.stat()
        if len(data) > MAX_MARKDOWN_BYTES:
            raise ValueError("Markdown-Datei ist für die Bearbeitung zu groß")
        if ((before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev)
                != (after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev)):
            raise MarkdownConflict("Datei wurde während des Lesens geändert")
        try:
            text = data.decode("utf-8", "strict")
        except UnicodeError as exc:
            raise ValueError("Markdown-Datei muss UTF-8 sein") from exc
        if "\x00" in text:
            raise ValueError("Markdown-Datei enthält Binärdaten")
        return data, text, hashlib.sha256(data).hexdigest()

    def _reconcile(self, document_id: str, current_sha: str) -> None:
        with self.db.connect() as db:
            rows = db.execute("""SELECT id,before_sha256,after_sha256,status
                               FROM markdown_revisions WHERE document_id=?
                               AND status='prepared' ORDER BY rowid DESC""",
                              (document_id,)).fetchall()
            for row in rows:
                status = ("applied" if current_sha == row["after_sha256"] else
                          "aborted" if current_sha == row["before_sha256"] else "unknown")
                db.execute("UPDATE markdown_revisions SET status=?,updated_at=? WHERE id=?",
                           (status, utcnow(), row["id"]))

    def read(self, document_id: str) -> dict:
        document, _, path = self._path(document_id)
        _, content, sha = self._read(path)
        self._reconcile(document_id, sha)
        with self.db.connect() as db:
            latest = db.execute("""SELECT id,after_sha256 FROM markdown_revisions
                                   WHERE document_id=? AND status='applied'
                                   ORDER BY rowid DESC LIMIT 1""", (document_id,)).fetchone()
        return {"document_id": document_id, "path": str(path), "content": content,
                "sha256": sha, "indexed_sha256": document["sha256"],
                "undo_revision_id": latest["id"] if latest and latest["after_sha256"] == sha else None}

    def _token(self, document_id: str, before_sha: str, after_sha: str) -> str:
        payload = f"{document_id}\0{before_sha}\0{after_sha}".encode()
        return hmac.new(self.secret, payload, hashlib.sha256).hexdigest()

    @staticmethod
    def _preserve_newlines(content: str, original: str) -> str:
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        if original.count("\r\n") and original.count("\r\n") == original.count("\n"):
            return normalized.replace("\n", "\r\n")
        if original.count("\r") and not original.count("\n"):
            return normalized.replace("\n", "\r")
        return normalized

    def preview(self, document_id: str, expected_sha256: str, content: str) -> dict:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ValueError("Ungültiger Datei-Hash")
        current = self.read(document_id)
        if current["sha256"] != expected_sha256:
            raise MarkdownConflict("Datei wurde seit dem Öffnen geändert; erneut laden")
        content = self._preserve_newlines(content, current["content"])
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_MARKDOWN_BYTES or "\x00" in content:
            raise ValueError("Markdown-Text ist zu groß oder enthält Binärdaten")
        if current["content"] == content:
            raise ValueError("Keine Änderung vorhanden")
        after_sha = hashlib.sha256(encoded).hexdigest()
        diff = "".join(difflib.unified_diff(
            current["content"].splitlines(keepends=True), content.splitlines(keepends=True),
            fromfile="bisher", tofile="neu",
        ))
        return {"document_id": document_id, "path": current["path"],
                "before_sha256": expected_sha256, "after_sha256": after_sha,
                "diff": diff, "approval_token": self._token(document_id, expected_sha256, after_sha)}

    def _atomic_replace(self, document_id: str, path: Path, data: bytes,
                        expected_sha: str) -> None:
        original_mode = stat.S_IMODE(path.stat().st_mode)
        descriptor, temporary = tempfile.mkstemp(prefix=".jarvis-md-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, original_mode)
            _, _, current_sha = self._read(path)
            if current_sha != expected_sha:
                raise MarkdownConflict("Datei wurde zwischen Vorschau und Speichern geändert")
            _, _, checked_path = self._path(document_id)
            if checked_path != path:
                raise MarkdownConflict("Dateipfad wurde seit der Vorschau geändert")
            # os.replace ersetzt auch einen inzwischen eingeschobenen Link selbst,
            # folgt ihm also nicht zu einer Datei außerhalb der Freigabe.
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _reindex(self, folder: dict, path: Path) -> str | None:
        try:
            with self.documents._scan_lock:
                self.documents._index_file(folder, path)
        except (OSError, ValueError) as exc:
            return str(exc)
        return None

    def save(self, document_id: str, expected_sha256: str, content: str,
             approval_token: str) -> dict:
        with self._write_lock, self.documents.mutation_lock:
            _, folder, path = self._path(document_id)
            before_bytes, before_text, before_sha = self._read(path)
            self._reconcile(document_id, before_sha)
            if before_sha != expected_sha256:
                raise MarkdownConflict("Datei wurde seit der Vorschau geändert; erneut laden")
            content = self._preserve_newlines(content, before_text)
            encoded = content.encode("utf-8")
            if len(encoded) > MAX_MARKDOWN_BYTES or "\x00" in content:
                raise ValueError("Markdown-Text ist zu groß oder enthält Binärdaten")
            after_sha = hashlib.sha256(encoded).hexdigest()
            if not hmac.compare_digest(
                    approval_token, self._token(document_id, expected_sha256, after_sha)):
                raise ValueError("Änderungsvorschau fehlt oder stimmt nicht mit dem Text überein")
            revision_id, now = str(uuid4()), utcnow()
            with self.db.connect() as db:
                db.execute("""INSERT INTO markdown_revisions
                           VALUES(?,?,?,?,?,'prepared',?,?)""",
                           (revision_id, document_id, before_sha, after_sha, before_bytes, now, now))
            try:
                self._atomic_replace(document_id, path, encoded, before_sha)
            except BaseException:
                with self.db.connect() as db:
                    db.execute("UPDATE markdown_revisions SET status='aborted',updated_at=? WHERE id=?",
                               (utcnow(), revision_id))
                raise
            with self.db.connect() as db:
                db.execute("UPDATE markdown_revisions SET status='applied',updated_at=? WHERE id=?",
                           (utcnow(), revision_id))
            index_error = self._reindex(folder, path)
            self.db.log("markdown_saved", f"Dokument {document_id}, Revision {revision_id}")
            return {"revision_id": revision_id, "sha256": after_sha, "index_error": index_error}

    def undo(self, document_id: str, revision_id: str) -> dict:
        with self._write_lock, self.documents.mutation_lock:
            _, folder, path = self._path(document_id)
            _, _, current_sha = self._read(path)
            self._reconcile(document_id, current_sha)
            with self.db.connect() as db:
                revision = db.execute("""SELECT * FROM markdown_revisions WHERE id=?
                                         AND document_id=? AND status='applied'""",
                                      (revision_id, document_id)).fetchone()
            if revision is None:
                raise ValueError("Rückgängig-Stand nicht vorhanden")
            with self.db.connect() as db:
                latest = db.execute("""SELECT id FROM markdown_revisions WHERE document_id=?
                                      AND status='applied' ORDER BY rowid DESC LIMIT 1""",
                                    (document_id,)).fetchone()
            if latest is None or latest["id"] != revision_id:
                raise MarkdownConflict("Nur die letzte Änderung kann rückgängig gemacht werden")
            if current_sha != revision["after_sha256"]:
                raise MarkdownConflict("Datei wurde seit dem Speichern geändert; Rückgängig gestoppt")
            self._atomic_replace(document_id, path, revision["before_bytes"], current_sha)
            with self.db.connect() as db:
                db.execute("UPDATE markdown_revisions SET status='undone',updated_at=? WHERE id=?",
                           (utcnow(), revision_id))
            index_error = self._reindex(folder, path)
            self.db.log("markdown_undone", f"Dokument {document_id}, Revision {revision_id}")
            return {"sha256": revision["before_sha256"], "index_error": index_error}
