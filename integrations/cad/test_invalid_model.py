"""A rejected CAD build must not rewrite the previously exported files."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    source = (ROOT / "integrations" / "cad" / "holder.py").read_text(encoding="utf-8")
    original = "HOLE_SPACING_MM = 40.0"
    assert source.count(original) == 1
    artifacts = [ROOT / "artifacts" / "cad" / f"holder-v1.{extension}"
                 for extension in ("step", "stl")]
    before = [digest(path) for path in artifacts]
    environment = os.environ.copy()
    environment.update(CAD_TELEMETRY="0", CAD_UPDATE_CHECK="0", DO_NOT_TRACK="1")
    python = shutil.which("python")
    if python is None:
        raise RuntimeError("Python fehlt")
    with tempfile.TemporaryDirectory(prefix="jarvis-cad-invalid-") as temporary:
        script = Path(temporary) / "invalid_holder.py"
        script.write_text(source.replace(original, "HOLE_SPACING_MM = 60.0"),
                          encoding="utf-8")
        result = subprocess.run([
            python, "-m", "uv", "tool", "run", "--no-config", "--managed-python",
            "--python", "3.14", "--from", "cad-server==0.10.3", "cad", "run",
            str(script),
        ], cwd=ROOT, env=environment, capture_output=True, text=True,
            timeout=120, check=False)
    assert result.returncode != 0, result.stdout
    assert "Haltermaße oder Bohrungsabstand sind ungültig" in result.stdout + result.stderr
    assert [digest(path) for path in artifacts] == before
    print("invalid dimensions rejected; previous STEP/STL checksums unchanged")


if __name__ == "__main__":
    main()
