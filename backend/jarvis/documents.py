"""Erlaubte lokale Quellen, FTS5-Suche und explizite Erinnerungen."""

import hashlib
import io
import os
import re
import threading
from pathlib import Path
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from jarvis.db import Database, utcnow
from jarvis.ocr import OcrUnavailable, scan_pdf

SUPPORTED = {".txt", ".md", ".markdown", ".pdf", ".docx"}
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_FILES_PER_SCAN = 1000
MAX_DIRECTORIES_PER_SCAN = 2000
MAX_EXTRACTED_CHARS = 2_000_000
MAX_PDF_PAGES = 200
MAX_DOCX_ENTRIES = 1000
MAX_DOCX_XML_BYTES = 8 * 1024 * 1024
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class SourceChanged(ValueError):
    """The original changed while a deliberately selected OCR operation ran."""


def fts_query(text: str) -> str:
    words = re.findall(r"\w+", text, flags=re.UNICODE)[:12]
    return " AND ".join(f'"{word}"' for word in words)


def chunks(text: str, size: int = 1200, overlap: int = 100) -> list[str]:
    clean = re.sub(r"\r\n?", "\n", text).strip()
    if not clean:
        return []
    result = []
    start = 0
    while start < len(clean):
        end = min(len(clean), start + size)
        result.append(clean[start:end])
        if end == len(clean):
            break
        start = end - overlap
    return result


