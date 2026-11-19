"""Install or remove a trusted local Jarvis feature module. Never downloads code."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from jarvis.config import load_settings
from jarvis.db import Database
from jarvis.modules import install_package, remove_package


def install(source: Path, data_dir: Path) -> str:
    return install_package(source, data_dir)


def remove(module_id: str, data_dir: Path) -> str:
    return remove_package(module_id, data_dir, Database(data_dir / "jarvis.db"))


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
