"""Install, upgrade or remove a trusted local Jarvis module. Never downloads code."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from jarvis.config import load_settings
from jarvis.db import Database
from jarvis.modules import (
    check_package_syntax,
    checked_package,
    install_package,
    package_digest,
    remove_package,
    upgrade_package,
)


def install(source: Path, data_dir: Path) -> str:
    return install_package(source, data_dir, Database(data_dir / "jarvis.db"))


def remove(module_id: str, data_dir: Path) -> str:
    return remove_package(module_id, data_dir, Database(data_dir / "jarvis.db"))


def upgrade(source: Path, data_dir: Path) -> str:
    return upgrade_package(source, data_dir, Database(data_dir / "jarvis.db"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["verify", "install", "upgrade", "remove"])
    parser.add_argument("module", help="Source directory for install/upgrade, module ID for remove")
    parser.add_argument("--data-dir", type=Path, default=load_settings().data_dir)
    args = parser.parse_args()
    data_dir = args.data_dir.expanduser().resolve()
    if args.action == "verify":
        source = Path(args.module).expanduser()
        manifest = checked_package(source)
        digest = package_digest(source)
        check_package_syntax(source)
        if package_digest(source) != digest:
            raise ValueError("Modulquelle hat sich während der Syntaxprüfung geändert")
        print(f"Verified {manifest.id} {manifest.version} (SHA-256 {digest})")
    elif args.action == "install":
        print(f"Installed {install(Path(args.module).expanduser(), data_dir)}")
    elif args.action == "upgrade":
        print(f"Upgraded {upgrade(Path(args.module).expanduser(), data_dir)} (data retained)")
    else:
        print(f"Removed {remove(args.module, data_dir)} (data retained)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"Module error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
