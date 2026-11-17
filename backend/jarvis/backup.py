"""Prüfbares Offline-Backup der lokalen Jarvis-Daten ohne Schlüssel und Browserprofil."""

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from jarvis.instance import InstanceLock

MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_FILE_COUNT = 10_000
MAX_DIRECTORY_COUNT = 2_000
SUPPORTED_SCHEMA = 52


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_bounded(source: Path, target: Path, remaining: int) -> int:
    before = source.stat()
    if before.st_size > remaining:
        raise ValueError("Backup überschreitet 2 GiB")
    copied = 0
    with source.open("rb") as original, target.open("xb") as snapshot:
        for block in iter(lambda: original.read(1024 * 1024), b""):
            copied += len(block)
            if copied > remaining:
                raise ValueError("Backup überschreitet 2 GiB")
            snapshot.write(block)
    after = source.stat()
    if ((before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
            or sha256(source) != sha256(target)):
        raise ValueError("Datei wurde während des Backups geändert")
    return copied


def raise_walk_error(error: OSError) -> None:
    raise ValueError("Backup-Ordner kann nicht vollständig gelesen werden") from error


def verified_exports(database_path: Path, stage: Path, schema: int) -> list[tuple[str, str, str]]:
    """Prüft DB-Exportbelege gegen die Dateien in der Backup-Momentaufnahme."""
    if schema < 6:
        return []
    try:
        with closing(sqlite3.connect(database_path)) as db:
            rows = db.execute("SELECT draft_id,format,path,sha256 FROM draft_exports").fetchall()
    except sqlite3.DatabaseError as exc:
        raise ValueError("Exportbelege in der Datenbank sind nicht lesbar") from exc
    verified = []
    for draft_id, format_name, stored_path, expected_hash in rows:
        try:
            if str(UUID(draft_id)) != draft_id or format_name not in {"txt", "md", "eml"}:
                raise ValueError("Ungültiger Exportbeleg")
        except (TypeError, ValueError) as exc:
            raise ValueError("Ungültiger Exportbeleg") from exc
        filename = f"{draft_id}.{format_name}"
        if not isinstance(stored_path, str):
            raise ValueError("Ungültiger Exportpfad")  # noqa: TRY004 - beschädigte Archivdaten
        path = Path(stored_path)
        if path.name != filename or path.parent.name != "outputs":
            raise ValueError("Exportpfad passt nicht zum Entwurf")
        relative = f"outputs/{filename}"
        staged_file = stage / relative
        if not staged_file.is_file() or sha256(staged_file) != expected_hash:
            raise ValueError("Exportdatei fehlt oder weicht vom Entwurf ab")
        verified.append((draft_id, format_name, relative))
    return verified


def create_backup(data_dir: Path, destination: Path,
                  *, owned_lock: InstanceLock | None = None) -> dict:
    data_dir, destination = Path(data_dir).resolve(), Path(destination).resolve()
    if destination.is_relative_to(data_dir):
        raise ValueError("Backup muss außerhalb des Jarvis-Datenordners liegen")
    if destination.exists():
        raise ValueError("Backup-Datei existiert bereits")
    source_db = data_dir / "jarvis.db"
    if not source_db.is_file():
        raise ValueError("Jarvis-Datenbank nicht gefunden")
    lock = owned_lock or InstanceLock(data_dir / "jarvis.lock")
    if owned_lock is None:
        lock.acquire()
    elif lock.handle is None or lock.path.resolve() != data_dir / "jarvis.lock":
        raise RuntimeError("Laufende Jarvis-Instanz besitzt die Backup-Sperre nicht")
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis-backup-") as temporary:
            stage = Path(temporary)
            files: list[tuple[Path, str]] = []
            staged_bytes = 0
            for filename in ("jarvis.db", "checkpoints.db"):
                source = data_dir / filename
                if source.is_symlink():
                    raise ValueError("Datenbankdatei darf kein Symlink sein")
                if not source.is_file():
                    continue
                target = stage / filename
                try:
                    with closing(sqlite3.connect(source)) as original, closing(sqlite3.connect(target)) as snapshot:
                        original.backup(snapshot)
                        if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise ValueError(f"Datenbankdatei {filename} ist nicht intakt")
                except sqlite3.DatabaseError as exc:
                    raise ValueError(f"Datenbankdatei {filename} ist nicht intakt") from exc
                staged_bytes += target.stat().st_size
                if staged_bytes > MAX_ARCHIVE_BYTES:
                    raise ValueError("Backup überschreitet 2 GiB")
                files.append((target, filename))
            with closing(sqlite3.connect(stage / "jarvis.db")) as db:
                schema = db.execute("PRAGMA user_version").fetchone()[0]
            if schema > SUPPORTED_SCHEMA:
                raise ValueError("Datenbank-Schema ist neuer als diese Jarvis-Version")
            visited_directories = 0
            for dirname in ("outputs", "notes", "browser-screenshots", "cad-models"):
                folder = data_dir / dirname
                if folder.is_symlink() or folder.is_junction():
                    raise ValueError("Ausgabeordner darf kein Link oder Junction sein")
                if folder.is_dir():
                    folder_root = folder.resolve()
                    for current, directories, filenames in os.walk(
                            folder, topdown=True, followlinks=False, onerror=raise_walk_error):
                        visited_directories += 1
                        if visited_directories > MAX_DIRECTORY_COUNT:
                            raise ValueError("Backup enthält zu viele Verzeichnisse")
                        current_path = Path(current)
                        if not current_path.resolve().is_relative_to(folder_root):
                            raise ValueError("Backup-Ordner verlässt den Datenordner")
                        for dirname in directories:
                            subfolder = current_path / dirname
                            if subfolder.is_symlink() or subfolder.is_junction():
                                raise ValueError("Backup-Unterordner darf kein Link oder Junction sein")
                        for filename in filenames:
                            source = current_path / filename
                            if source.is_symlink() or source.is_junction():
                                raise ValueError("Backup-Datei darf kein Link oder Junction sein")
                            if not source.is_file():
                                continue
                            if not source.resolve().is_relative_to(folder_root):
                                raise ValueError("Backup-Datei verlässt den Datenordner")
                            name = source.relative_to(data_dir).as_posix()
                            if len(files) >= MAX_FILE_COUNT:
                                raise ValueError("Backup enthält zu viele Dateien")
                            staged = stage / name
                            staged.parent.mkdir(parents=True, exist_ok=True)
                            staged_bytes += copy_bounded(source, staged,
                                                         MAX_ARCHIVE_BYTES - staged_bytes)
                            files.append((staged, name))
            verified_exports(stage / "jarvis.db", stage, schema)
            if len(files) > MAX_FILE_COUNT:
                raise ValueError("Backup enthält zu viele Dateien")
            manifest = {"format": "jarvis-backup-v1", "schema": schema,
                        "created_at": datetime.now(UTC).isoformat(),
                        "files": {name: {"sha256": sha256(path), "size": path.stat().st_size}
                                  for path, name in files}}
            if sum(item["size"] for item in manifest["files"].values()) > MAX_ARCHIVE_BYTES:
                raise ValueError("Backup überschreitet 2 GiB")
            destination.parent.mkdir(parents=True, exist_ok=True)
            pending = destination.with_name(destination.name + ".partial")
            if pending.exists():
                raise ValueError("Temporäre Backup-Datei existiert bereits")
            try:
                with zipfile.ZipFile(pending, "x", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                    for path, name in files:
                        archive.write(path, name)
                pending.rename(destination)
            except BaseException:
                pending.unlink(missing_ok=True)
                raise
            return {"path": str(destination), "schema": schema,
                    "file_count": len(files), "size": destination.stat().st_size}
    finally:
        if owned_lock is None:
            lock.release()


def restore_backup(archive_path: Path, target_dir: Path) -> dict:
    archive_path, target_dir = Path(archive_path).resolve(), Path(target_dir).resolve()
    if target_dir.exists():
        raise ValueError("Wiederherstellung benötigt einen neuen, noch nicht existierenden Zielordner")
    with zipfile.ZipFile(archive_path) as archive:
        try:
            manifest_info = archive.getinfo("manifest.json")
        except KeyError as exc:
            raise ValueError("Backup-Manifest fehlt") from exc
        if manifest_info.file_size > 1024 * 1024:
            raise ValueError("Backup-Manifest ist zu groß")
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
            raise ValueError("Backup-Manifest ist ungültig") from exc
        if not isinstance(manifest, dict) or manifest.get("format") != "jarvis-backup-v1":
            raise ValueError("Backup-Format oder Datenbank-Schema nicht unterstützt")
        schema = manifest.get("schema")
        files = manifest.get("files")
        if type(schema) is not int or not 1 <= schema <= SUPPORTED_SCHEMA:
            raise ValueError("Backup-Format oder Datenbank-Schema nicht unterstützt")
        if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILE_COUNT:
            raise ValueError("Backup-Dateiliste ist ungültig")
        for name, meta in files.items():
            if (not isinstance(name, str) or not isinstance(meta, dict)
                    or type(meta.get("size")) is not int or meta["size"] < 0
                    or not isinstance(meta.get("sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", meta["sha256"])):
                raise ValueError("Backup-Dateimetadaten sind ungültig")
        names = set(files)
        archive_names = archive.namelist()
        if ("jarvis.db" not in names or set(archive_names) != names | {"manifest.json"}
                or len(archive_names) != len(set(archive_names))):
            raise ValueError("Backup-Dateiliste stimmt nicht mit dem Manifest überein")
        folded_names = {name.casefold() for name in names}
        if len(folded_names) != len(names):
            raise ValueError("Backup-Dateiliste enthält kollidierende Windows-Pfade")
        for name in names:
            parts = name.split("/")
            if any("/".join(parts[:index]).casefold() in folded_names
                   for index in range(1, len(parts))):
                raise ValueError("Backup-Dateiliste enthält Datei-Unterordner-Konflikte")
        if sum(item["size"] for item in files.values()) > MAX_ARCHIVE_BYTES:
            raise ValueError("Backup überschreitet 2 GiB")
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="jarvis-restore-", dir=target_dir.parent) as temporary:
            stage = Path(temporary) / "data"
            stage.mkdir()
            for name, meta in files.items():
                path = Path(name)
                if (not path.parts or path.is_absolute() or ".." in path.parts
                        or name != path.as_posix()
                        or (os.name == "nt" and os.path.isreserved(name))
                        or path.parts[0] not in {"jarvis.db", "checkpoints.db", "outputs", "notes",
                                                 "browser-screenshots", "cad-models"}):
                    raise ValueError("Unsicherer Dateiname im Backup")
                if path.parts[0] not in {"outputs", "notes", "browser-screenshots", "cad-models"} and len(path.parts) != 1:
                    raise ValueError("Unsicherer Datenbankpfad im Backup")
                if path.parts[0] in {"outputs", "notes", "browser-screenshots", "cad-models"} and len(path.parts) < 2:
                    raise ValueError("Unsicherer Ausgabe- oder Notizpfad im Backup")
                if archive.getinfo(name).file_size != meta["size"]:
                    raise ValueError("Dateigröße stimmt nicht mit dem Manifest überein")
                destination = stage / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source, destination.open("xb") as output:
                    total = 0
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        total += len(block)
                        if total > meta["size"]:
                            raise ValueError("Datei im Backup ist größer als angegeben")
                        output.write(block)
                if total != meta["size"] or sha256(destination) != meta["sha256"]:
                    raise ValueError("Backup-Prüfsumme stimmt nicht")
            try:
                with closing(sqlite3.connect(stage / "jarvis.db")) as db:
                    actual_schema = db.execute("PRAGMA user_version").fetchone()[0]
                    integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
                if actual_schema != manifest["schema"] or integrity != "ok":
                    raise ValueError("Backup-Datenbank ist nicht intakt")
                checkpoint = stage / "checkpoints.db"
                if checkpoint.exists():
                    with closing(sqlite3.connect(checkpoint)) as db:
                        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise ValueError("Backup-Checkpoints sind nicht intakt")
                exports = verified_exports(stage / "jarvis.db", stage, schema)
                if exports:
                    with closing(sqlite3.connect(stage / "jarvis.db")) as db:
                        db.executemany(
                            "UPDATE draft_exports SET path=? WHERE draft_id=? AND format=?",
                            ((str(target_dir / relative), draft_id, format_name)
                             for draft_id, format_name, relative in exports),
                        )
                        db.commit()
            except sqlite3.DatabaseError as exc:
                raise ValueError("Backup-Datenbank ist nicht intakt") from exc
            stage.rename(target_dir)
    return {"path": str(target_dir), "schema": manifest["schema"],
            "file_count": len(names)}
