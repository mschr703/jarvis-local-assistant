"""CLI für normale und passwortgeschützte lokale Sicherungen."""

import argparse
import getpass
import json
import zipfile
from pathlib import Path

from jarvis.backup import create_backup, restore_backup
from jarvis.config import default_data_dir
from jarvis.encrypted_backup import create_encrypted_backup, restore_encrypted_backup


def main() -> None:
    parser = argparse.ArgumentParser(description="Jarvis-Backup und Wiederherstellung")
    parser.add_argument("action", choices=["create", "restore", "create-encrypted",
                                            "restore-encrypted"])
    parser.add_argument("archive", type=Path)
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    args = parser.parse_args()
    try:
        if args.action == "create":
            result = create_backup(args.data_dir, args.archive)
        elif args.action == "restore":
            result = restore_backup(args.archive, args.data_dir)
        elif args.action == "create-encrypted":
            password = getpass.getpass("Backup-Passwort: ")
            if not password or password != getpass.getpass("Backup-Passwort wiederholen: "):
                raise ValueError("Passwörter stimmen nicht überein oder sind leer")
            result = create_encrypted_backup(args.data_dir, args.archive, password)
        else:
            password = getpass.getpass("Backup-Passwort: ")
            result = restore_encrypted_backup(args.archive, args.data_dir, password)
    except (ValueError, RuntimeError, OSError, zipfile.BadZipFile) as exc:
        parser.exit(2, f"Backup/Wiederherstellung fehlgeschlagen: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
