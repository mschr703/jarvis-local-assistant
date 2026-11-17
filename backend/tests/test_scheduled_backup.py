"""Geplante Windows-Backups mit echtem Restore und begrenzter Rotation."""

import importlib.util
import json
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.instance import InstanceLock
from jarvis.scheduled_backup import ScheduledBackup, _save
from jarvis.windows_secret import protect_secret, unprotect_secret


def local(year, month, day, hour, minute, fold=0):
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("Europe/Paris"),
                    fold=fold).astimezone(UTC)


@pytest.fixture
def profile(tmp_path):
    data = tmp_path / "data"
    memory = DocumentStore(Database(data / "jarvis.db")).add_memory(
        "Atlas-Sicherung", "Zeitplan-Test", "local_only")
    return data, memory, tmp_path / "backups"


def test_schedule_config_atomic_replace_recovers_from_short_windows_lock(tmp_path, monkeypatch):
    path = tmp_path / "backup_schedule.json"
    path.write_text('{"version": 0}', encoding="utf-8")
    replace = Path.replace
    attempts = 0

    def briefly_locked(source, target):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError(5, "Access is denied", str(target))
        return replace(source, target)

    monkeypatch.setattr(Path, "replace", briefly_locked)
    _save(path, {"version": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"version": 1}
    assert attempts == 3
    assert list(tmp_path.glob("*.partial")) == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-DPAPI")
def test_dpapi_user_secret_roundtrip_and_tamper():
    password = "Testpassphrase mit vielen Wörtern"
    blob = protect_secret(password)
    assert password.encode() not in blob
    assert unprotect_secret(blob) == password
    changed = bytearray(blob)
    changed[-8] ^= 1
    with pytest.raises(RuntimeError, match="Windows-DPAPI"):
        unprotect_secret(bytes(changed))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-DPAPI")
def test_due_backup_restore_rotation_and_no_extra_dst_run(profile, tmp_path):
    data, memory, directory = profile
    schedule = ScheduledBackup(data)
    status = schedule.configure(directory, "02:30", "Europe/Paris", 2,
                                "lange geplante Testpassphrase", now=local(2027, 10, 30, 12, 0))
    assert status["configured"] and "secret_b64" not in status
    assert "lange geplante Testpassphrase" not in schedule.path.read_text(encoding="utf-8")
    (directory / "manuell.jbackup").write_bytes(b"manuell")
    lock = InstanceLock(data / "jarvis.lock")
    lock.acquire()
    try:
        running = ScheduledBackup(data, lock)
        assert running.tick(now=local(2027, 10, 31, 2, 15))["last_status"] == "configured"
        first = running.tick(now=local(2027, 10, 31, 2, 30, fold=0))
        assert first["last_status"] == "succeeded"
        assert Path(first["last_archive"]).is_file()
        assert running.tick(now=local(2027, 10, 31, 2, 30, fold=1))["last_archive"] == first["last_archive"]
        second = running.tick(now=local(2027, 11, 1, 2, 30))
        third = running.tick(now=local(2027, 11, 2, 2, 30))
        assert second["last_status"] == third["last_status"] == "succeeded"
        kept = list(directory.glob("jarvis-auto-*.jbackup"))
        assert len(kept) == 2 and Path(first["last_archive"]) not in kept
        assert (directory / "manuell.jbackup").read_bytes() == b"manuell"
        restored = tmp_path / "roundtrip"
        from jarvis.encrypted_backup import restore_encrypted_backup

        restore_encrypted_backup(Path(third["last_archive"]), restored,
                                 "lange geplante Testpassphrase")
        assert DocumentStore(Database(restored / "jarvis.db")).get_memory(memory["id"])["text"] == memory["text"]
        assert not (restored / "backup_schedule.json").exists()
    finally:
        lock.release()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-DPAPI")
def test_failure_visible_and_retry_keeps_old_backup(profile):
    data, _, directory = profile
    setup = ScheduledBackup(data)
    setup.configure(directory, "00:00", "Europe/Paris", 1,
                    "lange geplante Testpassphrase", now=local(2027, 1, 1, 12, 0))
    old = directory / "jarvis-auto-20260101T000000-abcdef12.jbackup"
    old.write_bytes(b"alte Sicherung")
    away = directory.with_name("backups-away")
    directory.rename(away)
    lock = InstanceLock(data / "jarvis.lock")
    lock.acquire()
    try:
        running = ScheduledBackup(data, lock)
        failed = running.tick(now=local(2027, 1, 2, 0, 1))
        assert failed["last_status"] == "failed" and "Zielordner" in failed["last_error"]
        assert (away / old.name).read_bytes() == b"alte Sicherung"
        away.rename(directory)
        recovered = running.tick(now=local(2027, 1, 2, 0, 2), force=True)
        assert recovered["last_status"] == "succeeded" and recovered["last_error"] is None
        assert not old.exists()
    finally:
        lock.release()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-DPAPI")
def test_api_status_csrf_run_and_restart(profile):
    data, _, directory = profile
    schedule = ScheduledBackup(data)
    schedule.configure(directory, "00:00", "Europe/Paris", 2,
                       "lange geplante Testpassphrase", now=datetime.now(UTC))
    config = json.loads(schedule.path.read_text(encoding="utf-8"))
    config["last_attempt_date"] = datetime.now(ZoneInfo("Europe/Paris")).date().isoformat()
    schedule.path.write_text(json.dumps(config), encoding="utf-8")
    settings = Settings(JARVIS_DATA_DIR=data, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        token = client.get("/session").json()["csrf"]
        assert client.post("/backups/schedule/run").status_code == 403
        assert "secret_b64" not in client.get("/backups/schedule").text
        done = client.post("/backups/schedule/run", headers={"x-jarvis-csrf": token})
        assert done.status_code == 200 and done.json()["last_status"] == "succeeded"
        saved_path = done.json()["last_archive"]
        away = directory.with_name("temporarily-away")
        directory.rename(away)
        failed = client.post("/backups/schedule/run", headers={"x-jarvis-csrf": token})
        assert failed.status_code == 200 and failed.json()["last_status"] == "failed"
        assert client.get("/health").json()["status"] == "degraded"
        away.rename(directory)
        recovered = client.post("/backups/schedule/run", headers={"x-jarvis-csrf": token})
        assert recovered.json()["last_status"] == "succeeded"
        saved_path = recovered.json()["last_archive"]
    with TestClient(create_app(settings)) as restarted:
        restarted.get("/session")
        status = restarted.get("/backups/schedule").json()
        assert status["last_archive"] == saved_path
        assert len(list(directory.glob("jarvis-auto-*.jbackup"))) == 2
    with pytest.raises(RuntimeError, match="bereits von einem Prozess"):
        lock = InstanceLock(data / "jarvis.lock")
        lock.acquire()
        try:
            schedule.disable()
        finally:
            lock.release()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-DPAPI")
def test_invalid_schedule_config_does_not_expose_secret_or_delete_files(profile):
    data, _, directory = profile
    schedule = ScheduledBackup(data)
    schedule.configure(directory, "03:00", "Europe/Paris", 2,
                       "lange geplante Testpassphrase")
    config = json.loads(schedule.path.read_text(encoding="utf-8"))
    config["keep"] = 0
    schedule.path.write_text(json.dumps(config), encoding="utf-8")
    archive = directory / "jarvis-auto-20260101T000000-abcdef12.jbackup"
    archive.write_bytes(b"keep-this")
    lock = InstanceLock(data / "jarvis.lock")
    lock.acquire()
    try:
        with pytest.raises(ValueError, match="ungültig"):
            ScheduledBackup(data, lock).tick(force=True)
        status = schedule.status()
        assert status["last_status"] == "failed" and "secret_b64" not in status
        assert archive.read_bytes() == b"keep-this"
    finally:
        lock.release()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-DPAPI")
def test_schedule_cli_configure_status_disable(profile, monkeypatch, capsys):
    data, _, directory = profile
    script = Path(__file__).resolve().parents[2] / "scripts" / "backup_schedule.py"
    spec = importlib.util.spec_from_file_location("jarvis_schedule_cli", script)
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    password = "lange CLI-Testpassphrase"
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: password)
    monkeypatch.setattr(sys, "argv", [str(script), "configure", "--data-dir", str(data),
                                      "--directory", str(directory), "--time", "04:15",
                                      "--timezone", "Europe/Paris", "--keep", "3"])
    cli.main()
    monkeypatch.setattr(sys, "argv", [str(script), "status", "--data-dir", str(data)])
    cli.main()
    monkeypatch.setattr(sys, "argv", [str(script), "disable", "--data-dir", str(data)])
    cli.main()
    output = capsys.readouterr()
    assert password not in output.out + output.err
    assert '"time_local": "04:15"' in output.out
    assert not ScheduledBackup(data).status()["enabled"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-DPAPI")
def test_app_worker_runs_due_backup_and_restart_does_not_duplicate(profile):
    data, _, directory = profile
    current = datetime.now(ZoneInfo("Europe/Paris"))
    ScheduledBackup(data).configure(directory, current.strftime("%H:%M"), "Europe/Paris", 2,
                                    "lange geplante Testpassphrase",
                                    now=(current - timedelta(minutes=1)).astimezone(UTC))
    settings = Settings(JARVIS_DATA_DIR=data, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        client.get("/session")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status = client.get("/backups/schedule").json()
            if status["last_status"] == "succeeded":
                break
            time.sleep(0.2)
        assert status["last_status"] == "succeeded", (status["last_error"], status)
    assert len(list(directory.glob("jarvis-auto-*.jbackup"))) == 1
    with TestClient(create_app(settings)) as restarted:
        restarted.get("/session")
        assert restarted.get("/backups/schedule").json()["last_status"] == "succeeded"
    assert len(list(directory.glob("jarvis-auto-*.jbackup"))) == 1


def test_shutdown_waits_for_active_backup(tmp_path, monkeypatch):
    schedule = ScheduledBackup(tmp_path)
    started = threading.Event()
    release = threading.Event()

    def slow_tick(**_):
        started.set()
        assert release.wait(5)
        return {"last_status": "succeeded"}

    monkeypatch.setattr(schedule, "_tick", slow_tick)
    runner = threading.Thread(target=lambda: schedule.tick(force=True))
    runner.start()
    assert started.wait(2)
    closer = threading.Thread(target=schedule.close)
    closer.start()
    closer.join(0.05)
    assert closer.is_alive()
    release.set()
    runner.join(2)
    closer.join(2)
    assert not runner.is_alive() and not closer.is_alive()
