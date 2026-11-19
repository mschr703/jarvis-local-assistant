"""Schreibstil, editierbare Entwürfe und idempotente Exporte."""

import hashlib
import json
import os
import re
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
from uuid import uuid4

from jarvis.db import Database, utcnow
from jarvis.documents import DocumentStore

MAX_IMPORT_BYTES = 80_000
PROFILE_KINDS = {"email", "text", "answer", "essay"}


class StyleProfileConflict(RuntimeError):
    """Die gespeicherte Profilfassung wurde inzwischen geändert."""


class DraftStore:
    def __init__(self, db: Database, data_dir: Path):
        self.db = db
        self.output_dir = Path(data_dir) / "outputs"

    def samples(self) -> list[dict]:
        with self.db.connect() as db:
            return [dict(row) for row in db.execute(
                """SELECT s.*,o.document_id,o.source_path,o.source_sha256,o.imported_at
                   FROM style_samples s LEFT JOIN style_sample_origins o ON o.sample_id=s.id
                   ORDER BY s.created_at DESC,s.id DESC""")]

    def add_sample(self, title: str, content: str, policy: str) -> dict:
        if not title.strip() or not content.strip() or policy not in {"local_only", "cloud_allowed"}:
            raise ValueError("Titel, Text und Cloud-Freigabe werden benötigt")
        sample_id, now = str(uuid4()), utcnow()
        with self.db.connect() as db:
            db.execute("INSERT INTO style_samples VALUES(?,?,?,?,?)",
                       (sample_id, title.strip(), content.strip(), policy, now))
        self.db.log("style_sample_added", f"Schreibprobe {sample_id}")
        return {"id": sample_id, "title": title.strip(), "content": content.strip(),
                "policy": policy, "created_at": now}

    def import_sample(self, documents: DocumentStore, document_id: str,
                      expected_sha256: str, title: str, confirm_own_text: bool) -> dict:
        if not confirm_own_text:
            raise ValueError("Import nur nach Bestätigung: Text selbst verfasst")
        title = " ".join(title.split())
        if not 1 <= len(title) <= 200:
            raise ValueError("Titel braucht 1–200 Zeichen")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ValueError("Gültigen SHA-256 der angezeigten Datei angeben")
        with documents.mutation_lock:
            item = documents.get_document(document_id)
            if (item is None or item["excluded"] or item["status"] != "indexed"
                    or item["sha256"] != expected_sha256):
                raise ValueError("Datei ist nicht mehr in der angezeigten Fassung indexiert")
            if Path(item["path"]).suffix.lower() not in {".txt", ".md", ".markdown"}:
                raise ValueError("Nur TXT- und Markdown-Dateien als Schreibprobe importieren")
            folder = documents.get_folder(item["folder_id"])
            if folder is None:
                raise ValueError("Freigabeordner fehlt")
            try:
                root = documents.granted_root(folder)
                path = Path(item["path"]).resolve(strict=True)
                if (path != Path(item["path"]) or not path.is_relative_to(root)
                        or not path.is_file()):
                    raise ValueError("Datei liegt nicht unverändert im freigegebenen Ordner")
                before = path.stat()
                if before.st_size > MAX_IMPORT_BYTES:
                    raise ValueError("Schreibprobe ist größer als 80 KB")
                with path.open("rb") as stream:
                    data = stream.read(MAX_IMPORT_BYTES + 1)
                after = path.stat()
            except OSError as exc:
                raise ValueError("Datei oder Freigabeordner ist nicht mehr lesbar") from exc
            if len(data) > MAX_IMPORT_BYTES:
                raise ValueError("Schreibprobe ist größer als 80 KB")
            if ((before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev)
                    != (after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev)
                    or after.st_size != item["size"] or after.st_mtime_ns != item["mtime_ns"]
                    or hashlib.sha256(data).hexdigest() != expected_sha256):
                raise ValueError("Datei wurde seit der Indexierung geändert; zuerst neu indexieren")
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("Schreibprobe braucht gültigen UTF-8-Text") from exc
            if not content.strip() or len(content) > 20_000:
                raise ValueError("Schreibprobe braucht 1–20.000 Textzeichen")
            sample_id, now = str(uuid4()), utcnow()
            with self.db.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
                if (current is None or current["excluded"] or current["status"] != "indexed"
                        or current["sha256"] != expected_sha256
                        or current["path"] != item["path"]
                        or current["folder_id"] != item["folder_id"]):
                    raise ValueError("Datei oder Freigabe wurde während des Imports geändert")
                existing = db.execute("""SELECT s.*,o.document_id,o.source_path,
                                        o.source_sha256,o.imported_at FROM style_sample_origins o
                                        JOIN style_samples s ON s.id=o.sample_id
                                        WHERE o.document_id=? AND o.source_sha256=?""",
                                      (document_id, expected_sha256)).fetchone()
                if existing is not None:
                    return dict(existing)
                db.execute("INSERT INTO style_samples VALUES(?,?,?,?,?)",
                           (sample_id, title, content, "local_only", now))
                db.execute("INSERT INTO style_sample_origins VALUES(?,?,?,?,?)",
                           (sample_id, document_id, item["path"], expected_sha256, now))
        self.db.log("style_sample_imported", f"Schreibprobe {sample_id} aus Dokument {document_id}")
        return {"id": sample_id, "title": title, "content": content,
                "policy": "local_only", "created_at": now, "document_id": document_id,
                "source_path": item["path"], "source_sha256": expected_sha256,
                "imported_at": now}

    def delete_sample(self, sample_id: str) -> None:
        with self.db.connect() as db:
            db.execute("DELETE FROM style_samples WHERE id=?", (sample_id,))
        self.db.log("style_sample_deleted", f"Schreibprobe {sample_id}")

    def selected_samples(self, ids: list[str], *, cloud_only: bool) -> list[dict]:
        unique = list(dict.fromkeys(ids))
        if len(unique) > 5:
            raise ValueError("Höchstens fünf Schreibproben")
        if not unique:
            raise ValueError("Mindestens eine Schreibprobe auswählen")
        placeholders = ",".join("?" for _ in unique)
        with self.db.connect() as db:
            rows = db.execute(f"SELECT * FROM style_samples WHERE id IN ({placeholders})", unique)
            found = {row["id"]: dict(row) for row in rows}
        if len(found) != len(unique):
            raise ValueError("Schreibprobe fehlt")
        selected = [found[item] for item in unique]
        if cloud_only and any(item["policy"] != "cloud_allowed" for item in selected):
            raise ValueError("Schreibprobe ist nicht für Cloud-Verarbeitung freigegeben")
        return selected

    def profile(self) -> dict:
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM style_profile WHERE id=1").fetchone()
            revision = db.execute("""SELECT COALESCE(MAX(revision),0) FROM
                                  style_profile_revisions WHERE scope='general'""").fetchone()[0]
            return {**(dict(row) if row else {"description": "", "policy": "local_only",
                                              "updated_at": None}), "revision": revision}

    def profile_history(self, scope: str) -> dict:
        if scope != "general" and scope not in PROFILE_KINDS:
            raise ValueError("Unbekannte Textsorte")
        with self.db.connect() as db:
            revisions = [dict(row) for row in db.execute(
                """SELECT revision,description,policy,restored_from,created_at
                   FROM style_profile_revisions WHERE scope=?
                   ORDER BY revision DESC""", (scope,))]
            current = db.execute("""SELECT COALESCE(MAX(revision),0)
                                   FROM style_profile_revisions WHERE scope=?""",
                                 (scope,)).fetchone()[0]
        return {"scope": scope, "revision": current, "versions": revisions}

    def _save_profile(self, scope: str, description: str, policy: str,
                      expected_revision: int | None, restored_from: int | None = None) -> bool:
        if scope != "general" and scope not in PROFILE_KINDS:
            raise ValueError("Unbekannte Textsorte")
        if policy not in {"local_only", "cloud_allowed"}:
            raise ValueError("Ungültige Cloud-Freigabe")
        cleaned = description.strip()
        if len(cleaned) > 4000:
            raise ValueError("Stilprofil ist zu lang")
        if expected_revision is not None and expected_revision < 0:
            raise ValueError("Ungültige Profilrevision")
        if scope != "general" and not cleaned:
            policy = "local_only"
        now, changed = utcnow(), False
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            revision = db.execute("""SELECT COALESCE(MAX(revision),0)
                                   FROM style_profile_revisions WHERE scope=?""",
                                  (scope,)).fetchone()[0]
            if expected_revision is not None and revision != expected_revision:
                raise StyleProfileConflict("Stilprofil wurde zwischenzeitlich geändert")
            if scope == "general":
                prior = db.execute("SELECT description,policy FROM style_profile WHERE id=1").fetchone()
                previous = (prior["description"], prior["policy"]) if prior else ("", "local_only")
            else:
                prior = db.execute("""SELECT description,policy FROM typed_style_profiles
                                    WHERE kind=?""", (scope,)).fetchone()
                previous = (prior["description"], prior["policy"]) if prior else ("", "local_only")
            if previous != (cleaned, policy) or restored_from is not None:
                changed = True
                if scope == "general":
                    db.execute("""INSERT INTO style_profile(id,description,policy,updated_at)
                               VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET
                               description=excluded.description,policy=excluded.policy,
                               updated_at=excluded.updated_at""", (cleaned, policy, now))
                elif cleaned:
                    db.execute("""INSERT INTO typed_style_profiles
                               (kind,description,policy,updated_at) VALUES(?,?,?,?)
                               ON CONFLICT(kind) DO UPDATE SET
                               description=excluded.description,policy=excluded.policy,
                               updated_at=excluded.updated_at""", (scope, cleaned, policy, now))
                else:
                    db.execute("DELETE FROM typed_style_profiles WHERE kind=?", (scope,))
                db.execute("""INSERT INTO style_profile_revisions
                           (scope,revision,description,policy,restored_from,created_at)
                           VALUES(?,?,?,?,?,?)""",
                           (scope, revision + 1, cleaned, policy, restored_from, now))
        if changed:
            self.db.log("style_profile_updated" if scope == "general" else
                        "typed_style_profile_updated", f"Stilprofil {scope}: {policy}")
        return changed

    def set_profile(self, description: str, policy: str,
                    expected_revision: int | None = None) -> dict:
        self._save_profile("general", description, policy, expected_revision)
        return self.profile()

    def typed_profile(self, kind: str) -> dict:
        if kind not in PROFILE_KINDS:
            raise ValueError("Unbekannte Textsorte")
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM typed_style_profiles WHERE kind=?", (kind,)).fetchone()
            revision = db.execute("""SELECT COALESCE(MAX(revision),0)
                                   FROM style_profile_revisions WHERE scope=?""",
                                  (kind,)).fetchone()[0]
        general = self.profile()
        if row is None:
            return {"kind": kind, "description": "", "policy": "local_only",
                    "updated_at": None, "override": False, "revision": revision,
                    "effective_description": general["description"],
                    "effective_policy": general["policy"]}
        selected = dict(row)
        return {**selected, "override": True, "revision": revision,
                "effective_description": selected["description"],
                "effective_policy": selected["policy"]}

    def set_typed_profile(self, kind: str, description: str, policy: str,
                          expected_revision: int | None = None) -> dict:
        self._save_profile(kind, description, policy, expected_revision)
        return self.typed_profile(kind)

    def restore_profile(self, scope: str, revision: int, expected_revision: int) -> dict:
        if scope != "general" and scope not in PROFILE_KINDS:
            raise ValueError("Unbekannte Textsorte")
        if revision < 1:
            raise ValueError("Ungültige Profilfassung")
        with self.db.connect() as db:
            version = db.execute("""SELECT description,policy FROM style_profile_revisions
                                    WHERE scope=? AND revision=?""", (scope, revision)).fetchone()
        if version is None:
            raise LookupError("Profilfassung nicht gefunden")
        self._save_profile(scope, version["description"], version["policy"],
                           expected_revision, restored_from=revision)
        return self.profile() if scope == "general" else self.typed_profile(scope)

    def profile_for_kind(self, kind: str) -> dict:
        selected = self.typed_profile(kind)
        if selected["override"]:
            return {"description": selected["description"], "policy": selected["policy"]}
        return {"description": selected["effective_description"],
                "policy": selected["effective_policy"]}

    def named_profile(self, name: str = "Maxim") -> dict | None:
        if name != "Maxim":
            raise ValueError("Unbekanntes Schreibprofil")
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM named_style_profiles WHERE name=?", (name,)).fetchone()
            return dict(row) if row else None

    def set_named_profile(self, name: str, description: str, policy: str,
                          expected_revision: int) -> dict:
        if name != "Maxim":
            raise ValueError("Unbekanntes Schreibprofil")
        cleaned = description.strip()
        if not cleaned or len(cleaned) > 4000:
            raise ValueError("Schreibprofil braucht 1–4000 Zeichen")
        if policy not in {"local_only", "cloud_allowed"}:
            raise ValueError("Ungültige Cloud-Freigabe")
        now = utcnow()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT * FROM named_style_profiles WHERE name=?", (name,)).fetchone()
            revision = prior["revision"] if prior else 0
            if revision != expected_revision:
                raise StyleProfileConflict("Schreibprofil wurde zwischenzeitlich geändert")
            if prior and (prior["description"], prior["policy"]) == (cleaned, policy):
                return dict(prior)
            db.execute("""INSERT INTO named_style_profiles VALUES(?,?,?,?,?)
                       ON CONFLICT(name) DO UPDATE SET description=excluded.description,
                       policy=excluded.policy,revision=excluded.revision,
                       updated_at=excluded.updated_at""",
                       (name, cleaned, policy, revision + 1, now))
            db.execute("INSERT INTO named_style_profile_revisions VALUES(?,?,?,?,?)",
                       (name, revision + 1, cleaned, policy, now))
        self.db.log("named_style_profile_updated", f"Schreibprofil {name} v{revision + 1}")
        return self.named_profile(name)

    def essay_feedback(self, limit: int = 50) -> list[dict]:
        with self.db.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,draft_id,draft_version,note,created_at FROM essay_style_feedback "
                "ORDER BY created_at DESC,id DESC LIMIT ?", (max(1, min(limit, 50)),))]

    def add_essay_feedback(self, draft_id: str, expected_version: int, note: str) -> dict:
        cleaned = " ".join(note.split())
        if not 10 <= len(cleaned) <= 500:
            raise ValueError("Stilhinweis braucht 10–500 Zeichen")
        normalized = cleaned.casefold()
        feedback_id, now = str(uuid4()), utcnow()
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            draft = db.execute("SELECT kind,version FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if draft is None:
                raise LookupError("Entwurf nicht gefunden")
            if draft["kind"] != "essay":
                raise ValueError("Stilhinweise sind nur für Essays verfügbar")
            if draft["version"] != expected_version:
                raise RuntimeError("Entwurfsversion ist veraltet")
            if db.execute("SELECT 1 FROM essay_style_feedback WHERE normalized_note=?",
                          (normalized,)).fetchone():
                raise ValueError("Dieser Stilhinweis ist bereits gespeichert")
            count = db.execute("SELECT COUNT(*) FROM essay_style_feedback").fetchone()[0]
            if count >= 50:
                raise ValueError("Höchstens 50 aktive Essay-Stilhinweise; zuerst einen löschen")
            db.execute("INSERT INTO essay_style_feedback VALUES(?,?,?,?,?,?)",
                       (feedback_id, draft_id, expected_version, cleaned, normalized, now))
        self.db.log("essay_style_feedback_added", f"Entwurf {draft_id} Version {expected_version}")
        return {"id": feedback_id, "draft_id": draft_id, "draft_version": expected_version,
                "note": cleaned, "created_at": now}

    def delete_essay_feedback(self, feedback_id: str) -> bool:
        with self.db.connect() as db:
            removed = db.execute("DELETE FROM essay_style_feedback WHERE id=?",
                                 (feedback_id,)).rowcount > 0
        if removed:
            self.db.log("essay_style_feedback_deleted", f"Stilhinweis {feedback_id}")
        return removed

    def create_draft(self, kind: str, input_text: str, brief: str, content: str,
                     sources: list[dict], uncertainty: str, style_snapshot: str,
                     rule_version: int | None = None, *, word_target: int | None = None,
                     outline: list[str] | None = None, status: str = "editing",
                     feedback_snapshot: list[dict] | None = None,
                     named_profile: dict | None = None,
                     recipient_snapshot: dict | None = None) -> dict:
        if kind not in {"email", "text", "answer", "essay"}:
            raise ValueError("Unbekannter Entwurfstyp")
        if status not in {"editing", "partial"}:
            raise ValueError("Ungültiger Entwurfsstatus")
        if kind == "essay" and (word_target is None or not 300 <= word_target <= 1200
                                or outline is None
                                or not 2 <= len(outline) <= (6 if word_target > 900 else 4)
                                or (word_target > 600 and len(outline) < 3)
                                or (word_target > 750 and len(outline) < 4)
                                or (word_target > 900 and len(outline) < 5)
                                or (word_target > 1050 and len(outline) < 6)):
            raise ValueError("Essay braucht passendes Wortziel und 2–6 Gliederungspunkte")
        draft_id, now = str(uuid4()), utcnow()
        with self.db.connect() as db:
            db.execute("""INSERT INTO drafts(id,kind,input_text,brief,content,sources_json,uncertainty,
                       style_snapshot,status,version,created_at,updated_at,rule_version)
                       VALUES(?,?,?,?,?,?,?,?,?,1,?,?,?)""",
                       (draft_id, kind, input_text, brief, content,
                        json.dumps(sources, ensure_ascii=False), uncertainty, style_snapshot, status, now,
                        now, rule_version))
            if kind == "essay":
                db.execute("INSERT INTO essay_metadata VALUES(?,?,?)",
                           (draft_id, word_target, json.dumps(outline, ensure_ascii=False)))
                db.execute("INSERT INTO essay_feedback_usage VALUES(?,?)",
                           (draft_id, json.dumps(feedback_snapshot or [], ensure_ascii=False)))
            if named_profile is not None:
                db.execute("INSERT INTO draft_named_style_usage VALUES(?,?,?,?)",
                           (draft_id, named_profile["name"], named_profile["revision"],
                            named_profile["description"]))
            if recipient_snapshot is not None:
                db.execute("INSERT INTO draft_recipients(draft_id,snapshot_json) VALUES(?,?)",
                           (draft_id, json.dumps(recipient_snapshot, ensure_ascii=False)))
        self.db.log("draft_created", f"Entwurf {draft_id}: {kind}")
        return self.get_draft(draft_id)

    def get_draft(self, draft_id: str) -> dict | None:
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None:
                return None
            draft = dict(row)
            draft["sources"] = json.loads(draft.pop("sources_json"))
            essay = db.execute("SELECT word_target,outline_json FROM essay_metadata WHERE draft_id=?",
                               (draft_id,)).fetchone()
            draft["word_target"] = essay["word_target"] if essay else None
            draft["outline"] = json.loads(essay["outline_json"]) if essay else []
            usage = db.execute("SELECT snapshot_json FROM essay_feedback_usage WHERE draft_id=?",
                               (draft_id,)).fetchone()
            draft["style_feedback"] = json.loads(usage["snapshot_json"]) if usage else []
            named = db.execute("""SELECT name,revision,description FROM draft_named_style_usage
                             WHERE draft_id=?""", (draft_id,)).fetchone()
            draft["named_style_profile"] = dict(named) if named else None
            recipient = db.execute("SELECT snapshot_json FROM draft_recipients WHERE draft_id=?",
                                   (draft_id,)).fetchone()
            draft["recipient_snapshot"] = json.loads(recipient["snapshot_json"]) if recipient else None
            draft["actual_words"] = len(re.findall(r"\b\w+(?:[-’']\w+)*\b", draft["content"]))
            draft["exports"] = [dict(item) for item in db.execute(
                "SELECT format,path,sha256,created_at FROM draft_exports WHERE draft_id=?", (draft_id,)
            )]
            return draft

    def drafts(self) -> list[dict]:
        with self.db.connect() as db:
            ids = [row["id"] for row in db.execute("SELECT id FROM drafts ORDER BY updated_at DESC")]
        return [self.get_draft(draft_id) for draft_id in ids]

    def update_draft(self, draft_id: str, content: str, expected_version: int) -> dict:
        if not content.strip():
            raise ValueError("Entwurf darf nicht leer sein")
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT version FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None:
                raise ValueError("Entwurf nicht gefunden")
            if row["version"] != expected_version:
                raise ValueError("Entwurf wurde inzwischen geändert; bitte neu laden")
            if db.execute("SELECT 1 FROM draft_exports WHERE draft_id=?", (draft_id,)).fetchone():
                raise ValueError("Exportierter Entwurf ist gesperrt; erstelle einen neuen Entwurf")
            db.execute("UPDATE drafts SET content=?,version=version+1,updated_at=? WHERE id=? AND version=?",
                       (content, utcnow(), draft_id, expected_version))
        self.db.log("draft_updated", f"Entwurf {draft_id}")
        return self.get_draft(draft_id)

    def export_draft(self, draft_id: str, format: str, expected_version: int) -> dict:
        if format not in {"txt", "md", "eml"}:
            raise ValueError("Format muss txt, md oder eml sein")
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            draft = db.execute("SELECT kind,brief,content,version FROM drafts WHERE id=?",
                               (draft_id,)).fetchone()
            if draft is None:
                raise ValueError("Entwurf nicht gefunden")
            if draft["version"] != expected_version:
                raise ValueError("Entwurf wurde inzwischen geändert; bitte neu laden")
            if format == "eml" and draft["kind"] != "email":
                raise ValueError("EML ist nur für E-Mail-Entwürfe verfügbar")
            if self.output_dir.is_symlink() or self.output_dir.is_junction():
                raise ValueError("Ausgabeordner darf kein Link oder Junction sein")
            self.output_dir.mkdir(parents=True, exist_ok=True)
            path = self.output_dir / f"{draft_id}.{format}"
            if path.is_symlink():
                raise ValueError("Exportdatei darf kein Symlink sein")
            prior = db.execute("SELECT * FROM draft_exports WHERE draft_id=? AND format=?",
                               (draft_id, format)).fetchone()
            if prior:
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != prior["sha256"]:
                    raise ValueError("Exportdatei fehlt oder wurde außerhalb von Jarvis verändert")
                return dict(prior)
            if format == "eml":
                message = EmailMessage()
                subject = re.sub(r"[\x00-\x20\x7f]+", " ", draft["brief"]).strip()[:120]
                message["Subject"] = subject or "Entwurf"
                message.set_content(draft["content"])
                data = message.as_bytes(policy=SMTP)
            else:
                data = (draft["content"].rstrip() + "\n").encode("utf-8")
            digest = hashlib.sha256(data).hexdigest()
            created = False
            try:
                with path.open("xb") as stream:
                    created = True
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise ValueError("Exportpfad enthält eine andere Datei") from None
            except BaseException:
                if created:
                    path.unlink(missing_ok=True)
                raise
            created_at = utcnow()
            db.execute("INSERT INTO draft_exports VALUES(?,?,?,?,?)",
                       (draft_id, format, str(path), digest, created_at))
            db.execute("UPDATE drafts SET status='saved',updated_at=? WHERE id=?", (utcnow(), draft_id))
        self.db.log("draft_exported", f"Entwurf {draft_id}: {format}")
        return {"draft_id": draft_id, "format": format, "path": str(path),
                "sha256": digest, "created_at": created_at}
