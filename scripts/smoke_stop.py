"""Prüft Start und portgebundenen Stopp des echten Windows-Entwicklungsservers."""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

ROOT = Path(__file__).resolve().parents[1]
PORTS = (8765, 5173)


def port_free(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) != 0


def main() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Diese Start-/Stopp-Probe benötigt Windows")
    if not all(port_free(port) for port in PORTS) or (ROOT / ".jarvis-dev.json").exists():
        raise RuntimeError("Jarvis vor dieser Probe stoppen; Ports/PID-Datei sind belegt")
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        raise RuntimeError("PowerShell wurde nicht gefunden")
    with TemporaryDirectory(prefix="jarvis-stop-smoke-") as temporary:
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(Path(temporary) / "data"),
                           JARVIS_PROVIDER="ollama", JARVIS_OLLAMA_MODEL="qwen3:4b-instruct")
        environment.pop("JARVIS_OPENROUTER_API_KEY", None)
        with (Path(temporary) / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                                       cwd=ROOT, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Jarvis endete vor der Bereitschaft")
                    try:
                        health = httpx.get("http://127.0.0.1:8765/health", timeout=2)
                        ui = httpx.get("http://127.0.0.1:5173/", timeout=2)
                        if health.status_code == 200 and ui.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.2)
                else:
                    raise RuntimeError("Backend oder UI wurden nicht bereit")
                stopped = subprocess.run([shell, "-NoProfile", "-File", str(ROOT / "scripts" / "stop.ps1")],
                                         cwd=ROOT, capture_output=True, text=True, timeout=30, check=True)
                process.wait(timeout=10)
                assert all(port_free(port) for port in PORTS)
                assert not (ROOT / ".jarvis-dev.json").exists()
                print(json.dumps({"health": health.json()["status"], "ui": ui.status_code,
                                  "stopped": stopped.stdout.strip().splitlines(),
                                  "ports_free": list(PORTS)}, ensure_ascii=False))
            finally:
                if any(not port_free(port) for port in PORTS) or (ROOT / ".jarvis-dev.json").exists():
                    subprocess.run([shell, "-NoProfile", "-File", str(ROOT / "scripts" / "stop.ps1")],
                                   cwd=ROOT, capture_output=True, text=True, timeout=30, check=False)
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)


if __name__ == "__main__":
    main()
