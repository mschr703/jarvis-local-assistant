"""Optional, rebuildable local cache for passage embeddings; never stores source text."""

import json
import math
import sqlite3
import time
from contextlib import closing
from pathlib import Path

LIMIT = 200


def _valid_vector(value: object) -> bool:
    if not isinstance(value, list) or not 1 <= len(value) <= 4096:
        return False
    try:
        return (all(type(item) in (int, float) and math.isfinite(item) for item in value)
                and 0 < sum(item * item for item in value) < math.inf)
    except (OverflowError, TypeError, ValueError):
        return False


class PersistentEmbeddingCache:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("""CREATE TABLE IF NOT EXISTS embeddings (
                digest TEXT NOT NULL, text_hash TEXT NOT NULL, vector_json TEXT NOT NULL,
                used_at INTEGER NOT NULL, PRIMARY KEY (digest,text_hash))""")
        except sqlite3.Error:
            db.close()
            raise
        return db

    def read(self, digest: str, hashes: list[str]) -> dict[str, list[float]]:
        if not hashes:
            return {}
        try:
            with closing(self._connect()) as db:
                db.execute("DELETE FROM embeddings WHERE digest<>?", (digest,))
                placeholders = ",".join("?" for _ in hashes)
                rows = list(db.execute(f"""SELECT text_hash,vector_json FROM embeddings
                    WHERE digest=? AND text_hash IN ({placeholders})""", (digest, *hashes)))
                hits = {}
                for text_hash, serialized in rows:
                    try:
                        vector = json.loads(serialized)
                    except (TypeError, ValueError):
                        vector = None
                    if _valid_vector(vector):
                        hits[text_hash] = vector
                        db.execute("UPDATE embeddings SET used_at=? WHERE digest=? AND text_hash=?",
                                   (time.time_ns(), digest, text_hash))
                    else:
                        db.execute("DELETE FROM embeddings WHERE digest=? AND text_hash=?",
                                   (digest, text_hash))
                db.commit()
                return hits
        except (OSError, sqlite3.Error):
            return {}

    def write(self, digest: str, entries: dict[str, list[float]]) -> None:
        if not entries:
            return
        try:
            with closing(self._connect()) as db:
                db.execute("DELETE FROM embeddings WHERE digest<>?", (digest,))
                now = time.time_ns()
                db.executemany("""INSERT OR REPLACE INTO embeddings
                    (digest,text_hash,vector_json,used_at) VALUES(?,?,?,?)""",
                    [(digest, text_hash, json.dumps(vector, separators=(",", ":")), now + index)
                     for index, (text_hash, vector) in enumerate(entries.items())
                     if _valid_vector(vector)])
                db.execute("""DELETE FROM embeddings WHERE rowid NOT IN
                    (SELECT rowid FROM embeddings ORDER BY used_at DESC,rowid DESC LIMIT ?)""",
                           (LIMIT,))
                db.commit()
        except (OSError, sqlite3.Error):
            return

    def stats(self) -> dict[str, int | bool]:
        """Report only counts and file size; never read or expose cached vectors."""
        try:
            if not self.path.is_file():
                return {"entries": 0, "bytes": 0, "available": True}
            size = self.path.stat().st_size
            with closing(sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro",
                                         uri=True, timeout=5)) as db:
                count = db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
            return {"entries": count, "bytes": size, "available": True}
        except (OSError, sqlite3.Error):
            return {"entries": 0, "bytes": 0, "available": False}

    def clear(self) -> bool:
        if not self.path.is_file():
            return True
        try:
            with closing(self._connect()) as db:
                db.execute("DELETE FROM embeddings")
                db.commit()
            return True
        except sqlite3.DatabaseError as exc:
            if not any(marker in str(exc).lower() for marker in
                       ("not a database", "malformed")):
                return False
            try:
                self.path.unlink()
                return True
            except OSError:
                return False
        except (OSError, sqlite3.Error):
            return False
