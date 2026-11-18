"""Geplante Windows-Backups mit echtem Restore und begrenzter Rotation."""

import hashlib
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
from jarvis.encrypted_backup import MAGIC
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


def test_verified_archive_rejects_changed_bytes_and_foreign_path(tmp_path):
    data, directory = tmp_path / "data", tmp_path / "backups"
    data.mkdir()
    directory.mkdir()
    archive = directory / "jarvis-auto-20261118T220000-abcdef12.jbackup"
    original = MAGIC + b"synthetische verschluesselte Bytes"
    archive.write_bytes(original)
    config = {"version": 1, "enabled": False, "directory": str(directory),
              "time_local": "03:00", "timezone": "Europe/Paris", "keep": 2,
              "secret_b64": "ZmFrZQ==", "last_archive": str(archive),
              "last_archive_sha256": hashlib.sha256(original).hexdigest()}
    schedule = ScheduledBackup(data)
    _save(schedule.path, config)
    handle, name, size = schedule.open_verified_archive()
    with handle:
        assert handle.read() == original
    assert name == archive.name and size == len(original)
    archive.write_bytes(original + b"x")
    with pytest.raises(ValueError, match="verändert"):
        schedule.open_verified_archive()
    archive.write_bytes(original)
    invalid = b"kein verschluesseltes Archiv"
    archive.write_bytes(invalid)
    config["last_archive_sha256"] = hashlib.sha256(invalid).hexdigest()
    _save(schedule.path, config)
    with pytest.raises(ValueError, match="format"):
        schedule.open_verified_archive()
    archive.write_bytes(original)
    config["last_archive_sha256"] = hashlib.sha256(original).hexdigest()
    foreign = tmp_path / archive.name
    foreign.write_bytes(original)
    config["last_archive"] = str(foreign)
    _save(schedule.path, config)
    with pytest.raises(ValueError, match="pfad"):
        schedule.open_verified_archive()
    config["last_archive"] = str(archive)
    config["last_archive_sha256"] = 42
    _save(schedule.path, config)
    with pytest.raises(ValueError, match="Noch kein"):
        schedule.open_verified_archive()


def test_rotation_retains_just_created_archive_even_with_future_named_file(tmp_path):
    data, directory = tmp_path / "data", tmp_path / "backups"
    directory.mkdir()
    current = directory / "jarvis-auto-20261118T220000-abcdef12.jbackup"
    future = directory / "jarvis-auto-20990101T000000-abcdef12.jbackup"
    manual = directory / "manual.jbackup"
    for path in (current, future, manual):
        path.write_bytes(b"synthetisch")
    ScheduledBackup(data)._rotate(directory, 1, current)
    assert current.exists() and manual.exists() and not future.exists()


