"""Echte verschlüsselte Sicherung mit Integritäts- und Profilstartprüfung."""

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.encrypted_backup import create_encrypted_backup, restore_encrypted_backup


@pytest.fixture
def profile(tmp_path):
    data = tmp_path / "profile"
    memory = DocumentStore(Database(data / "jarvis.db")).add_memory(
        "Sehr vertraulicher Atlas-Stand", "Backup-Test", "local_only")
    (data / "notes").mkdir()
    (data / "notes" / "notiz.md").write_text("Nur im geschützten Archiv", encoding="utf-8")
    (data / ".env").write_text("JARVIS_OPENROUTER_API_KEY=secret", encoding="utf-8")
    return data, memory


def test_encrypted_roundtrip_and_new_profile_start(profile, tmp_path):
    data, memory = profile
    archive = tmp_path / "backup.jbackup"
    result = create_encrypted_backup(data, archive, "ein langes Testpasswort 123")
    assert result["encrypted"] and result["schema"] == 63
    payload = archive.read_bytes()
    assert not payload.startswith(b"PK")
    assert b"Sehr vertraulicher Atlas-Stand" not in payload
    assert "Nur im geschützten Archiv".encode() not in payload
    assert b"secret" not in payload
    restored = tmp_path / "restored"
    info = restore_encrypted_backup(archive, restored, "ein langes Testpasswort 123")
    assert info["encrypted"] and info["schema"] == 63
    assert (restored / "notes" / "notiz.md").read_text(encoding="utf-8") == "Nur im geschützten Archiv"
    assert not (restored / ".env").exists()
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=restored,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        client.get("/session").raise_for_status()
        assert any(item["id"] == memory["id"] for item in client.get("/memories").json())
    with pytest.raises(ValueError, match="noch nicht existierenden"):
        restore_encrypted_backup(archive, restored, "ein langes Testpasswort 123")


@pytest.mark.parametrize("kind", ["wrong-password", "bit-flip", "missing-final", "truncated"])
def test_encrypted_backup_rejects_wrong_or_damaged_archive(profile, tmp_path, kind):
    data, _ = profile
    archive = tmp_path / "backup.jbackup"
    create_encrypted_backup(data, archive, "ein langes Testpasswort 123")
    password = "ein langes Testpasswort 123"
    if kind == "wrong-password":
        password = "das falsche Passwort"
    else:
        payload = bytearray(archive.read_bytes())
        if kind == "bit-flip":
            payload[-25] ^= 1
        elif kind == "missing-final":
            del payload[-20:]
        else:
            del payload[-7:]
        archive.write_bytes(payload)
    target = tmp_path / "target"
    with pytest.raises(ValueError, match="Passwort|unvollständig|ungültig"):
        restore_encrypted_backup(archive, target, password)
    assert not target.exists()


def test_encrypted_backup_failure_leaves_no_partial_or_archive(tmp_path):
    data = tmp_path / "missing"
    destination = tmp_path / "backup.jbackup"
    with pytest.raises(ValueError, match="Datenbank nicht gefunden"):
        create_encrypted_backup(data, destination, "ein langes Testpasswort 123")
    assert not destination.exists()
    assert not Path(str(destination) + ".partial").exists()


def test_encrypted_cli_create_and_restore_without_password_in_output(profile, tmp_path,
                                                                     monkeypatch, capsys):
    data, memory = profile
    archive = tmp_path / "cli.jbackup"
    target = tmp_path / "cli-restored"
    script = Path(__file__).resolve().parents[2] / "scripts" / "backup.py"
    spec = importlib.util.spec_from_file_location("jarvis_backup_cli", script)
    assert spec is not None and spec.loader is not None
    backup_cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backup_cli)
    password = "CLI nur für den Test 123"
    monkeypatch.setattr(backup_cli.getpass, "getpass", lambda _: password)
    monkeypatch.setattr(sys, "argv", [str(script), "create-encrypted", str(archive),
                                      "--data-dir", str(data)])
    backup_cli.main()
    monkeypatch.setattr(sys, "argv", [str(script), "restore-encrypted", str(archive),
                                      "--data-dir", str(target)])
    backup_cli.main()
    output = capsys.readouterr()
    assert password not in output.out + output.err
    assert DocumentStore(Database(target / "jarvis.db")).get_memory(memory["id"])["text"] == memory["text"]
