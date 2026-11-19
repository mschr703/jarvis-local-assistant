import builtins
import hashlib
import json
import os
import sqlite3
import warnings
import zipfile

import pytest

from jarvis.backup import copy_bounded, create_backup, restore_backup
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.drafts import DraftStore
from jarvis.instance import InstanceLock
from jarvis.time_model import ExceptionInput, RuleInput, TimeModelStore


def rewrite_manifest(source, destination, change):
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination, "w") as rewritten:
        manifest = json.loads(original.read("manifest.json"))
        change(manifest)
        for name in original.namelist():
            payload = (json.dumps(manifest).encode("utf-8") if name == "manifest.json"
                       else original.read(name))
            rewritten.writestr(name, payload)


def test_backup_restore_into_new_profile_without_secrets(tmp_path):
    source = tmp_path / "source"
    db = Database(source / "jarvis.db")
    memory = DocumentStore(db).add_memory("Projekt Atlas", "Besprechung", "local_only")
    time_model = TimeModelStore(db)
    work = time_model.create_rule(RuleInput(title="Arbeit", kind="work", weekdays=[0],
                                            start_time="09:00", end_time="17:00",
                                            timezone="Europe/Paris"))
    time_model.create_exception(ExceptionInput(date_local="2027-01-04", mode="skip",
                                               rule_id=work["id"]))
    outputs = source / "outputs"
    outputs.mkdir()
    (outputs / "draft.md").write_text("Hallo", encoding="utf-8")
    notes = source / "notes"
    notes.mkdir()
    (notes / "freigabe.txt").write_text("Bestätigt", encoding="utf-8")
    (source / ".env").write_text("JARVIS_OPENROUTER_API_KEY=secret", encoding="utf-8")
    profile = source / "browser-profile"
    profile.mkdir()
    (profile / "Cookies").write_text("secret-cookie", encoding="utf-8")
    archive = tmp_path / "backup.zip"
    info = create_backup(source, archive)
    assert info["schema"] == 63
    with zipfile.ZipFile(archive) as package:
        assert ".env" not in package.namelist()
        assert not any("browser-profile" in name for name in package.namelist())
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    assert (restored / "outputs" / "draft.md").read_text(encoding="utf-8") == "Hallo"
    assert (restored / "notes" / "freigabe.txt").read_text(encoding="utf-8") == "Bestätigt"
    assert DocumentStore(Database(restored / "jarvis.db")).get_memory(memory["id"])["text"] == "Projekt Atlas"
    restored_time = TimeModelStore(Database(restored / "jarvis.db"))
    assert restored_time.list_rules()[0]["title"] == "Arbeit"
    assert restored_time.availability("2027-01-04", "Europe/Paris")["free_minutes"] == 1440
    assert not (restored / ".env").exists()
    with pytest.raises(ValueError, match="noch nicht existierenden"):
        restore_backup(archive, restored)


