"""Prüft das einmalige Nachreichen einer Erinnerung über echte Serverneustarts."""

import json
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
PORT = 8767
BASE = f"http://127.0.0.1:{PORT}"


def start_server(data_dir: Path, log) -> subprocess.Popen:
    environment = os.environ.copy()
    environment.update(JARVIS_DATA_DIR=str(data_dir), JARVIS_PROVIDER="ollama",
                       JARVIS_OLLAMA_MODEL="qwen3:4b-instruct")
    environment.pop("JARVIS_OPENROUTER_API_KEY", None)
    python = ROOT / "backend" / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    process = subprocess.Popen(
        [str(python), "-m", "uvicorn", "jarvis.app:create_app", "--factory",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=ROOT / "backend", env=environment, stdout=log, stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Jarvis-Testserver endete beim Start: {process.returncode}")
        try:
            if httpx.get(f"{BASE}/health", timeout=2).status_code == 200:
                return process
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    stop_server(process)
    raise RuntimeError("Jarvis-Testserver wurde nicht bereit")


def stop_server(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


@contextmanager
def authenticated_client():
    with httpx.Client(base_url=BASE, timeout=5) as client:
        token = client.get("/session").raise_for_status().json()["csrf"]
        yield client, {"x-jarvis-csrf": token}


def main() -> None:
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            raise RuntimeError(f"Port {PORT} ist belegt; isolierte Probe nicht gestartet")
    with TemporaryDirectory(prefix="jarvis-reminder-restart-") as temporary:
        data_dir = Path(temporary) / "data"
        with (Path(temporary) / "server.log").open("w", encoding="utf-8") as log:
            process = start_server(data_dir, log)
            try:
                with authenticated_client() as (client, headers):
                    now = datetime.now(ZoneInfo("Europe/Paris"))
                    due = (now + timedelta(seconds=105)).replace(second=0, microsecond=0)
                    task_response = client.post("/tasks", headers=headers, json={
                        "title": "Neustartprobe", "notes": "isolierter Test",
                        "local_due": due.strftime("%Y-%m-%dT%H:%M"),
                        "timezone": "Europe/Paris",
                    })
                    task_response.raise_for_status()
                    task = task_response.json()
                    assert task["reminder_status"] == "pending"
                    assert client.get("/notifications").json() == []
            finally:
                # Simuliert einen abrupten Prozessabbruch ohne Lease-Freigabe.
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=15)

            time.sleep(max(0, due.timestamp() + 2 - time.time()))
            notification = None
            for restart in range(2):
                process = start_server(data_dir, log)
                try:
                    with authenticated_client() as (client, _headers):
                        current = client.get("/notifications").raise_for_status().json()
                        assert len(current) == 1, (restart, current)
                        assert current[0]["job_id"] == task["reminder_job_id"]
                        assert client.get("/tasks").raise_for_status().json()[0]["reminder_status"] == "fired"
                        if notification is None:
                            notification = current[0]
                        else:
                            assert current[0]["id"] == notification["id"]
                finally:
                    stop_server(process)
            print(json.dumps({"task_id": task["id"], "job_id": task["reminder_job_id"],
                              "due_local": task["due_local"], "notification_id": notification["id"],
                              "restarts": 2, "notifications": 1}, ensure_ascii=False))


if __name__ == "__main__":
    main()
