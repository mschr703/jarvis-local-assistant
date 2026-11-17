"""CLI zur lokalen Tagesplanung verschlüsselter Jarvis-Backups."""

import argparse
import getpass
import json
from pathlib import Path

from jarvis.config import default_data_dir
from jarvis.scheduled_backup import ScheduledBackup


def main() -> None:
    parser = argparse.ArgumentParser(description="Geplante Jarvis-Backups")
    parser.add_argument("action", choices=["configure", "status", "disable"])
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--time", default="03:00")
    parser.add_argument("--timezone", default="Europe/Paris")
    parser.add_argument("--keep", type=int, default=7)
    args = parser.parse_args()
    schedule = ScheduledBackup(args.data_dir)
    try:
        if args.action == "configure":
            if args.directory is None:
                raise ValueError("--directory für Backup-Zielordner angeben")
            password = getpass.getpass("Backup-Passwort: ")
            if not password or password != getpass.getpass("Backup-Passwort wiederholen: "):
                raise ValueError("Passwörter stimmen nicht überein oder sind leer")
            result = schedule.configure(args.directory, args.time, args.timezone,
                                        args.keep, password)
        elif args.action == "disable":
            result = schedule.disable()
        else:
            result = schedule.status()
    except (ValueError, RuntimeError, OSError) as exc:
        parser.exit(2, f"Backup-Planung fehlgeschlagen: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