def test_backup_restores_cad_models_but_not_engine_workdirs(tmp_path):
    source = tmp_path / "source"
    Database(source / "jarvis.db")
    model = source / "cad-models" / "12345678-1234-4234-8234-123456789abc"
    model.mkdir(parents=True)
    (model / "holder.py").write_text("model source", encoding="utf-8")
    (model / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    jobs = source / "cad-jobs" / "private-engine"
    jobs.mkdir(parents=True)
    (jobs / "state").write_text("transient", encoding="utf-8")
    archive = tmp_path / "cad-backup.zip"
    create_backup(source, archive)
    with zipfile.ZipFile(archive) as package:
        assert f"cad-models/{model.name}/holder.py" in package.namelist()
        assert not any(name.startswith("cad-jobs/") for name in package.namelist())
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    assert (restored / "cad-models" / model.name / "holder.py").read_text() == "model source"
    assert not (restored / "cad-jobs").exists()


def test_backup_includes_committed_changes_still_in_sqlite_wal(tmp_path):
    source = tmp_path / "source"
    database = Database(source / "jarvis.db")
    reader = sqlite3.connect(database.path)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM memories").fetchone()
        memory = DocumentStore(database).add_memory("WAL-Eintrag", "Neustartprobe", "local_only")
        assert (source / "jarvis.db-wal").stat().st_size > 0
        archive = tmp_path / "backup.zip"
        create_backup(source, archive)
    finally:
        reader.close()
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    assert DocumentStore(Database(restored / "jarvis.db")).get_memory(memory["id"])["text"] == "WAL-Eintrag"


def test_restore_rebases_export_paths_to_new_profile(tmp_path):
    source = tmp_path / "source"
    drafts = DraftStore(Database(source / "jarvis.db"), source)
    draft = drafts.create_draft("email", "", "Betreff", "Inhalt", [], "", "")
    original_export = drafts.export_draft(draft["id"], "eml", draft["version"])
    archive = tmp_path / "backup.zip"
    create_backup(source, archive)
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    restored_drafts = DraftStore(Database(restored / "jarvis.db"), restored)
    saved = restored_drafts.get_draft(draft["id"])
    expected = restored / "outputs" / f"{draft['id']}.eml"
    assert saved["exports"][0]["path"] == str(expected)
    assert saved["exports"][0]["path"] != original_export["path"]
    assert expected.is_file()
    assert restored_drafts.export_draft(draft["id"], "eml", draft["version"])["path"] == str(expected)


def test_backup_rejects_export_changed_after_saving(tmp_path):
    source = tmp_path / "source"
    drafts = DraftStore(Database(source / "jarvis.db"), source)
    draft = drafts.create_draft("text", "", "Probe", "Original", [], "", "")
    exported = drafts.export_draft(draft["id"], "md", draft["version"])
    with open(exported["path"], "w", encoding="utf-8") as stream:
        stream.write("Außerhalb geändert")
    archive = tmp_path / "backup.zip"
    with pytest.raises(ValueError, match="Exportdatei fehlt oder weicht"):
        create_backup(source, archive)
    assert not archive.exists()


def test_backup_rejects_oversized_note_before_copying_it(tmp_path, monkeypatch):
    source = tmp_path / "source"
    Database(source / "jarvis.db")
    note = source / "notes" / "large.txt"
    note.parent.mkdir()
    note.write_text("X" * 200, encoding="utf-8")
    monkeypatch.setattr("jarvis.backup.MAX_ARCHIVE_BYTES",
                        (source / "jarvis.db").stat().st_size + 32)
    original_open = builtins.open

    def deny_note_read(path, *args, **kwargs):
        if str(path) == str(note):
            raise AssertionError("Übergroße Datei wurde vor der Größenprüfung geöffnet")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", deny_note_read)
    archive = tmp_path / "backup.zip"
    with pytest.raises(ValueError, match="2 GiB"):
        create_backup(source, archive)
    assert not archive.exists()


def test_backup_copy_stops_if_source_grows_after_size_check(tmp_path, monkeypatch):
    source = tmp_path / "growing.txt"
    source.write_bytes(b"start")
    target = tmp_path / "snapshot.txt"
    original_open = type(source).open
    grown = False

    def grow_before_read(path, *args, **kwargs):
        nonlocal grown
        if path == source and args and args[0] == "rb" and not grown:
            grown = True
            with original_open(source, "ab") as stream:
                stream.write(b"X" * 200)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(type(source), "open", grow_before_read)
    with pytest.raises(ValueError, match="2 GiB"):
        copy_bounded(source, target, remaining=100)
    assert grown


def test_backup_rejects_file_count_before_copying_extra_note(tmp_path, monkeypatch):
    source = tmp_path / "source"
    Database(source / "jarvis.db")
    note = source / "notes" / "extra.txt"
    note.parent.mkdir()
    note.write_text("Inhalt", encoding="utf-8")
    monkeypatch.setattr("jarvis.backup.MAX_FILE_COUNT", 1)
    original_open = builtins.open

    def deny_note_read(path, *args, **kwargs):
        if str(path) == str(note):
            raise AssertionError("Zusätzliche Datei wurde trotz Anzahlgrenze gelesen")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", deny_note_read)
    with pytest.raises(ValueError, match="zu viele Dateien"):
        create_backup(source, tmp_path / "backup.zip")


def test_restore_detects_tampered_content(tmp_path):
    source = tmp_path / "source"
    Database(source / "jarvis.db")
    archive = tmp_path / "backup.zip"
    create_backup(source, archive)
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive) as original, zipfile.ZipFile(tampered, "w") as changed:
        for name in original.namelist():
            data = original.read(name)
            changed.writestr(name, b"bad" if name == "jarvis.db" else data)
    with pytest.raises(ValueError, match="Dateigröße|Datei im Backup|Prüfsumme"):
        restore_backup(tampered, tmp_path / "restored")