def extract_docx(data: bytes) -> str:
    """Read the main Word document in document order without extracting ZIP members."""
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > MAX_DOCX_ENTRIES:
                raise ValueError("DOCX enthält zu viele ZIP-Einträge")
            document = archive.getinfo("word/document.xml")
            if document.file_size > MAX_DOCX_XML_BYTES:
                raise ValueError("DOCX-XML ist zu groß")
            if document.flag_bits & 1:
                raise ValueError("Verschlüsseltes DOCX")
            with archive.open(document) as stream:
                xml = stream.read(MAX_DOCX_XML_BYTES + 1)
    except (BadZipFile, KeyError, RuntimeError, OSError) as exc:
        raise ValueError("Ungültiges oder verschlüsseltes DOCX") from exc
    if len(xml) > MAX_DOCX_XML_BYTES:
        raise ValueError("DOCX-XML ist zu groß")
    try:
        root = ElementTree.fromstring(xml, forbid_dtd=True)
    except (ElementTree.ParseError, DefusedXmlException) as exc:
        raise ValueError("Ungültiges DOCX-XML") from exc
    body = root.find(f"{WORD_NS}body")
    if body is None:
        raise ValueError("DOCX-Haupttext fehlt")
    paragraphs: list[str] = []
    length = 0
    for paragraph in body.iter(f"{WORD_NS}p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == f"{WORD_NS}t":
                parts.append(node.text or "")
            elif node.tag == f"{WORD_NS}tab":
                parts.append("\t")
            elif node.tag in {f"{WORD_NS}br", f"{WORD_NS}cr"}:
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            length += len(text)
            if length > MAX_EXTRACTED_CHARS:
                raise ValueError("DOCX-Text ist zu lang")
            paragraphs.append(text)
    return "\n".join(paragraphs)


class DocumentStore:
    def __init__(self, db: Database):
        self.db = db
        self._scan_lock = threading.Lock()
        self.mutation_lock = threading.RLock()

    def add_folder(self, path: str, policy: str) -> dict:
        if policy not in {"local_only", "cloud_allowed"}:
            raise ValueError("Ungültige Cloud-Freigabe")
        supplied = Path(path).expanduser()
        if not supplied.is_absolute() or not supplied.is_dir():
            raise ValueError("Es wird ein vorhandener absoluter Ordnerpfad benötigt")
        root = supplied.resolve(strict=True)
        folder_id, now = str(uuid4()), utcnow()
        with self.db.connect() as db:
            prior = db.execute("SELECT * FROM folders WHERE path=?", (str(root),)).fetchone()
            if prior:
                return dict(prior)
            for row in db.execute("SELECT path FROM folders"):
                existing = Path(row["path"])
                if root.is_relative_to(existing) or existing.is_relative_to(root):
                    raise ValueError("Überlappende Freigabeordner sind nicht erlaubt")
            db.execute("INSERT INTO folders VALUES(?,?,?,?)", (folder_id, str(root), policy, now))
        self.db.log("folder_added", f"Dokumentenordner {folder_id}")
        return {"id": folder_id, "path": str(root), "policy": policy, "created_at": now}

    def folders(self) -> list[dict]:
        with self.db.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM folders ORDER BY path")]

    def get_folder(self, folder_id: str) -> dict | None:
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM folders WHERE id=?", (folder_id,)).fetchone()
            return dict(row) if row else None

    @staticmethod
    def granted_root(folder: dict) -> Path:
        saved = Path(folder["path"])
        current = saved.resolve(strict=True)
        if current != saved or not current.is_dir():
            raise ValueError("Freigabeordner wurde umgeleitet; Ordner erneut freigeben")
        return current

    @staticmethod
    def _current_source_bytes(document: dict) -> bytes | None:
        """Return the exact indexed bytes only while the granted original still matches."""
        if (document["excluded"] or document["status"] != "indexed"
                or not document["sha256"] or not isinstance(document["size"], int)
                or not 0 < document["size"] <= MAX_FILE_BYTES):
            return None
        try:
            root = DocumentStore.granted_root({"path": document["folder_path"]})
            path = Path(document["path"])
            if (path.is_symlink() or path.is_junction()
                    or path.resolve(strict=True) != path
                    or not path.is_relative_to(root)
                    or path.stat().st_size != document["size"]):
                return None
            with path.open("rb") as source:
                contents = source.read(document["size"] + 1)
            if (len(contents) == document["size"]
                    and path.resolve(strict=True) == path
                    and path.stat().st_size == document["size"]
                    and hashlib.sha256(contents).hexdigest() == document["sha256"]):
                return contents
        except (OSError, ValueError):
            pass
        return None

    @staticmethod
    def source_is_current(document: dict) -> bool:
        """Verify one indexed source against its granted original before citing it."""
        return DocumentStore._current_source_bytes(document) is not None

    def document_freshness(self, document_id: str) -> dict:
        """Prüft eine einzelne Originaldatei vor dem Öffnen aus der Suche."""
        with self.db.connect() as db:
            row = db.execute("""SELECT d.*,f.path AS folder_path FROM documents d
                                JOIN folders f ON f.id=d.folder_id WHERE d.id=?""",
                             (document_id,)).fetchone()
        if row is None:
            raise LookupError("Dokument nicht mehr vorhanden")
        snapshot = dict(row)
        current = self.source_is_current(snapshot)
        with self.db.connect() as db:
            latest = db.execute("""SELECT d.*,f.path AS folder_path FROM documents d
                                   JOIN folders f ON f.id=d.folder_id WHERE d.id=?""",
                                (document_id,)).fetchone()
        if latest is None or any(latest[key] != snapshot[key] for key in
                                 ("folder_id", "folder_path", "path", "sha256", "size",
                                  "status", "excluded", "policy")):
            raise ValueError("Index während der Prüfung geändert; erneut prüfen")
        return {"document_id": document_id,
                "status": "current" if current else "needs_reindex",
                "index_sha256": snapshot["sha256"]}

    def folder_freshness(self, folder_id: str) -> dict:
        """Inspect a bounded set of indexed originals without changing the index."""
        folder = self.get_folder(folder_id)
        if folder is None:
            raise LookupError("Freigabeordner nicht gefunden")
        self.granted_root(folder)
        with self.db.connect() as db:
            rows = [dict(row) for row in db.execute("""
                SELECT d.*,f.path AS folder_path FROM documents d
                JOIN folders f ON f.id=d.folder_id
                WHERE d.folder_id=? AND d.status='indexed' AND d.excluded=0
                ORDER BY d.path LIMIT 101
            """, (folder_id,))]
        limited = len(rows) > 100
        checked = rows[:100]
        stale = []
        for row in checked:
            if not self.source_is_current(row):
                stale.append({"document_id": row["id"], "path": row["path"],
                              "status": "needs_reindex"})
        with self.db.connect() as db:
            current_folder = db.execute("SELECT path FROM folders WHERE id=?", (folder_id,)).fetchone()
            if current_folder is None or current_folder["path"] != folder["path"]:
                raise LookupError("Freigabeordner nicht mehr vorhanden")
            ids = [row["id"] for row in checked]
            current = {row["id"]: row for row in db.execute(f"""
                SELECT id,path,sha256,size FROM documents
                WHERE folder_id=? AND status='indexed' AND excluded=0
                  AND id IN ({','.join('?' for _ in ids)})
            """, (folder_id, *ids))} if ids else {}
        if any((row["id"] not in current
                or any(current[row["id"]][key] != row[key]
                       for key in ("path", "sha256", "size"))) for row in checked):
            raise ValueError("Index während der Prüfung geändert; erneut prüfen")
        self.granted_root(folder)
        return {"folder_id": folder_id, "checked": len(checked),
                "current": len(checked) - len(stale), "stale": len(stale),
                "limited": limited, "stale_sources": stale}

    def original_bytes(self, document_id: str) -> tuple[dict, bytes]:
        """Serve a verified snapshot, never a later unverified filesystem read."""
        with self.db.connect() as db:
            row = db.execute("""SELECT d.*,f.path AS folder_path FROM documents d
                              JOIN folders f ON f.id=d.folder_id WHERE d.id=?""",
                             (document_id,)).fetchone()
        if row is None or row["excluded"] or row["status"] != "indexed":
            raise LookupError("Indexiertes Dokument nicht gefunden")
        item = dict(row)
        contents = self._current_source_bytes(item)
        if contents is None:
            raise ValueError("Originaldatei weicht vom Index ab; zuerst neu indexieren")
        with self.db.connect() as db:
            current = db.execute("""SELECT d.sha256,d.size,d.status,d.excluded,d.path,
                                  f.path AS folder_path FROM documents d
                                  JOIN folders f ON f.id=d.folder_id WHERE d.id=?""",
                                 (document_id,)).fetchone()
        if current is None or any(current[key] != item[key] for key in
                                  ("sha256", "size", "status", "excluded", "path",
                                   "folder_path")):
            raise ValueError("Dokumentfreigabe oder Index hat sich geändert")
        return item, contents

    def set_folder_policy(self, folder_id: str, policy: str) -> dict:
        if policy not in {"local_only", "cloud_allowed"}:
            raise ValueError("Ungültige Cloud-Freigabe")
        with self.db.connect() as db:
            if db.execute("SELECT 1 FROM folders WHERE id=?", (folder_id,)).fetchone() is None:
                raise ValueError("Ordner nicht gefunden")
            db.execute("UPDATE folders SET policy=? WHERE id=?", (policy, folder_id))
            db.execute("UPDATE documents SET policy=? WHERE folder_id=?", (policy, folder_id))
        self.db.log("folder_policy_changed", f"Ordner {folder_id}: {policy}")
        return self.get_folder(folder_id)

    def remove_folder(self, folder_id: str) -> None:
        with self.mutation_lock, self.db.connect() as db:
            db.execute("DELETE FROM folders WHERE id=?", (folder_id,))
        self.db.log("folder_removed", f"Ordner {folder_id}; Originaldateien unverändert")

    def index_folder(self, folder_id: str) -> dict:
        with self._scan_lock:
            return self._index_folder(folder_id)

    def index_document(self, document_id: str) -> dict:
        """Refresh only an existing document inside its unchanged grant."""
        return self._index_document(document_id)

    def ocr_document(self, document_id: str) -> dict:
        """Read one pending scanned PDF with local OCR after explicit selection."""
        return self._index_document(document_id, ocr=True)

    def _index_document(self, document_id: str, *, ocr: bool = False) -> dict:
        with self._scan_lock, self.mutation_lock:
            item = self.get_document(document_id)
            if item is None:
                raise LookupError("Dokument nicht gefunden")
            if item["excluded"]:
                raise ValueError("Ausgeschlossenes Dokument zuerst wieder einbeziehen")
            if ocr and (Path(item["path"]).suffix.lower() != ".pdf"
                        or item["status"] != "ocr_pending" or not item["sha256"]):
                raise ValueError("OCR ist nur für ein PDF mit ausstehender Texterkennung möglich")
            folder = self.get_folder(item["folder_id"])
            if folder is None:
                raise LookupError("Freigabeordner nicht mehr vorhanden")
            root = self.granted_root(folder)
            path = Path(item["path"])
            if path.suffix.lower() not in SUPPORTED or not path.is_relative_to(root):
                raise ValueError("Dokument liegt nicht im freigegebenen Ordner")
            if path.is_symlink() or path.is_junction():
                raise ValueError("Dokumentpfad wurde umgeleitet")
            parent = path.parent.resolve(strict=True)
            if parent != path.parent or not parent.is_relative_to(root):
                raise ValueError("Dokumentordner wurde umgeleitet")
            if not path.exists():
                with self.db.connect() as db:
                    db.execute("DELETE FROM documents WHERE id=? AND path=?",
                               (document_id, str(path)))
                self.db.log("document_removed", f"Dokument {document_id} fehlt im Original")
                return {"document_id": document_id, "outcome": "removed", "document": None}
            if path.resolve(strict=True) != path or not path.is_file():
                raise ValueError("Dokumentpfad wurde umgeleitet")
            try:
                changed = self._index_file(folder, path, ocr=ocr,
                                           expected_sha=item["sha256"] if ocr else None)
                outcome = "indexed" if changed else "unchanged"
            except OcrPending:
                outcome = "ocr_pending"
            except (SourceChanged, OcrUnavailable):
                raise
            except (OSError, ValueError, PdfReadError) as exc:
                reason = (str(exc) if str(exc) == "Datei wurde während der Indexierung geändert"
                          or (ocr and isinstance(exc, ValueError))
                          or (path.suffix.lower() == ".docx" and isinstance(exc, ValueError))
                          else type(exc).__name__)
                self._record_error(folder, path, reason)
                outcome = "error"
            current = self.get_document(document_id)
            if outcome == "unchanged" and current and current["status"] == "ocr_pending":
                outcome = "ocr_pending"
            self.db.log("document_indexed", f"Dokument {document_id}: {outcome}")
            return {"document_id": document_id, "outcome": outcome, "document": current}

    def _index_folder(self, folder_id: str) -> dict:
        folder = self.get_folder(folder_id)
        if folder is None:
            raise ValueError("Ordner nicht gefunden")
        root = self.granted_root(folder)
        counts = {"indexed": 0, "unchanged": 0, "ocr_pending": 0,
                  "skipped": 0, "errors": 0, "removed": 0, "limited": 0}
        seen: set[str] = set()
        visited = 0
        visited_directories = 0
        limit_hit = False
        scan_incomplete = False
        def walk_error(_error: OSError) -> None:
            nonlocal scan_incomplete
            scan_incomplete = True
            counts["errors"] += 1

        for base, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
            visited_directories += 1
            if visited_directories > MAX_DIRECTORIES_PER_SCAN:
                counts["skipped"] += 1
                counts["limited"] = 1
                limit_hit = True
                break
            safe_dirs = []
            for directory in dirs:
                candidate = Path(base) / directory
                try:
                    if (not candidate.is_symlink() and not candidate.is_junction()
                            and candidate.resolve(strict=True).is_relative_to(root)):
                        safe_dirs.append(directory)
                except OSError:
                    scan_incomplete = True
                    counts["skipped"] += 1
                    counts["errors"] += 1
            dirs[:] = safe_dirs
            for name in files:
                if Path(name).suffix.lower() not in SUPPORTED:
                    continue
                if visited >= MAX_FILES_PER_SCAN:
                    counts["skipped"] += 1
                    counts["limited"] = 1
                    limit_hit = True
                    break
                visited += 1
                candidate = Path(base) / name
                resolved: Path | None = None
                try:
                    resolved = candidate.resolve(strict=True)
                    if not resolved.is_relative_to(root) or not resolved.is_file():
                        counts["skipped"] += 1
                        continue
                    seen.add(str(resolved))
                    if self._index_file(folder, resolved):
                        counts["indexed"] += 1
                    else:
                        counts["unchanged"] += 1
                except OcrPending:
                    counts["ocr_pending"] += 1
                except (OSError, ValueError, PdfReadError) as exc:
                    counts["errors"] += 1
                    if resolved is None or str(resolved) not in seen:
                        scan_incomplete = True
                    if resolved is not None and resolved.is_relative_to(root):
                        reason = (str(exc) if str(exc) == "Datei wurde während der Indexierung geändert"
                                  or (resolved.suffix.lower() == ".docx"
                                      and isinstance(exc, ValueError))
                                  else type(exc).__name__)
                        self._record_error(folder, resolved, reason)
            if limit_hit:
                dirs[:] = []
                break
        if not limit_hit and not scan_incomplete:
            with self.db.connect() as db:
                old = db.execute("SELECT id,path FROM documents WHERE folder_id=?", (folder_id,)).fetchall()
                for item in old:
                    if item["path"] not in seen:
                        db.execute("DELETE FROM documents WHERE id=?", (item["id"],))
                        counts["removed"] += 1
        self.db.log("folder_indexed", f"Ordner {folder_id}: {counts}")
        return counts

    def _index_file(self, folder: dict, path: Path, *, ocr: bool = False,
                    expected_sha: str | None = None) -> bool:
        if path.is_symlink() or path.is_junction() or path.resolve(strict=True) != path:
            raise ValueError("Datei wurde während der Indexierung umgeleitet")
        stat = path.stat()
        if stat.st_size > MAX_FILE_BYTES:
            raise ValueError("Datei zu groß")
        with self.db.connect() as db:
            prior = db.execute("SELECT * FROM documents WHERE path=?", (str(path),)).fetchone()
            if prior and prior["excluded"]:
                return False
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("Datei zu groß")
        after = path.stat()
        if path.is_symlink() or path.is_junction() or path.resolve(strict=True) != path:
            raise ValueError("Datei wurde während der Indexierung umgeleitet")
        if ((stat.st_size, stat.st_mtime_ns, stat.st_ino, stat.st_dev)
                != (after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev)):
            raise ValueError("Datei wurde während der Indexierung geändert")
        digest = hashlib.sha256(data).hexdigest()
        if expected_sha is not None and digest != expected_sha:
            raise SourceChanged("Original geändert. Datei zuerst neu indexieren.")
        if (not ocr and prior and prior["status"] in {"indexed", "ocr_pending"}
                and prior["sha256"] == digest and prior["policy"] == folder["policy"]):
            return False
        segments: list[tuple[int | None, str]] = []
        if path.suffix.lower() == ".pdf":
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise ValueError("Verschlüsseltes PDF")
            if len(reader.pages) > MAX_PDF_PAGES:
                raise ValueError("PDF hat zu viele Seiten")
            extracted = 0
            for number, page in enumerate(reader.pages, start=1):
                content = page.extract_text() or ""
                extracted += len(content)
                if extracted > MAX_EXTRACTED_CHARS:
                    raise ValueError("PDF-Text ist zu lang")
                segments.extend((number, piece) for piece in chunks(content))
            if ocr and not segments:
                for number, content in scan_pdf(data):
                    segments.extend((number, piece) for piece in chunks(content))
        elif path.suffix.lower() == ".docx":
            segments.extend((None, piece) for piece in chunks(extract_docx(data)))
        else:
            content = data.decode("utf-8", "replace")
            if len(content) > MAX_EXTRACTED_CHARS:
                raise ValueError("Dokumenttext ist zu lang")
            segments.extend((None, piece) for piece in chunks(content))
        status = "indexed" if segments else "ocr_pending"
        if ocr:
            if path.is_symlink() or path.is_junction() or path.resolve(strict=True) != path:
                raise SourceChanged("Original wurde während der OCR umgeleitet")
            current_stat = path.stat()
            if ((stat.st_size, stat.st_mtime_ns, stat.st_ino, stat.st_dev)
                    != (current_stat.st_size, current_stat.st_mtime_ns,
                        current_stat.st_ino, current_stat.st_dev)):
                raise SourceChanged("Original wurde während der OCR geändert")
            with path.open("rb") as stream:
                if hashlib.sha256(stream.read(MAX_FILE_BYTES + 1)).hexdigest() != digest:
                    raise SourceChanged("Original wurde während der OCR geändert")
        now = utcnow()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT policy FROM folders WHERE id=?", (folder["id"],)).fetchone()
            if current is None:
                return False
            current_document = db.execute("SELECT id,excluded FROM documents WHERE path=?",
                                          (str(path),)).fetchone()
            if current_document and current_document["excluded"]:
                return False
            document_id = current_document["id"] if current_document else str(uuid4())
            db.execute("""INSERT INTO documents(id,folder_id,path,sha256,mtime_ns,size,policy,status,indexed_at)
                       VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET
                       sha256=excluded.sha256,mtime_ns=excluded.mtime_ns,size=excluded.size,
                       policy=excluded.policy,status=excluded.status,indexed_at=excluded.indexed_at,
                       error=NULL""",
                       (document_id, folder["id"], str(path), digest, stat.st_mtime_ns,
                        stat.st_size, current["policy"], status, now))
            db.execute("DELETE FROM passages WHERE document_id=?", (document_id,))
            db.executemany("INSERT INTO passages(document_id,page,ordinal,text) VALUES(?,?,?,?)",
                           ((document_id, page, index, text)
                            for index, (page, text) in enumerate(segments)))
        if status == "ocr_pending":
            raise OcrPending()
        return True

    def _record_error(self, folder: dict, path: Path, error: str) -> None:
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT policy FROM folders WHERE id=?", (folder["id"],)).fetchone()
            if current is None:
                return
            prior = db.execute("SELECT id FROM documents WHERE path=?", (str(path),)).fetchone()
            document_id = prior["id"] if prior else str(uuid4())
            db.execute("""INSERT INTO documents(id,folder_id,path,policy,status,indexed_at,error)
                       VALUES(?,?,?,?,'error',?,?) ON CONFLICT(path) DO UPDATE SET
                       status='error',error=excluded.error,indexed_at=excluded.indexed_at""",
                       (document_id, folder["id"], str(path), current["policy"], utcnow(), error))
            db.execute("DELETE FROM passages WHERE document_id=?", (document_id,))

    def documents(self, folder_id: str | None = None) -> list[dict]:
        with self.db.connect() as db:
            if folder_id:
                rows = db.execute("SELECT * FROM documents WHERE folder_id=? ORDER BY path", (folder_id,))
            else:
                rows = db.execute("SELECT * FROM documents ORDER BY path")
            return [dict(row) for row in rows]

    def get_document(self, document_id: str) -> dict | None:
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            return dict(row) if row else None

    def set_excluded(self, document_id: str, excluded: bool) -> dict:
        with self.mutation_lock, self.db.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            if row is None:
                raise ValueError("Dokument nicht gefunden")
            if excluded:
                db.execute("UPDATE documents SET excluded=1 WHERE id=?", (document_id,))
                db.execute("DELETE FROM passages WHERE document_id=?", (document_id,))
                db.execute("DELETE FROM markdown_revisions WHERE document_id=?", (document_id,))
            else:
                db.execute("""UPDATE documents SET excluded=0,status='pending_index',
                           sha256=NULL,error=NULL WHERE id=?""", (document_id,))
        self.db.log("document_excluded" if excluded else "document_included", f"Dokument {document_id}")
        return self.get_document(document_id)

    def search(self, query: str, *, cloud_only: bool = False, limit: int = 20) -> list[dict]:
        match = fts_query(query)
        if not match:
            return []
        with self.db.connect() as db:
            rows = db.execute("""SELECT p.id AS passage_id,p.document_id,p.page,p.text,
                              d.path,d.policy,d.sha256,d.size,d.status,d.excluded,
                              f.path AS folder_path,bm25(passages_fts) AS rank
                              FROM passages_fts JOIN passages p ON p.id=passages_fts.rowid
                              JOIN documents d ON d.id=p.document_id
                              JOIN folders f ON f.id=d.folder_id
                              WHERE passages_fts MATCH ? AND d.excluded=0 AND d.status='indexed'
                              AND (?=0 OR d.policy='cloud_allowed')
                              ORDER BY rank LIMIT ?""", (match, int(cloud_only), limit))
            found = [dict(row) for row in rows]
        freshness = {}
        for item in found:
            document_id = item["document_id"]
            if document_id not in freshness:
                freshness[document_id] = self.source_is_current(item)
            item["source_status"] = "current" if freshness[document_id] else "needs_reindex"
            for internal in ("size", "status", "excluded", "folder_path"):
                item.pop(internal)
        return found

    def selected_passages(self, passage_ids: list[int], *, cloud_only: bool = False) -> list[dict]:
        unique = list(dict.fromkeys(passage_ids))
        if len(unique) > 5:
            raise ValueError("Höchstens fünf Fundstellen pro Anfrage")
        if not unique:
            return []
        placeholders = ",".join("?" for _ in unique)
        with self.db.connect() as db:
            rows = db.execute(
                f"""SELECT p.id AS passage_id,p.document_id,p.page,p.text,d.path,d.policy,d.sha256,
                    d.size,d.status,d.excluded,f.path AS folder_path
                    FROM passages p JOIN documents d ON d.id=p.document_id
                    JOIN folders f ON f.id=d.folder_id
                    WHERE p.id IN ({placeholders}) AND d.excluded=0 AND d.status='indexed'
                    AND (?=0 OR d.policy='cloud_allowed')""", (*unique, int(cloud_only)))
            found = {row["passage_id"]: dict(row) for row in rows}
        if len(found) != len(unique):
            raise ValueError("Fundstelle fehlt oder ist für Cloud-Nutzung nicht freigegeben")
        selected = [found[item] for item in unique]
        if not all(self.source_is_current(item) for item in selected):
            raise ValueError("Originaldatei weicht vom Index ab; zuerst Index aktualisieren")
        for item in selected:
            for internal in ("size", "status", "excluded", "folder_path"):
                item.pop(internal)
        return selected

    def add_memory(self, text: str, source_ref: str, policy: str) -> dict:
        if not text.strip() or not source_ref.strip():
            raise ValueError("Erinnerung und Quelle werden benötigt")
        if policy not in {"local_only", "cloud_allowed"}:
            raise ValueError("Ungültige Cloud-Freigabe")
        memory_id, now = str(uuid4()), utcnow()
        with self.db.connect() as db:
            db.execute("INSERT INTO memories VALUES(?,?,?,?,?,?)",
                       (memory_id, text.strip(), source_ref.strip(), policy, now, now))
            db.execute("INSERT INTO memories_fts(id,text) VALUES(?,?)", (memory_id, text.strip()))
        self.db.log("memory_added", f"Erinnerung {memory_id}")
        return self.get_memory(memory_id)

    def get_memory(self, memory_id: str) -> dict | None:
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
            return dict(row) if row else None

    def memories(self, query: str | None = None) -> list[dict]:
        with self.db.connect() as db:
            if query:
                match = fts_query(query)
                if not match:
                    return []
                rows = db.execute("""SELECT m.* FROM memories_fts f JOIN memories m ON m.id=f.id
                                  WHERE memories_fts MATCH ? ORDER BY bm25(memories_fts) LIMIT 50""",
                                  (match,))
            else:
                rows = db.execute("SELECT * FROM memories ORDER BY updated_at DESC")
            return [dict(row) for row in rows]

    def update_memory(self, memory_id: str, text: str, source_ref: str, policy: str) -> dict:
        if not text.strip() or not source_ref.strip():
            raise ValueError("Erinnerung und Quelle werden benötigt")
        if policy not in {"local_only", "cloud_allowed"}:
            raise ValueError("Ungültige Cloud-Freigabe")
        with self.db.connect() as db:
            if db.execute("SELECT 1 FROM memories WHERE id=?", (memory_id,)).fetchone() is None:
                raise ValueError("Erinnerung nicht gefunden")
            db.execute("UPDATE memories SET text=?,source_ref=?,policy=?,updated_at=? WHERE id=?",
                       (text.strip(), source_ref.strip(), policy, utcnow(), memory_id))
            db.execute("DELETE FROM memories_fts WHERE id=?", (memory_id,))
            db.execute("INSERT INTO memories_fts(id,text) VALUES(?,?)", (memory_id, text.strip()))
        self.db.log("memory_updated", f"Erinnerung {memory_id}")
        return self.get_memory(memory_id)

    def delete_memory(self, memory_id: str) -> None:
        with self.db.connect() as db:
            db.execute("DELETE FROM memories_fts WHERE id=?", (memory_id,))
            db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        self.db.log("memory_deleted", f"Erinnerung {memory_id}")


class OcrPending(Exception):
    """PDF ohne extrahierbare Textschicht."""
