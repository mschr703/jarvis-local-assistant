"""Passwortgeschützte, authentifizierte Hülle um das geprüfte M07-Backup."""

import os
import tempfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from jarvis.backup import MAX_ARCHIVE_BYTES, create_backup, restore_backup
from jarvis.instance import InstanceLock

MAGIC = b"JARVIS-ENCRYPTED-BACKUP-v1\n"
SALT_SIZE = 16
NONCE_PREFIX_SIZE = 8
HEADER_SIZE = len(MAGIC) + SALT_SIZE + NONCE_PREFIX_SIZE
CHUNK_SIZE = 1024 * 1024
TAG_SIZE = 16
KDF_N = 2**17
KDF_R = 8
KDF_P = 1
MAX_CIPHERTEXT_BYTES = MAX_ARCHIVE_BYTES + 64 * 1024 * 1024


def _key(passphrase: str, salt: bytes) -> bytes:
    if not isinstance(passphrase, str) or not passphrase:
        raise ValueError("Backup-Passwort darf nicht leer sein")
    return Scrypt(salt=salt, length=32, n=KDF_N, r=KDF_R, p=KDF_P).derive(
        passphrase.encode("utf-8"))


def _nonce(prefix: bytes, index: int) -> bytes:
    if index >= 2**32:
        raise ValueError("Backup enthält zu viele Blöcke")
    return prefix + index.to_bytes(4, "big")


def _aad(header: bytes, index: int, final: bool) -> bytes:
    return header + index.to_bytes(4, "big") + (b"F" if final else b"D")


def encrypt_archive(source: Path, destination: Path, passphrase: str) -> dict:
    """Verschlüsselt ein bestehendes ZIP blockweise, ohne es komplett in RAM zu laden."""
    source, destination = Path(source), Path(destination)
    if destination.exists():
        raise ValueError("Backup-Datei existiert bereits")
    if source.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Backup überschreitet 2 GiB")
    salt, prefix = os.urandom(SALT_SIZE), os.urandom(NONCE_PREFIX_SIZE)
    header = MAGIC + salt + prefix
    cipher = AESGCM(_key(passphrase, salt))
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_name(destination.name + ".partial")
    if pending.exists():
        raise ValueError("Temporäre Backup-Datei existiert bereits")
    try:
        with source.open("rb") as original, pending.open("xb") as encrypted:
            encrypted.write(header)
            index = 0
            while block := original.read(CHUNK_SIZE):
                ciphertext = cipher.encrypt(_nonce(prefix, index), block, _aad(header, index, False))
                encrypted.write(len(ciphertext).to_bytes(4, "big"))
                encrypted.write(ciphertext)
                index += 1
            terminator = cipher.encrypt(_nonce(prefix, index), b"", _aad(header, index, True))
            encrypted.write(len(terminator).to_bytes(4, "big"))
            encrypted.write(terminator)
            encrypted.flush()
            os.fsync(encrypted.fileno())
        pending.rename(destination)
    except BaseException:
        pending.unlink(missing_ok=True)
        raise
    return {"path": str(destination), "size": destination.stat().st_size,
            "encrypted": True, "format": "jarvis-encrypted-backup-v1"}


def decrypt_archive(source: Path, destination: Path, passphrase: str) -> None:
    """Authentifiziert jeden Block und einen verpflichtenden, letzten Endmarker."""
    source, destination = Path(source), Path(destination)
    if source.stat().st_size > MAX_CIPHERTEXT_BYTES:
        raise ValueError("Verschlüsseltes Backup ist zu groß")
    if destination.exists():
        raise ValueError("Temporäres Entschlüsselungsziel existiert bereits")
    try:
        with source.open("rb") as encrypted:
            header = encrypted.read(HEADER_SIZE)
            if len(header) != HEADER_SIZE or not header.startswith(MAGIC):
                raise ValueError("Verschlüsseltes Backup-Format nicht unterstützt")
            salt = header[len(MAGIC):len(MAGIC) + SALT_SIZE]
            prefix = header[-NONCE_PREFIX_SIZE:]
            cipher = AESGCM(_key(passphrase, salt))
            with destination.open("xb") as restored:
                total = 0
                index = 0
                while True:
                    length_bytes = encrypted.read(4)
                    if len(length_bytes) != 4:
                        raise ValueError("Verschlüsseltes Backup ist unvollständig")
                    length = int.from_bytes(length_bytes, "big")
                    if not TAG_SIZE <= length <= CHUNK_SIZE + TAG_SIZE:
                        raise ValueError("Verschlüsselter Backup-Block ist ungültig")
                    block = encrypted.read(length)
                    if len(block) != length:
                        raise ValueError("Verschlüsseltes Backup ist unvollständig")
                    final = length == TAG_SIZE
                    try:
                        plaintext = cipher.decrypt(_nonce(prefix, index), block,
                                                   _aad(header, index, final))
                    except InvalidTag as exc:
                        raise ValueError("Backup-Passwort falsch oder Archiv verändert") from exc
                    if final:
                        if encrypted.read(1):
                            raise ValueError("Daten nach dem Backup-Ende sind nicht zulässig")
                        break
                    total += len(plaintext)
                    if total > MAX_ARCHIVE_BYTES:
                        raise ValueError("Backup überschreitet 2 GiB")
                    restored.write(plaintext)
                    index += 1
                restored.flush()
                os.fsync(restored.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def create_encrypted_backup(data_dir: Path, destination: Path, passphrase: str,
                            *, owned_lock: InstanceLock | None = None) -> dict:
    data_dir, destination = Path(data_dir).resolve(), Path(destination).resolve()
    if destination.is_relative_to(data_dir):
        raise ValueError("Backup muss außerhalb des Jarvis-Datenordners liegen")
    if destination.exists():
        raise ValueError("Backup-Datei existiert bereits")
    with tempfile.TemporaryDirectory(prefix="jarvis-encrypt-") as temporary:
        intermediate = Path(temporary) / "source.zip"
        backup = create_backup(data_dir, intermediate, owned_lock=owned_lock)
        result = encrypt_archive(intermediate, destination, passphrase)
        result.update(schema=backup["schema"], file_count=backup["file_count"])
        return result


def restore_encrypted_backup(archive: Path, target_dir: Path, passphrase: str) -> dict:
    target_dir = Path(target_dir).resolve()
    if target_dir.exists():
        raise ValueError("Wiederherstellung benötigt einen neuen, noch nicht existierenden Zielordner")
    with tempfile.TemporaryDirectory(prefix="jarvis-decrypt-") as temporary:
        intermediate = Path(temporary) / "source.zip"
        decrypt_archive(archive, intermediate, passphrase)
        result = restore_backup(intermediate, target_dir)
        result["encrypted"] = True
        return result
