"""Stop the actual Jarvis CAD job while its isolated worker builds geometry."""

import hashlib
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from jarvis.cad import CadJob
from jarvis.engine import ToolUserError


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    exports = [ROOT / "artifacts" / "cad" / f"holder-v1.{suffix}"
               for suffix in ("step", "stl")]
    before = [digest(path) for path in exports]
    cancel = threading.Event()
    with tempfile.TemporaryDirectory(prefix="jarvis-cad-cancel-", dir=ROOT / "data",
                                     ignore_cleanup_errors=True) as temporary:
        root = Path(temporary).resolve()
        if not root.is_relative_to((ROOT / "data").resolve()):
            raise RuntimeError("Unsicherer CAD-Testordner")
        script = root / "slow.py"
        script.write_text(
            "import time\nimport build123d as bd\nfrom cad import model\n"
            "@model\ndef slow():\n    time.sleep(120)\n    return bd.Box(1, 1, 1)\n"
            "if __name__ == '__main__':\n    slow()\n", encoding="utf-8")
        outcome = []
        with CadJob(root / "jobs", cancel) as job:
            def run() -> None:
                try:
                    job.run("run", str(script))
                    outcome.append("unexpected success")
                except ToolUserError as exc:
                    outcome.append(str(exc))

            worker = threading.Thread(target=run)
            worker.start()
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                status = subprocess.run(job._command("status"), cwd=ROOT, env=job.env,
                                        capture_output=True, text=True, timeout=10,
                                        check=False).stdout
                if re.search(r"busy\s+slow\.py\s+\d+", status) and "Building geometry" in status:
                    break
                if not worker.is_alive():
                    raise RuntimeError("Langsamer CAD-Bau endete vor Abbruch: " + str(outcome))
                time.sleep(1)
            else:
                raise TimeoutError("CAD-Arbeiter erreichte den langsamen Geometriebau nicht")
            started = time.monotonic()
            cancel.set()
            worker.join(timeout=20)
            elapsed = time.monotonic() - started
            assert not worker.is_alive() and elapsed < 20, elapsed
            assert outcome == ["CAD-Auftrag gestoppt"], outcome
            assert not job._engine_pids(), "Isolierte CAD-Engine läuft nach Stopp weiter"
            assert [digest(path) for path in exports] == before
            print(f"Jarvis-CAD-Auftrag im Bau nach {elapsed:.2f}s gestoppt; Exporte unverändert")


if __name__ == "__main__":
    main()