def test_restore_rejects_duplicate_archive_member(tmp_path):
    source = tmp_path / "source"
    Database(source / "jarvis.db")
    original_path = tmp_path / "original.zip"
    create_backup(source, original_path)
    duplicate_path = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(original_path) as original, zipfile.ZipFile(duplicate_path, "w") as duplicate:
        for name in original.namelist():
            duplicate.writestr(name, original.read(name))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            duplicate.writestr("jarvis.db", original.read("jarvis.db"))
    with pytest.raises(ValueError, match="Dateiliste"):
        restore_backup(duplicate_path, tmp_path / "restored")


@pytest.mark.parametrize("first,second", [
    ("notes/A.txt", "notes/a.txt"),
    ("notes/nested", "notes/nested/file.txt"),
])
def test_restore_rejects_colliding_target_paths(tmp_path, first, second):
    source = tmp_path / "source"
    Database(source / "jarvis.db")
    original_path = tmp_path / "original.zip"
    create_backup(source, original_path)
    malformed_path = tmp_path / "colliding.zip"
    payload = b"fixture"
    with zipfile.ZipFile(original_path) as original, zipfile.ZipFile(malformed_path, "w") as malformed:
        manifest = json.loads(original.read("manifest.json"))
        for name in (first, second):
            manifest["files"][name] = {"size": len(payload),
                                       "sha256": hashlib.sha256(payload).hexdigest()}
        malformed.writestr("manifest.json", json.dumps(manifest))
        malformed.writestr("jarvis.db", original.read("jarvis.db"))
        for name in (first, second):
            malformed.writestr(name, payload)

    with pytest.raises(ValueError, match="kollidierende|Konflikte"):
        restore_backup(malformed_path, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_backup_rejects_symlinked_output_root(tmp_path):
    source = tmp_path / "source"
    Database(source / "jarvis.db")
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (source / "outputs").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Windows-Symlinks sind nicht verfügbar")
    with pytest.raises(ValueError, match="Link|Junction"):
        create_backup(source, tmp_path / "backup.zip")


def test_backup_rejects_nested_junction_before_reading_external_file(tmp_path, monkeypatch):
    source = tmp_path / "source"
    Database(source / "jarvis.db")
    nested = source / "notes" / "linked"
    nested.mkdir(parents=True)
    (nested / "outside.txt").write_text("Nicht sichern", encoding="utf-8")
    original = type(nested).is_junction
    monkeypatch.setattr(type(nested), "is_junction",
                        lambda path: path == nested or original(path))
    archive = tmp_path / "backup.zip"
    with pytest.raises(ValueError, match="Unterordner.*Junction"):
        create_backup(source, archive)
    assert not archive.exists()


def test_backup_bounds_nested_directory_walk(tmp_path, monkeypatch):
    source = tmp_path / "source"
    Database(source / "jarvis.db")
    (source / "notes" / "nested").mkdir(parents=True)
    monkeypatch.setattr("jarvis.backup.MAX_DIRECTORY_COUNT", 1)
    archive = tmp_path / "backup.zip"
    with pytest.raises(ValueError, match="zu viele Verzeichnisse"):
        create_backup(source, archive)
    assert not archive.exists()


def test_backup_archives_output_snapshot_even_if_original_changes(tmp_path, monkeypatch):
    source = tmp_path / "source"
    Database(source / "jarvis.db")
    output = source / "outputs" / "draft.md"
    output.parent.mkdir()
    output.write_text("erste Version", encoding="utf-8")
    original_write = zipfile.ZipFile.write

    def change_original_during_archive(self, filename, arcname=None, *args, **kwargs):
        if arcname == "outputs/draft.md":
            output.write_text("zweite Version", encoding="utf-8")
        return original_write(self, filename, arcname, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", change_original_during_archive)
    archive = tmp_path / "backup.zip"
    create_backup(source, archive)
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    assert (restored / "outputs" / "draft.md").read_text(encoding="utf-8") == "erste Version"
    assert output.read_text(encoding="utf-8") == "zweite Version"


@pytest.mark.parametrize("change", [
    lambda manifest: manifest.update(schema=True),
    lambda manifest: manifest.update(files=[]),
    lambda manifest: manifest["files"]["jarvis.db"].update(size="large"),
    lambda manifest: manifest["files"]["jarvis.db"].update(sha256="invalid"),
])
def test_restore_rejects_invalid_manifest_metadata(tmp_path, change):
    source = tmp_path / "source"
    Database(source / "jarvis.db")
    original = tmp_path / "original.zip"
    create_backup(source, original)
    malformed = tmp_path / "malformed.zip"
    rewrite_manifest(original, malformed, change)
    with pytest.raises(ValueError, match="Schema|Dateiliste|Dateimetadaten"):
        restore_backup(malformed, tmp_path / "restored")


def test_restore_rejects_size_mismatch_before_extraction(tmp_path):
    source = tmp_path / "source"
    Database(source / "jarvis.db")
    original = tmp_path / "original.zip"
    create_backup(source, original)
    malformed = tmp_path / "malformed.zip"
    rewrite_manifest(original, malformed,
                     lambda manifest: manifest["files"]["jarvis.db"].update(size=1))
    with pytest.raises(ValueError, match="Dateigröße"):
        restore_backup(malformed, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_restore_creates_missing_target_parent(tmp_path):
    source = tmp_path / "source"
    Database(source / "jarvis.db")
    archive = tmp_path / "backup.zip"
    create_backup(source, archive)
    nested = tmp_path / "nested" / "restored"
    restore_backup(archive, nested)
    assert sqlite3.connect(nested / "jarvis.db").execute("PRAGMA user_version").fetchone()[0] == 63


def test_restore_rejects_corrupt_checkpoint_even_with_matching_hash(tmp_path):
    source = tmp_path / "source"
    Database(source / "jarvis.db")
    with sqlite3.connect(source / "checkpoints.db") as db:
        db.execute("CREATE TABLE marker (value TEXT)")
    original_path = tmp_path / "original.zip"
    create_backup(source, original_path)
    corrupt = b"not a sqlite database"
    altered_path = tmp_path / "altered.zip"
    with zipfile.ZipFile(original_path) as original, zipfile.ZipFile(altered_path, "w") as altered:
        manifest = json.loads(original.read("manifest.json"))
        manifest["files"]["checkpoints.db"] = {
            "size": len(corrupt), "sha256": hashlib.sha256(corrupt).hexdigest(),
        }
        for name in original.namelist():
            payload = (json.dumps(manifest).encode("utf-8") if name == "manifest.json"
                       else corrupt if name == "checkpoints.db" else original.read(name))
            altered.writestr(name, payload)
    with pytest.raises(ValueError, match="Backup-Datenbank ist nicht intakt"):
        restore_backup(altered_path, tmp_path / "restored")


def test_backup_rejects_corrupt_source_database(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "jarvis.db").write_bytes(b"not a sqlite database")
    archive = tmp_path / "backup.zip"
    with pytest.raises(ValueError, match="nicht intakt"):
        create_backup(source, archive)
    assert not archive.exists()


def test_restored_older_schema_migrates_on_start(tmp_path):
    source = tmp_path / "source"
    Database(source / "jarvis.db")
    with sqlite3.connect(source / "jarvis.db") as db:
        db.execute("ALTER TABLE messages DROP COLUMN skill_hash")
        db.execute("ALTER TABLE messages DROP COLUMN skill_version")
        db.execute("ALTER TABLE messages DROP COLUMN skill_id")
        db.execute("DROP TABLE skill_activations")
        db.execute("DROP TABLE skill_versions")
        db.execute("DROP TABLE skills")
        db.execute("DROP TABLE plan_evaluations")
        db.execute("DROP INDEX plans_usage_id")
        db.execute("ALTER TABLE plans DROP COLUMN usage_id")
        db.execute("DROP TABLE plan_rule_activations")
        db.execute("DROP TABLE plan_rule_state")
        db.execute("ALTER TABLE plans DROP COLUMN rule_version")
        db.execute("DROP TABLE plan_rule_versions")
        db.execute("DROP TABLE draft_rule_activations")
        db.execute("DROP TABLE draft_rule_state")
        db.execute("ALTER TABLE drafts DROP COLUMN rule_version")
        db.execute("DROP TABLE draft_rule_versions")
        db.execute("DROP TABLE chat_answer_evaluations")
        db.execute("DROP INDEX messages_usage_id")
        db.execute("ALTER TABLE messages DROP COLUMN usage_id")
        db.execute("DROP TABLE chat_rule_activations")
        db.execute("DROP TABLE chat_rule_state")
        db.execute("ALTER TABLE messages DROP COLUMN rule_version")
        db.execute("DROP TABLE chat_rule_versions")
        db.execute("DROP TABLE run_evaluations")
        db.execute("DROP TABLE project_reviews")
        db.execute("DROP TABLE project_review_schedules")
        db.execute("DROP TABLE project_actions")
        db.execute("DROP TABLE research_evaluations")
        db.execute("DROP TABLE research_reports")
        db.execute("DROP INDEX notifications_focus_deferred")
        db.execute("ALTER TABLE notifications DROP COLUMN focus_deferred_at")
        db.execute("DROP TABLE focus_settings")
        db.execute("DROP TABLE extension_captures")
        db.execute("DROP TABLE extension_pairs")
        db.execute("ALTER TABLE tasks DROP COLUMN source_url")
        db.execute("DROP INDEX notifications_followup_due")
        db.execute("DROP INDEX notifications_snoozed_due")
        for column in ("acknowledged_at", "snoozed_until", "followup_due_utc",
                       "followup_count", "last_delivered_at"):
            db.execute(f"ALTER TABLE notifications DROP COLUMN {column}")
        db.execute("ALTER TABLE tasks DROP COLUMN priority")
        db.execute("DROP TABLE evening_plans")
        db.execute("DROP TABLE time_exceptions")
        db.execute("DROP TABLE time_rules")
        db.execute("DROP TABLE work_states")
        db.execute("DROP TABLE markdown_revisions")
        db.execute("DROP INDEX plans_status_time")
        db.execute("DROP TABLE plans")
        db.execute("DROP INDEX chats_archive_updated")
        db.execute("ALTER TABLE chats DROP COLUMN archived_at")
        db.execute("DROP TABLE style_profile_revisions")
        db.execute("DROP TABLE chat_compactions")
        db.execute("DROP TABLE draft_named_style_usage")
        db.execute("DROP TABLE named_style_profile_revisions")
        db.execute("DROP TABLE named_style_profiles")
        db.execute("DROP TABLE project_memory_links")
        db.execute("DROP TABLE project_person_merges")
        db.execute("DROP TABLE project_person_aliases")
        db.execute("DROP TABLE project_person_tasks")
        db.execute("DROP TABLE project_people")
        db.execute("ALTER TABLE messages DROP COLUMN context_latest_omitted_id")
        db.execute("ALTER TABLE messages DROP COLUMN context_omitted_count")
        db.execute("ALTER TABLE messages DROP COLUMN context_token_budget")
        db.execute("ALTER TABLE messages DROP COLUMN context_estimated_tokens")
        db.execute("ALTER TABLE messages DROP COLUMN compaction_version")
        db.execute("DROP TABLE learning_sessions")
        db.execute("DROP TABLE memory_proposal_events")
        db.execute("DROP TABLE memory_proposals")
        db.execute("DROP TABLE memory_context_uses")
        db.execute("PRAGMA user_version=9")
    archive = tmp_path / "backup.zip"
    assert create_backup(source, archive)["schema"] == 9
    restored = tmp_path / "restored"
    assert restore_backup(archive, restored)["schema"] == 9
    migrated = Database(restored / "jarvis.db")
    with migrated.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 63
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='plans'").fetchone()


def test_backup_refuses_active_profile(tmp_path):
    source = tmp_path / "source"
    Database(source / "jarvis.db")
    lock = InstanceLock(source / "jarvis.lock")
    lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="bereits von einem Prozess verwendet"):
            create_backup(source, tmp_path / "backup.zip")
    finally:
        lock.release()


@pytest.mark.parametrize("name", [
    "../escape.txt", "outputs/../escape.txt", "outputs/./note.txt", "outputs",
    *(["outputs/CON.txt", "outputs/foo:bar"] if os.name == "nt" else []),
])
def test_restore_rejects_unsafe_archive_member_names(tmp_path, name):
    source = tmp_path / "source"
    Database(source / "jarvis.db")
    original_path = tmp_path / "original.zip"
    create_backup(source, original_path)
    malformed_path = tmp_path / "malformed.zip"
    payload = b"untrusted"
    with zipfile.ZipFile(original_path) as original, zipfile.ZipFile(malformed_path, "w") as malformed:
        manifest = json.loads(original.read("manifest.json"))
        manifest["files"][name] = {"size": len(payload),
                                   "sha256": hashlib.sha256(payload).hexdigest()}
        malformed.writestr("manifest.json", json.dumps(manifest))
        malformed.writestr("jarvis.db", original.read("jarvis.db"))
        malformed.writestr(name, payload)
    target = tmp_path / "restored"
    with pytest.raises(ValueError, match="Unsicherer"):
        restore_backup(malformed_path, target)
    assert not target.exists()
    assert not (tmp_path / "escape.txt").exists()
