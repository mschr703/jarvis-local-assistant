"""Tägliche verschlüsselte Sicherung der laufenden Windows-Instanz."""

import base64
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jarvis.encrypted_backup import (
    MAGIC,
    MAX_CIPHERTEXT_BYTES,
    create_encrypted_backup,
    restore_encrypted_backup,
)
from jarvis.instance import InstanceLock
from jarvis.windows_secret import protect_secret, unprotect_secret

ARCHIVE_NAME = re.compile(r"jarvis-auto-[0-9]{8}T[0-9]{6}-[0-9a-f]{8}\.jbackup")
CONFIG_NAME = "backup_schedule.json"


def _save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.is_junction():
        raise ValueError("Backup-Konfiguration darf kein Link sein")
    pending = path.with_name(path.name + "." + uuid4().hex + ".partial")
    try:
        with pending.open("x", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        # Windows can reject replacement while a concurrent status read has the
        # existing file open. Keep the atomic rename and retry this short race.
        for attempt in range(20):
            try:
                pending.replace(path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        pending.unlink(missing_ok=True)


class ScheduledBackup:
    def __init__(self, data_dir: Path, owned_lock: InstanceLock | None = None):
        self.data_dir = Path(data_dir).resolve()
        self.path = self.data_dir / CONFIG_NAME
        self.owned_lock = owned_lock
        self._tick_lock = threading.Lock()
        self._closing = False

    def close(self) -> None:
        self._closing = True
        with self._tick_lock:
            pass

    def _load(self) -> dict | None:
        if self.path.is_symlink() or self.path.is_junction():
            raise ValueError("Backup-Konfiguration darf kein Link sein")
        if not self.path.exists():
            return None
        try:
            if self.path.stat().st_size > 16 * 1024:
                raise ValueError("Backup-Konfiguration ist zu groß")
            config = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("Backup-Konfiguration ist nicht lesbar") from exc
        if (not isinstance(config, dict) or type(config.get("version")) is not int
                or config["version"] != 1 or type(config.get("enabled")) is not bool
                or not isinstance(config.get("directory"), str)
                or not Path(config["directory"]).is_absolute()
                or not isinstance(config.get("time_local"), str)
                or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", config["time_local"])
                or not isinstance(config.get("timezone"), str)
                or type(config.get("keep")) is not int or not 1 <= config["keep"] <= 30
                or not isinstance(config.get("secret_b64"), str)
                or len(config["secret_b64"]) > 16 * 1024):
            raise ValueError("Backup-Konfiguration ist ungültig")
        try:
            ZoneInfo(config["timezone"])
            base64.b64decode(config["secret_b64"], validate=True)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Backup-Konfiguration ist ungültig") from exc
        return config

    def configure(self, directory: Path, time_local: str, timezone: str,
                  keep: int, passphrase: str, *, now: datetime | None = None) -> dict:
        if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", time_local):
            raise ValueError("Backup-Uhrzeit im Format HH:MM angeben")
        try:
            zone = ZoneInfo(timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Unbekannte Backup-Zeitzone") from exc
        if type(keep) is not int or not 1 <= keep <= 30:
            raise ValueError("Aufbewahrung muss 1 bis 30 Backups umfassen")
        if not isinstance(passphrase, str) or len(passphrase) < 12:
            raise ValueError("Backup-Passwort benötigt mindestens 12 Zeichen")
        directory = Path(directory).resolve()
        if directory.is_relative_to(self.data_dir):
            raise ValueError("Backup-Ziel muss außerhalb des Jarvis-Datenordners liegen")
        if directory.is_symlink() or directory.is_junction():
            raise ValueError("Backup-Ziel darf kein Link oder Junction sein")
        directory.mkdir(parents=True, exist_ok=True)
        if not directory.is_dir():
            raise ValueError("Backup-Ziel ist kein Ordner")
        current = (now or datetime.now(UTC)).astimezone(zone)
        config = {"version": 1, "enabled": True, "directory": str(directory),
                  "time_local": time_local, "timezone": timezone, "keep": keep,
                  "secret_b64": base64.b64encode(protect_secret(passphrase)).decode("ascii"),
                  "last_attempt_date": (current.date().isoformat()
                                        if current.strftime("%H:%M") >= time_local else None),
                  "last_status": "configured", "last_error": None, "last_success_at": None,
                  "last_archive": None, "last_archive_sha256": None}
        if self.owned_lock is not None:
            raise RuntimeError("Backup-Planung nur bei gestopptem Jarvis ändern")
        lock = InstanceLock(self.data_dir / "jarvis.lock")
        lock.acquire()
        try:
            _save(self.path, config)
        finally:
            lock.release()
        return self.status()

    def disable(self) -> dict:
        if self.owned_lock is not None:
            raise RuntimeError("Backup-Planung nur bei gestopptem Jarvis ändern")
        lock = InstanceLock(self.data_dir / "jarvis.lock")
        lock.acquire()
        try:
            config = self._load()
            if config is None:
                raise ValueError("Keine Backup-Planung eingerichtet")
            config["enabled"] = False
            _save(self.path, config)
        finally:
            lock.release()
        return self.status()

    def status(self) -> dict:
        try:
            config = self._load()
        except ValueError as exc:
            return {"configured": True, "enabled": False, "last_status": "failed",
                    "last_error": str(exc)}
        if config is None:
            return {"configured": False, "enabled": False, "last_status": "not_configured",
                    "last_error": None}
        public = ("enabled", "directory", "time_local", "timezone", "keep",
                  "last_attempt_date", "last_status", "last_error", "last_success_at",
                  "last_archive", "last_archive_sha256")
        return {key: config.get(key) for key in public} | {"configured": True}

    def open_verified_archive(self):
        """Öffnet ausschließlich das zuletzt geprüfte, unveränderte Auto-Archiv."""
        config = self._load()
        if config is None:
            raise ValueError("Keine Backup-Planung eingerichtet")
        original = config.get("last_archive")
        expected_hash = config.get("last_archive_sha256")
        if (not isinstance(original, str) or not isinstance(expected_hash, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)):
            raise ValueError("Noch kein herunterladbares geprüftes Archiv; Sicherung erneut starten")
        directory = self._validate_destination(config)
        archive = Path(original)
        if (not ARCHIVE_NAME.fullmatch(archive.name) or archive.parent != directory
                or archive.is_symlink() or archive.is_junction()
                or archive.resolve() != directory / archive.name):
            raise ValueError("Backup-Archivpfad ist ungültig")
        try:
            handle = archive.open("rb")
        except OSError as exc:
            raise ValueError("Geprüftes Backup-Archiv fehlt") from exc
        try:
            opened = os.fstat(handle.fileno())
            current = archive.stat()
            if (not stat.S_ISREG(opened.st_mode) or opened.st_ino != current.st_ino
                    or opened.st_dev != current.st_dev or archive.is_symlink()
                    or archive.is_junction() or archive.resolve() != directory / archive.name):
                raise ValueError("Backup-Archiv wurde ersetzt")
            if not len(MAGIC) < opened.st_size <= MAX_CIPHERTEXT_BYTES:
                raise ValueError("Backup-Archivgröße ist ungültig")
            digest = hashlib.sha256()
            if handle.read(len(MAGIC)) != MAGIC:
                raise ValueError("Backup-Archivformat ist ungültig")
            handle.seek(0)
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
            if digest.hexdigest() != expected_hash:
                raise ValueError("Backup-Archiv wurde seit der Prüfung verändert")
            handle.seek(0)
            return handle, archive.name, opened.st_size
        except OSError as exc:
            handle.close()
            raise ValueError("Geprüftes Backup-Archiv ist nicht lesbar") from exc
        except BaseException:
            handle.close()
            raise

    def _validate_destination(self, config: dict) -> Path:
        directory = Path(config["directory"])
        if (directory.is_symlink() or directory.is_junction()
                or directory.resolve().is_relative_to(self.data_dir)):
            raise ValueError("Backup-Ziel ist unsicher")
        if not directory.is_dir():
            raise ValueError("Backup-Zielordner fehlt")
        return directory.resolve()

    def _rotate(self, directory: Path, keep: int, current: Path) -> None:
        archives = []
        for path in directory.iterdir():
            if (ARCHIVE_NAME.fullmatch(path.name) and path.is_file()
                    and not path.is_symlink() and path.resolve().parent == directory):
                archives.append(path)
        others = sorted((path for path in archives if path != current),
                        key=lambda path: path.name, reverse=True)
        keep_names = {current, *others[:keep - 1]}
        for old in archives:
            if old in keep_names:
                continue
            old.unlink()

    def tick(self, *, now: datetime | None = None, force: bool = False) -> dict:
        if not self._tick_lock.acquire(blocking=False):
            return self.status()
        try:
            if self._closing:
                return self.status()
            return self._tick(now=now, force=force)
        finally:
            self._tick_lock.release()

    def _tick(self, *, now: datetime | None = None, force: bool = False) -> dict:
        config = self._load()
        if config is None or not config.get("enabled"):
            return self.status()
        try:
            zone = ZoneInfo(config["timezone"])
            current = (now or datetime.now(UTC)).astimezone(zone)
            if config.get("last_status") == "running":
                config["last_status"] = "failed"
                config["last_error"] = "Vorheriger Backup-Lauf wurde unterbrochen"
                config["last_attempt_date"] = None
                _save(self.path, config)
            if not force and (config.get("last_attempt_date") == current.date().isoformat()
                              or current.strftime("%H:%M") < config["time_local"]):
                return self.status()
            directory = self._validate_destination(config)
            if self.owned_lock is None:
                raise RuntimeError("Geplanter Backup-Worker läuft nicht in Jarvis")
            secret = unprotect_secret(base64.b64decode(config["secret_b64"], validate=True))
            config["last_attempt_date"] = current.date().isoformat()
            config["last_status"] = "running"
            config["last_error"] = None
            _save(self.path, config)
            filename = f"jarvis-auto-{current:%Y%m%dT%H%M%S}-{uuid4().hex[:8]}.jbackup"
            archive = directory / filename
            create_encrypted_backup(self.data_dir, archive, secret, owned_lock=self.owned_lock)
            with tempfile.TemporaryDirectory(prefix="jarvis-auto-verify-") as temporary:
                restore_encrypted_backup(archive, Path(temporary) / "restored", secret)
            digest = hashlib.sha256()
            with archive.open("rb") as verified:
                for block in iter(lambda: verified.read(1024 * 1024), b""):
                    digest.update(block)
            self._rotate(directory, config["keep"], archive)
            config["last_status"] = "succeeded"
            config["last_error"] = None
            config["last_success_at"] = datetime.now(UTC).isoformat()
            config["last_archive"] = str(archive)
            config["last_archive_sha256"] = digest.hexdigest()
        except Exception as exc:  # noqa: BLE001 - Fehler im Status sichtbar halten.
            config["last_status"] = "failed"
            config["last_error"] = str(exc)[:300]
        _save(self.path, config)
        return self.status()
