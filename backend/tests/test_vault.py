"""Lokaler DPAPI-Tresor ohne Secret-Leck über API, Datei oder Backup."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.provider import OpenRouterAdapter, ProviderError, make_provider
from jarvis.vault import CredentialVault, VaultConflict

WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="Windows-DPAPI")


@WINDOWS
def test_vault_roundtrip_binding_rotation_and_backup_exclusion(tmp_path):
    data = tmp_path / "data"
    Database(data / "jarvis.db")
    vault = CredentialVault(data)
    secret = "synthetic-test-key-Atlas-123!"
    created = vault.add("openrouter", "maxim@example.test", "api_key", secret)
    assert created["usable"] and created["version"] == 1
    assert secret not in json.dumps(created) + json.dumps(vault.list())
    assert secret not in vault.path.read_text(encoding="utf-8")
    assert CredentialVault(data).secret_for(created["id"], "openrouter",
                                            "maxim@example.test", "api_key") == secret
    assert CredentialVault(data).secret_for_identity(
        "openrouter", "MAXIM@example.test", "api_key") == secret
    assert vault.secret_for_identity("openrouter", "default", "api_key") is None
    for provider, account, kind in (
            ("gmail", "maxim@example.test", "api_key"),
            ("openrouter", "other@example.test", "api_key"),
            ("openrouter", "maxim@example.test", "refresh_token")):
        with pytest.raises(ValueError, match="Kontozuordnung"):
            vault.secret_for(created["id"], provider, account, kind)
    with pytest.raises(VaultConflict):
        vault.add("openrouter", "MAXIM@example.test", "api_key", "other-test")
    rotated = vault.rotate(created["id"], 1, "synthetic-second-key")
    assert rotated["version"] == 2 and rotated["usable"]
    with pytest.raises(VaultConflict):
        vault.rotate(created["id"], 1, "synthetic-stale-key")
    assert vault.secret_for(created["id"], "openrouter",
                            "maxim@example.test", "api_key") == "synthetic-second-key"
    assert vault.secret_for_identity("openrouter", "maxim@example.test", "api_key") == "synthetic-second-key"
    archive = tmp_path / "backup.zip"
    create_backup(data, archive)
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    assert CredentialVault(restored).list() == []
    assert not (restored / vault.path.name).exists()
    assert vault.revoke(created["id"], 2) == {"revoked": True}
    assert vault.list() == []
    assert vault.secret_for_identity("openrouter", "maxim@example.test", "api_key") is None
    with pytest.raises(ValueError, match="nicht gefunden"):
        vault.revoke(created["id"], 2)


@WINDOWS
def test_blob_swap_and_unavailable_windows_user_are_visible_without_secret(tmp_path, monkeypatch):
    vault = CredentialVault(tmp_path)
    first = vault.add("outlook", "alpha@example.test", "refresh_token", "synthetic-alpha")
    second = vault.add("outlook", "beta@example.test", "refresh_token", "synthetic-beta")
    contents = json.loads(vault.path.read_text(encoding="utf-8"))
    contents["items"][0]["secret_b64"], contents["items"][1]["secret_b64"] = (
        contents["items"][1]["secret_b64"], contents["items"][0]["secret_b64"])
    vault.path.write_text(json.dumps(contents), encoding="utf-8")
    assert all(not item["usable"] for item in vault.list())
    with pytest.raises(RuntimeError, match="Windows-Benutzer") as error:
        vault.secret_for(first["id"], "outlook", "alpha@example.test", "refresh_token")
    assert "synthetic-" not in str(error.value)
    rotated = vault.rotate(first["id"], 1, "synthetic-repaired")
    assert rotated["usable"]
    assert vault.list()[1]["usable"] is False
    monkeypatch.setattr("jarvis.vault.unprotect_secret",
                        lambda _blob: (_ for _ in ()).throw(RuntimeError("anderer Benutzer")))
    assert not vault.list()[0]["usable"]
    with pytest.raises(RuntimeError, match="Windows-Benutzer"):
        vault.secret_for(first["id"], "outlook", "alpha@example.test", "refresh_token")
    assert second["id"] != first["id"]


@WINDOWS
def test_parallel_duplicate_prevention_and_invalid_file(tmp_path):
    vault = CredentialVault(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(vault.add, "gmail", "maxim@example.test", "access_token",
                               f"synthetic-key-{index}") for index in range(2)]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except VaultConflict:
                pass
    assert len(outcomes) == 1 and len(vault.list()) == 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        rotations = [pool.submit(vault.rotate, outcomes[0]["id"], 1,
                                 f"synthetic-rotation-{index}") for index in range(2)]
        rotated = []
        for future in rotations:
            try:
                rotated.append(future.result())
            except VaultConflict:
                pass
    assert len(rotated) == 1 and vault.list()[0]["version"] == 2
    vault.path.write_text('{"version":true,"items":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="ungültig"):
        vault.list()


@WINDOWS
def test_api_has_csrf_no_secret_response_and_revoke(tmp_path):
    settings = Settings(JARVIS_DATA_DIR=tmp_path / "data", JARVIS_OLLAMA_MODEL="fixture")
    secret = "synthetic-never-echo-this"
    with TestClient(create_app(settings)) as client:
        url = "/vault/credentials"
        body = {"provider": "openrouter", "account": "maxim@example.test",
                "kind": "api_key", "secret": secret}
        assert client.post(url, json=body).status_code == 401
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        assert client.post(url, json=body).status_code == 403
        invalid = client.post(url, json={**body, "secret": 123}, headers=headers)
        assert invalid.status_code == 422 and "123" not in invalid.text
        created = client.post(url, json=body, headers=headers)
        assert created.status_code == 200, created.text
        item = created.json()
        assert secret not in created.text and item["usable"]
        assert secret not in client.get(url).text
        assert client.post(url, json=body, headers=headers).status_code == 409
        rotated = client.post(f"{url}/{item['id']}/rotate", headers=headers,
                              json={"expected_version": 1, "secret": "synthetic-new-key"})
        assert rotated.status_code == 200 and rotated.json()["version"] == 2
        assert "synthetic-new-key" not in rotated.text + client.get(url).text
        stale = client.delete(f"{url}/{item['id']}?expected_version=1", headers=headers)
        assert stale.status_code == 409
        removed = client.delete(f"{url}/{item['id']}?expected_version=2", headers=headers)
        assert removed.status_code == 200 and removed.json()["revoked"]
        assert client.get(url).json() == []


@WINDOWS
def test_openrouter_uses_default_vault_key_and_revoke_stops_new_calls(tmp_path):
    config = Settings(JARVIS_DATA_DIR=tmp_path / "data", JARVIS_PROVIDER="openrouter",
                      JARVIS_OPENROUTER_MODEL="example/model")
    with TestClient(create_app(config)) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        assert client.get("/health").json()["status"] == "setup_required"
        created = client.post("/vault/credentials", headers=headers, json={
            "provider": "openrouter", "account": "default", "kind": "api_key",
            "secret": "synthetic-vault-first",
        }).json()
        assert "synthetic-vault-first" not in json.dumps(created)
        assert client.get("/health").json()["configured"] is True
        adapter = make_provider(config)
        assert isinstance(adapter, OpenRouterAdapter)
        assert adapter.api_key == "synthetic-vault-first"

        rotated = client.post(f"/vault/credentials/{created['id']}/rotate", headers=headers,
                              json={"expected_version": 1,
                                    "secret": "synthetic-vault-second"}).json()
        assert rotated["version"] == 2
        assert make_provider(config).api_key == "synthetic-vault-second"
        process_config = config.model_copy(update={"openrouter_api_key": "process-override"})
        assert make_provider(process_config).api_key == "process-override"

        client.delete(f"/vault/credentials/{created['id']}?expected_version=2",
                      headers=headers).raise_for_status()
        assert client.get("/health").json()["status"] == "setup_required"
        with pytest.raises(ProviderError, match="OpenRouter-Key und Modell"):
            make_provider(config)
        assert make_provider(process_config).api_key == "process-override"

        CredentialVault(config.data_dir).path.write_text("ungültig", encoding="utf-8")
        health = client.get("/health").json()
        assert health["status"] == "setup_required"
        assert "Tresor nicht lesbar" in health["message"]
        with pytest.raises(ProviderError, match="Tresor nicht lesbar"):
            make_provider(config)
