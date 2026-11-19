"""Install or remove a trusted local Jarvis feature module. Never downloads code."""

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from jarvis.config import load_settings
from jarvis.db import Database
from jarvis.modules import MODULE_API, MODULE_ID, ModuleManifest


def checked_module(source: Path) -> ModuleManifest:
    if not source.is_dir() or source.is_symlink() or source.is_junction():
        raise ValueError("Modulquelle muss ein gewöhnlicher Ordner sein")
    manifest = source / "jarvis-module.json"
    entry = source / "module.py"
    ui = source / "ui.js"
    for path in (manifest, entry, ui):
        if not path.is_file() or path.is_symlink() or path.is_junction():
            raise ValueError("Manifest, module.py und ui.js müssen gewöhnliche Dateien sein")
    if (manifest.stat().st_size > 16_384 or entry.stat().st_size > 2_000_000
            or ui.stat().st_size > 200_000):
        raise ValueError("Modulpaket ist zu groß")
    parsed = ModuleManifest.model_validate(json.loads(manifest.read_text(encoding="utf-8")))
    if parsed.id != source.name or parsed.api != MODULE_API:
        raise ValueError("Modul-ID oder API-Version passt nicht")
    return parsed


def install(source: Path, data_dir: Path) -> str:
    manifest = checked_module(source)
    root = data_dir / "modules"
    if root.is_symlink() or root.is_junction():
        raise ValueError("Modulverzeichnis darf kein Link sein")
    root.mkdir(parents=True, exist_ok=True)
    target = root / manifest.id
    if target.exists():
        raise ValueError("Modul ist bereits installiert; zuerst deaktivieren und entfernen")
    target.mkdir()
    try:
        for name in ("jarvis-module.json", "module.py", "ui.js"):
            shutil.copyfile(source / name, target / name)
    except BaseException:
        shutil.rmtree(target)
        raise
    return manifest.id


def remove(module_id: str, data_dir: Path) -> str:
    if not MODULE_ID.fullmatch(module_id):
        raise ValueError("Ungültige Modul-ID")
    root = data_dir / "modules"
    target = root / module_id
    if (not target.is_dir() or target.is_symlink() or target.is_junction()
            or target.resolve().parent != root.resolve()):
        raise ValueError("Modul ist nicht installiert")
    db = Database(data_dir / "jarvis.db")
    if db.get_settings().get(f"module.{module_id}.enabled") == "1":
        raise ValueError("Modul zuerst in Jarvis deaktivieren")
    required = {"jarvis-module.json", "module.py", "ui.js"}
    for child in target.iterdir():
        if child.name == "__pycache__":
            if (not child.is_dir() or child.is_symlink() or child.is_junction()
                    or child.resolve().parent != target.resolve()):
                raise ValueError("Modulcache ist kein sicherer Ordner")
            for cached in child.iterdir():
                if (not cached.is_file() or cached.suffix != ".pyc"
                        or cached.is_symlink() or cached.is_junction()):
                    raise ValueError("Modulcache enthält zusätzliche Dateien")
            continue
        if child.name not in required or not child.is_file():
            raise ValueError("Modulordner enthält zusätzliche Dateien; manuell prüfen")
        if child.is_symlink() or child.is_junction():
            raise ValueError("Modulordner enthält einen Link")
    if not required.issubset({child.name for child in target.iterdir()}):
        raise ValueError("Modulordner ist unvollständig")
    shutil.rmtree(target)
    return module_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "remove"])
    parser.add_argument("module", help="Source directory for install, module ID for remove")
    parser.add_argument("--data-dir", type=Path, default=load_settings().data_dir)
    args = parser.parse_args()
    data_dir = args.data_dir.expanduser().resolve()
    if args.action == "install":
        print(f"Installed {install(Path(args.module).expanduser(), data_dir)}")
    else:
        print(f"Removed {remove(args.module, data_dir)} (data retained)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"Module error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
