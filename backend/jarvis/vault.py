"""Windows-Benutzertresor für Kontozugangsdaten ohne HTTP-Secret-Ausgabe."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import threading
from pathlib import Path
from uuid import UUID, uuid4

from jarvis.db import utcnow
from jarvis.windows_secret import protect_secret, unprotect_secret

VAULT_NAME = "vault_credentials.json"
MAX_ITEMS = 30
MAX_FILE_BYTES = 256 * 1024
KINDS = {"api_key", "access_token", "refresh_token", "password"}


class VaultConflict(ValueError):
    """Veraltete Version oder bereits verwendete Kontozuordnung."""


class CredentialVault:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / VAULT_NAME
        self._lock = threading.Lock()

    @staticmethod
    def _identity(provider: str, account: str, kind: str) -> tuple[str, str, str]:
        if not isinstance(provider, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,39}", provider):
            raise ValueError("Ungültiger Anbietername")
        if not isinstance(account, str) or not 1 <= len(account) <= 200 \
                or account != account.strip() or any(ord(char) < 32 for char in account):
            raise ValueError("Ungültige Kontokennung")
        if not isinstance(kind, str) or kind not in KINDS:
            raise ValueError("Ungültiger Geheimnistyp")
        return provider, account.casefold(), kind

    @staticmethod
    def _secret(secret: str) -> str:
        if not isinstance(secret, str) or not secret or "\x00" in secret \
                or len(secret.encode("utf-8")) > 8 * 1024:
            raise ValueError("Geheimnis fehlt oder ist zu lang")
        return secret

    @staticmethod
    def _bound_payload(item: dict, secret: str) -> str:
        return json.dumps({"id": item["id"], "provider": item["provider"],
                           "account": item["account"], "kind": item["kind"],
                           "secret": secret}, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _unseal(item: dict) -> str:
        try:
            blob = base64.b64decode(item["secret_b64"], validate=True)
            payload = json.loads(unprotect_secret(blob))
            if (not isinstance(payload, dict) or
                    any(payload.get(field) != item[field]
                        for field in ("id", "provider", "account", "kind"))):
                raise ValueError("Kontobindung stimmt nicht")
            return CredentialVault._secret(payload.get("secret"))
        except (ValueError, RuntimeError, KeyError, TypeError, UnicodeError, binascii.Error) as exc:
            raise RuntimeError("Zugangsdaten können für diesen Windows-Benutzer nicht gelesen werden") from exc

    @staticmethod
    def _public(item: dict) -> dict:
        try:
            CredentialVault._unseal(item)
            usable = True
        except RuntimeError:
            usable = False
        return {key: item[key] for key in ("id", "provider", "account", "kind",
                                            "version", "created_at", "updated_at")} | {"usable": usable}

    def _load(self) -> list[dict]:
        if self.path.is_symlink() or self.path.is_junction():
            raise ValueError("Tresordatei darf kein Link sein")
        if not self.path.exists():
            return []
        try:
            if self.path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("Tresordatei ist zu groß")
            root = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("Tresordatei ist nicht lesbar") from exc
        if (not isinstance(root, dict) or type(root.get("version")) is not int or root["version"] != 1
                or not isinstance(root.get("items"), list) or len(root["items"]) > MAX_ITEMS):
            raise ValueError("Tresordatei ist ungültig")
        identities = set()
        ids = set()
        for item in root["items"]:
            if not isinstance(item, dict) or set(item) != {
                    "id", "provider", "account", "kind", "version", "created_at",
                    "updated_at", "secret_b64"}:
                raise ValueError("Tresordatei ist ungültig")
            try:
                if str(UUID(item["id"])) != item["id"]:
                    raise ValueError("Ungültige ID")
                identity = self._identity(item["provider"], item["account"], item["kind"])
                if type(item["version"]) is not int or item["version"] < 1 \
                        or not all(isinstance(item[key], str) for key in ("created_at", "updated_at")) \
                        or not isinstance(item["secret_b64"], str) \
                        or len(item["secret_b64"]) > 32 * 1024:
                    raise ValueError("Ungültiger Eintrag")
                base64.b64decode(item["secret_b64"], validate=True)
            except (TypeError, ValueError, KeyError, binascii.Error) as exc:
                raise ValueError("Tresordatei ist ungültig") from exc
            if item["id"] in ids or identity in identities:
                raise ValueError("Doppelte Kontozuordnung im Tresor")
            ids.add(item["id"])
            identities.add(identity)
        return root["items"]

    def _save(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink() or self.path.is_junction():
            raise ValueError("Tresordatei darf kein Link sein")
        encoded = json.dumps({"version": 1, "items": items}, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            raise ValueError("Tresor ist voll")
        pending = self.path.with_name(self.path.name + "." + uuid4().hex + ".partial")
        try:
            with pending.open("xb") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            if self.path.is_symlink() or self.path.is_junction():
                raise ValueError("Tresordatei darf kein Link sein")
            pending.replace(self.path)
        finally:
            pending.unlink(missing_ok=True)

    def list(self) -> list[dict]:
        with self._lock:
            return [self._public(item) for item in self._load()]

    def add(self, provider: str, account: str, kind: str, secret: str) -> dict:
        identity = self._identity(provider, account, kind)
        self._secret(secret)
        with self._lock:
            items = self._load()
            if len(items) >= MAX_ITEMS:
                raise ValueError("Höchstens 30 Zugangsdaten möglich")
            if any(self._identity(item["provider"], item["account"], item["kind"]) == identity
                   for item in items):
                raise VaultConflict("Kontozuordnung besteht bereits; zum Ersetzen rotieren")
            now = utcnow()
            item = {"id": str(uuid4()), "provider": provider, "account": account,
                    "kind": kind, "version": 1, "created_at": now, "updated_at": now}
            item["secret_b64"] = base64.b64encode(
                protect_secret(self._bound_payload(item, secret))).decode("ascii")
            items.append(item)
            self._save(items)
            return self._public(item)

    def rotate(self, credential_id: str, expected_version: int, secret: str) -> dict:
        self._secret(secret)
        with self._lock:
            items = self._load()
            item = next((item for item in items if item["id"] == credential_id), None)
            if item is None:
                raise ValueError("Zugangsdaten nicht gefunden")
            if type(expected_version) is not int or item["version"] != expected_version:
                raise VaultConflict("Zugangsdaten wurden zwischenzeitlich geändert")
            item["version"] += 1
            item["updated_at"] = utcnow()
            item["secret_b64"] = base64.b64encode(
                protect_secret(self._bound_payload(item, secret))).decode("ascii")
            self._save(items)
            return self._public(item)

    def revoke(self, credential_id: str, expected_version: int) -> dict:
        with self._lock:
            items = self._load()
            item = next((item for item in items if item["id"] == credential_id), None)
            if item is None:
                raise ValueError("Zugangsdaten nicht gefunden")
            if type(expected_version) is not int or item["version"] != expected_version:
                raise VaultConflict("Zugangsdaten wurden zwischenzeitlich geändert")
            self._save([entry for entry in items if entry["id"] != credential_id])
        return {"revoked": True}

    def secret_for(self, credential_id: str, provider: str, account: str, kind: str) -> str:
        """Nur Backend-Connectoren verwenden diese Methode; keine HTTP-Route anbieten."""
        identity = self._identity(provider, account, kind)
        with self._lock:
            item = next((item for item in self._load() if item["id"] == credential_id), None)
            if item is None or self._identity(item["provider"], item["account"], item["kind"]) != identity:
                raise ValueError("Keine passende Kontozuordnung")
            return self._unseal(item)

    def secret_for_identity(self, provider: str, account: str, kind: str) -> str | None:
        """Liest die eindeutige Kontozuordnung nur im Backend; fehlend bleibt None."""
        identity = self._identity(provider, account, kind)
        with self._lock:
            item = next((entry for entry in self._load()
                         if self._identity(entry["provider"], entry["account"], entry["kind"])
                         == identity), None)
            return self._unseal(item) if item else None
