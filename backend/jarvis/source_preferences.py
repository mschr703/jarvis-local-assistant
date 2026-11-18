"""Explicit local document preferences for one normalized search question."""

import hashlib
from pathlib import Path

from jarvis.db import Database, utcnow
from jarvis.documents import MAX_FILE_BYTES, DocumentStore
from jarvis.unified_search import query_terms

MAX_PREFERENCES = 5


def _query_key(query: str) -> str:
    cleaned, _terms = query_terms(query)
    return cleaned.casefold()


class SourcePreferences:
    def __init__(self, db: Database):
        self.db = db

    def _document(self, document_id: str) -> dict | None:
        with self.db.connect() as db:
            row = db.execute("""SELECT d.id,d.path,d.sha256,d.size,d.policy,d.excluded,d.status,
                f.path AS folder_path FROM documents d
                JOIN folders f ON f.id=d.folder_id WHERE d.id=?""", (document_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _verified(document: dict) -> bool:
        if (document["excluded"] or document["status"] != "indexed"
                or not document["sha256"] or not document["size"]):
            return False
        try:
            root = DocumentStore.granted_root({"path": document["folder_path"]})
            path = Path(document["path"])
            if (path.is_symlink() or path.is_junction()
                    or not path.resolve(strict=True).is_relative_to(root)
                    or path.stat().st_size > MAX_FILE_BYTES):
                return False
            return hashlib.sha256(path.read_bytes()).hexdigest() == document["sha256"]
        except (OSError, ValueError):
            return False

    def add(self, query: str, document_id: str) -> dict:
        key = _query_key(query)
        document = self._document(document_id)
        if document is None:
            raise LookupError("Dokument nicht gefunden")
        if not self._verified(document):
            raise ValueError("Dokument ist nicht aktuell indexiert; zuerst Index aktualisieren")
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("""SELECT path,sha256,status,excluded FROM documents
                WHERE id=?""", (document_id,)).fetchone()
            if (current is None or current["path"] != document["path"]
                    or current["sha256"] != document["sha256"]
                    or current["status"] != "indexed" or current["excluded"]):
                raise ValueError("Dokument hat sich während der Vormerkung geändert")
            count = db.execute("SELECT COUNT(*) FROM source_preferences WHERE query_key=?",
                               (key,)).fetchone()[0]
            exists = db.execute("""SELECT 1 FROM source_preferences
                WHERE query_key=? AND document_id=?""", (key, document_id)).fetchone()
            if count >= MAX_PREFERENCES and not exists:
                raise ValueError("Höchstens fünf bevorzugte Dokumente je Suchfrage")
            db.execute("""INSERT INTO source_preferences
                (query_key,document_id,path,sha256,created_at) VALUES(?,?,?,?,?)
                ON CONFLICT(query_key,document_id) DO UPDATE SET
                path=excluded.path,sha256=excluded.sha256,created_at=excluded.created_at""",
                (key, document_id, document["path"], document["sha256"], utcnow()))
        self.db.log("source_preference_added", f"Dokument {document_id}")
        return self.list(query)

    def remove(self, query: str, document_id: str) -> dict:
        key = _query_key(query)
        with self.db.connect() as db:
            deleted = db.execute("""DELETE FROM source_preferences
                WHERE query_key=? AND document_id=?""", (key, document_id)).rowcount
        if not deleted:
            raise LookupError("Quellenpräferenz nicht gefunden")
        self.db.log("source_preference_removed", f"Dokument {document_id}")
        return self.list(query)

    def list(self, query: str) -> dict:
        key = _query_key(query)
        with self.db.connect() as db:
            rows = [dict(row) for row in db.execute("""SELECT document_id,path,sha256,created_at
                FROM source_preferences WHERE query_key=? ORDER BY created_at,document_id
                LIMIT ?""", (key, MAX_PREFERENCES))]
        preferences = []
        for row in rows:
            current = self._document(row["document_id"])
            if (not current or current["path"] != row["path"] or current["excluded"]
                    or current["status"] != "indexed" or not current["sha256"]):
                status = "unavailable"
            elif not self._verified(current):
                status = "needs_reindex"
            elif current["sha256"] != row["sha256"]:
                status = "updated"
            else:
                status = "current"
            entry = {"document_id": row["document_id"], "path": row["path"],
                     "saved_sha256": row["sha256"], "created_at": row["created_at"],
                     "status": status, "current_sha256": current["sha256"] if current else None}
            if status in {"current", "updated"}:
                entry["policy"] = current["policy"]
                with self.db.connect() as db:
                    passage = db.execute("""SELECT id,page,text FROM passages
                        WHERE document_id=? ORDER BY ordinal,id LIMIT 1""",
                        (row["document_id"],)).fetchone()
                if passage:
                    entry.update({"passage_id": passage["id"], "page": passage["page"],
                                  "excerpt": passage["text"][:500]})
            preferences.append(entry)
        return {"query": " ".join(query.split()), "preferences": preferences}
