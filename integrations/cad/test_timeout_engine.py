"""Probe that a Jarvis-owned CAD engine can be stopped during a slow build.

The real user engine has a different door. This test only kills PIDs discovered
through its own freshly created CAD_ENGINE_DIR and verifies the prior exports.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CAD = ["-m", "uv", "tool", "run", "--no-config", "--managed-python",
       "--python", "3.14", "--from", "cad-server==0.10.3", "cad"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cad(python: str, env: dict[str, str], *args: str, timeout: int = 45) -> str:
    result = subprocess.run([python, *CAD, *args], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=timeout,
                            check=False)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout


def kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, check=False, timeout=10)
    else:
        os.kill(pid, 9)


def main() -> None:
    python = shutil.which("python")
    if not python:
        raise RuntimeError("Python fehlt")
    exports = [ROOT / "artifacts" / "cad" / f"holder-v1.{suffix}"
               for suffix in ("step", "stl")]
    before = [sha(path) for path in exports]
    base_env = os.environ.copy()
    base_env.update(CAD_TELEMETRY="0", CAD_UPDATE_CHECK="0", DO_NOT_TRACK="1")
    default_status = cad(python, base_env, "status")
    default_match = re.search(r"engine server (\d+)", default_status)
    default_pid = int(default_match.group(1)) if default_match else None
    data_root = (ROOT / "data").resolve()
    data_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="jarvis-cad-timeout-", dir=data_root,
                                     ignore_cleanup_errors=True) as temporary:
        root = Path(temporary).resolve()
        if not root.is_relative_to(data_root) or not root.name.startswith("jarvis-cad-timeout-"):
            raise RuntimeError("Unsicherer temporärer CAD-Testordner")
        script = root / "slow.py"
        script.write_text(
            "import time\nimport build123d as bd\nfrom cad import model\n"
            "@model\ndef slow():\n    time.sleep(120)\n    return bd.Box(1, 1, 1)\n"
            "if __name__ == '__main__':\n    slow()\n", encoding="utf-8")
        env = base_env | {"CAD_ENGINE_DIR": str(root / "engine"),
                          "CAD_STATE_DIR": str(root / "state"),
                          "CAD_ENGINE_PORT": "0", "CAD_ENGINE_WORKERS": "1",
                          "CAD_ENGINE_STANDBY": "0"}
        server_pid = None
        worker_pid = None
        with (root / "client.log").open("w+", encoding="utf-8") as log:
            client = subprocess.Popen([python, *CAD, "run", str(script)], cwd=ROOT,
                                      env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 150
                while time.monotonic() < deadline:
                    if client.poll() is not None:
                        log.seek(0)
                        raise RuntimeError("CAD-Lauf endete vor Timeout-Probe: " + log.read()[-1000:])
                    try:
                        status = cad(python, env, "status")
                    except subprocess.TimeoutExpired:
                        continue
                    server = re.search(r"engine server (\d+)", status)
                    if server:
                        server_pid = int(server.group(1))
                        if server_pid == default_pid:
                            raise RuntimeError("CAD-Test hat keinen eigenen Server")
                    worker = re.search(r"busy\s+slow\.py\s+(\d+)", status)
                    if worker:
                        worker_pid = int(worker.group(1))
                    if worker_pid and "Building geometry" in status:
                        break
                    time.sleep(1)
                else:
                    raise TimeoutError("CAD-Test erreichte den langsamen Modellbau nicht")
                started = time.monotonic()
                kill_tree(client.pid)
                if server_pid:
                    kill_tree(server_pid)
                if worker_pid:
                    kill_tree(worker_pid)
                client.wait(timeout=10)
                elapsed = time.monotonic() - started
                assert elapsed < 20, elapsed
                assert "no engine server is running" in cad(python, env, "status")
                assert [sha(path) for path in exports] == before
                if default_pid:
                    assert f"engine server {default_pid}" in cad(python, base_env, "status")
                print(f"isolated slow build stopped in {elapsed:.2f}s; "
                      "default engine and prior exports unchanged")
            finally:
                if client.poll() is None:
                    kill_tree(client.pid)
                if server_pid:
                    kill_tree(server_pid)
                if worker_pid:
                    kill_tree(worker_pid)


if __name__ == "__main__":
    main()
