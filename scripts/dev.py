"""Startet Backend und UI mit einem Befehl; beendet beide bei Ctrl+C."""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PID_FILE = ROOT / ".jarvis-dev.json"


def main() -> int:
    venv_python = ROOT / "backend" / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    vite_cli = ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
    if not venv_python.exists() or not vite_cli.exists():
        print("Abhängigkeiten fehlen: zuerst uv sync --project backend und pnpm --dir frontend install ausführen", file=sys.stderr)
        return 1
    for port in (8765, 5173):
        with socket.socket() as probe:
            probe.settimeout(0.5)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                print(f"Port {port} ist belegt. Laufende Jarvis-Prozesse mit .\\scripts\\stop.ps1 prüfen.",
                      file=sys.stderr)
                return 1
    commands = [
        ([str(venv_python), "-m", "uvicorn", "jarvis.app:create_app", "--factory",
          "--host", "127.0.0.1", "--port", "8765"], ROOT),
        ([shutil.which("node") or "node", str(vite_cli), "frontend", "--host", "127.0.0.1", "--config", "frontend/vite.config.ts"], ROOT),
    ]
    if sys.platform == "win32":
        commands.append(([str(venv_python), str(ROOT / "scripts" / "hotkey.py"),
                          "--owner-pid", str(os.getpid())], ROOT))
    processes = []
    frontend_env = os.environ.copy()
    frontend_env.pop("JARVIS_OPENROUTER_API_KEY", None)
    helper_reported = False
    try:
        for index, (command, cwd) in enumerate(commands):
            processes.append(subprocess.Popen(
                command, cwd=cwd, env=None if index == 0 else frontend_env,
            ))
        PID_FILE.write_text(json.dumps({"backend": processes[0].pid,
                                        "frontend": processes[1].pid,
                                        "hotkey": processes[2].pid if len(processes) > 2 else None,
                                        "owner_pid": os.getpid()}), encoding="utf-8")
        print("Jarvis UI: http://127.0.0.1:5173", flush=True)
        while all(process.poll() is None for process in processes[:2]):
            if len(processes) > 2 and processes[2].poll() is not None and not helper_reported:
                print("Jarvis-Hotkey nicht aktiv; Backend und Oberfläche laufen weiter.",
                      file=sys.stderr, flush=True)
                helper_reported = True
            time.sleep(0.5)
        for name, process in zip(("Backend", "Oberfläche"), processes[:2], strict=True):
            if process.poll() is not None:
                print(f"Jarvis-{name} wurde unerwartet beendet (Code {process.returncode}).",
                      file=sys.stderr)
                return process.returncode or 1
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