def test_backup_configuration_errors_never_echo_passphrase(tmp_path):
    data = tmp_path / "data"
    settings = Settings(JARVIS_DATA_DIR=data, JARVIS_OLLAMA_MODEL="fixture")
    password = "synthetische lange Testpassphrase"
    payload = {"directory": str(tmp_path / "backups"), "time_local": "03:00",
               "timezone": "Europe/Paris", "keep": 2, "passphrase": password}
    with TestClient(create_app(settings)) as client:
        token = client.get("/session").json()["csrf"]
        for invalid in ({"passphrase": "kurz"}, {"time_local": "25:99"},
                        {"keep": "zwei"}, {"directory": "relative/backups"}):
            response = client.post("/backups/schedule/configure", json=payload | invalid,
                                   headers={"x-jarvis-csrf": token})
            assert response.status_code == 422
            assert password not in response.text
            assert "kurz" not in response.text
        missing = {key: value for key, value in payload.items() if key != "keep"}
        response = client.post("/backups/schedule/configure", json=missing,
                               headers={"x-jarvis-csrf": token})
        assert response.status_code == 422 and password not in response.text


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
def test_download_last_verified_archive_and_restore(profile, tmp_path):
    data, memory, directory = profile
    schedule = ScheduledBackup(data)
    schedule.configure(directory, "00:00", "Europe/Paris", 2,
                       "lange geplante Testpassphrase", now=datetime.now(UTC))
    settings = Settings(JARVIS_DATA_DIR=data, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        assert client.get("/backups/schedule/latest").status_code == 401
        token = client.get("/session").json()["csrf"]
        assert client.get("/backups/schedule/latest/check").status_code == 409
        assert client.get("/backups/schedule/latest", headers={"origin": "https://evil.example"}).status_code == 403
        assert client.get("/backups/schedule/latest", headers={"host": "evil.example"}).status_code == 403
        result = client.post("/backups/schedule/run", headers={"x-jarvis-csrf": token})
        assert result.status_code == 200 and result.json()["last_status"] == "succeeded"
        latest = result.json()
        assert len(latest["last_archive_sha256"]) == 64
        checked = client.get("/backups/schedule/latest/check")
        assert checked.status_code == 200
        assert checked.headers["cache-control"] == "no-store"
        assert checked.json()["name"] == Path(latest["last_archive"]).name
        downloaded = client.get("/backups/schedule/latest")
        assert downloaded.status_code == 200
        assert downloaded.headers["content-type"] == "application/octet-stream"
        assert downloaded.headers["cache-control"] == "no-store"
        assert "attachment" in downloaded.headers["content-disposition"]
        assert len(downloaded.content) == checked.json()["size"]
        assert hashlib.sha256(downloaded.content).hexdigest() == latest["last_archive_sha256"]
        copy = tmp_path / "heruntergeladen.jbackup"
        copy.write_bytes(downloaded.content)
        from jarvis.encrypted_backup import restore_encrypted_backup

        restored = tmp_path / "download-restore"
        restore_encrypted_backup(copy, restored, "lange geplante Testpassphrase")
        assert DocumentStore(Database(restored / "jarvis.db")).get_memory(memory["id"])["text"] == memory["text"]

        archive = Path(latest["last_archive"])
        with archive.open("r+b") as changed:
            changed.seek(-1, 2)
            final_byte = changed.read(1)[0]
            changed.seek(-1, 2)
            changed.write(bytes([final_byte ^ 1]))
        assert client.get("/backups/schedule/latest/check").status_code == 409
        assert client.get("/backups/schedule/latest").status_code == 409
        foreign = tmp_path / archive.name
        foreign.write_bytes(downloaded.content)
        config = json.loads(schedule.path.read_text(encoding="utf-8"))
        config["last_archive"] = str(foreign)
        schedule.path.write_text(json.dumps(config), encoding="utf-8")
        assert client.get("/backups/schedule/latest").status_code == 409


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-DPAPI")
def test_api_configure_and_disable_while_app_runs(profile):
    data, _, directory = profile
    settings = Settings(JARVIS_DATA_DIR=data, JARVIS_OLLAMA_MODEL="fixture")
    password = "lange API Testpassphrase"
    payload = {"directory": str(directory), "time_local": "00:00",
               "timezone": "Europe/Paris", "keep": 2, "passphrase": password}
    with TestClient(create_app(settings)) as client:
        token = client.get("/session").json()["csrf"]
        assert client.post("/backups/schedule/configure", json=payload).status_code == 403
        bad = client.post("/backups/schedule/configure", json=payload | {"passphrase": "kurz"},
                          headers={"x-jarvis-csrf": token})
        assert bad.status_code == 422 and "kurz" not in bad.text
        missing = client.post("/backups/schedule/configure",
                              json={key: value for key, value in payload.items() if key != "keep"},
                              headers={"x-jarvis-csrf": token})
        assert missing.status_code == 422 and password not in missing.text
        invalid_time = client.post("/backups/schedule/configure",
                                   json=payload | {"time_local": "25:99"},
                                   headers={"x-jarvis-csrf": token})
        assert invalid_time.status_code == 422 and password not in invalid_time.text
        relative = client.post("/backups/schedule/configure",
                               json=payload | {"directory": "relative/backups"},
                               headers={"x-jarvis-csrf": token})
        assert relative.status_code == 422 and "absoluter Pfad" in relative.text
        inside = client.post("/backups/schedule/configure",
                             json=payload | {"directory": str(data / "backups")},
                             headers={"x-jarvis-csrf": token})
        assert inside.status_code == 422 and password not in inside.text
        assert not (data / "backups").exists()
        response = client.post("/backups/schedule/configure", json=payload,
                               headers={"x-jarvis-csrf": token})
        assert response.status_code == 200 and response.json()["enabled"]
        assert password not in response.text
        assert "secret_b64" not in client.get("/backups/schedule").text
        assert password not in (data / "backup_schedule.json").read_text(encoding="utf-8")
        created = client.post("/backups/schedule/run", headers={"x-jarvis-csrf": token})
        assert created.status_code == 200 and created.json()["last_status"] == "succeeded"
        archive = Path(created.json()["last_archive"])
        assert archive.exists()
        assert client.post("/backups/schedule/disable").status_code == 403
        disabled = client.post("/backups/schedule/disable", headers={"x-jarvis-csrf": token})
        assert disabled.status_code == 200 and not disabled.json()["enabled"]
        assert archive.exists()
        assert client.post("/backups/schedule/run", headers={"x-jarvis-csrf": token}).status_code == 409
    with TestClient(create_app(settings)) as restarted:
        restarted.get("/session")
        assert not restarted.get("/backups/schedule").json()["enabled"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-DPAPI")
def test_live_configuration_waits_for_running_backup(profile):
    data, _, directory = profile
    lock = InstanceLock(data / "jarvis.lock")
    lock.acquire()
    schedule = ScheduledBackup(data, lock)
    errors = []
    schedule._tick_lock.acquire()
    try:
        worker = threading.Thread(
            target=lambda: _capture_configure_error(schedule, directory, errors), daemon=True)
        worker.start()
        time.sleep(0.1)
        assert worker.is_alive() and not schedule.path.exists()
    finally:
        schedule._tick_lock.release()
        worker.join(timeout=5)
        lock.release()
    assert not worker.is_alive() and not errors
    assert schedule.status()["enabled"]


def _capture_configure_error(schedule, directory, errors):
    try:
        schedule.configure(directory, "03:00", "Europe/Paris", 2,
                           "lange Testpassphrase", in_process=True)
    except Exception as exc:  # noqa: BLE001 - Threadfehler im Haupttest sichtbar machen.
        errors.append(exc)


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
