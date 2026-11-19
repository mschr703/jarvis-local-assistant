"""Misst eine echte fällige Erinnerung bei laufendem, regelmäßig erreichbarem Server."""

import json
import socket
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from smoke_reminder_restart import PORT, authenticated_client, start_server, stop_server


@contextmanager
def temporary_profile():
    directory = TemporaryDirectory(prefix="jarvis-reminder-awake-")
    try:
        yield directory.name
    finally:
        # Windows can release the stopped server's SQLite file handle slightly later.
        for attempt in range(20):
            try:
                directory.cleanup()
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.25)


def main() -> None:
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            raise RuntimeError(f"Port {PORT} ist belegt; isolierte Probe nicht gestartet")

    with (
        temporary_profile() as temporary,
        (Path(temporary) / "server.log").open("w", encoding="utf-8") as log,
    ):
            process = start_server(Path(temporary) / "data", log)
            try:
                with authenticated_client() as (client, headers):
                    due = (datetime.now(ZoneInfo("Europe/Paris"))
                           + timedelta(seconds=120)).replace(second=0, microsecond=0)
                    response = client.post("/tasks", headers=headers, json={
                        "title": "Wachbetrieb-Probe", "notes": "isolierter Test",
                        "local_due": due.strftime("%Y-%m-%dT%H:%M"),
                        "timezone": "Europe/Paris",
                    })
                    response.raise_for_status()
                    task = response.json()
                    assert task["reminder_status"] == "pending"
                    assert client.get("/notifications").raise_for_status().json() == []

                    previous_wall = time.time()
                    max_gap = 0.0
                    health_samples = 0
                    notification = None
                    deadline = due.timestamp() + 40
                    next_health = 0.0
                    while time.time() <= deadline:
                        now = time.time()
                        max_gap = max(max_gap, now - previous_wall)
                        previous_wall = now
                        assert process.poll() is None, "Testserver wurde vor Fälligkeit beendet"
                        if now >= next_health:
                            health = client.get("/health").raise_for_status().json()
                            assert health["status"] == "ok", health
                            health_samples += 1
                            next_health = now + 5
                        current = client.get("/notifications").raise_for_status().json()
                        assert len(current) <= 1, current
                        if current:
                            notification = current[0]
                            break
                        time.sleep(1)

                    assert max_gap <= 10, f"Host/Probe war {max_gap:.1f}s nicht aktiv"
                    assert notification is not None, "Keine Erinnerung innerhalb von 40s nach Fälligkeit"
                    assert notification["job_id"] == task["reminder_job_id"]
                    fired = datetime.fromisoformat(notification["created_at"]).astimezone(UTC)
                    delay = (fired - due.astimezone(UTC)).total_seconds()
                    assert 0 <= delay <= 40, f"Erinnerung um {delay:.1f}s verspätet"
                    assert client.get("/tasks").raise_for_status().json()[0]["reminder_status"] == "fired"
                    print(json.dumps({
                        "due_local": due.isoformat(),
                        "fired_local": fired.astimezone(ZoneInfo("Europe/Paris")).isoformat(),
                        "delay_seconds": delay,
                        "health_samples": health_samples,
                        "max_sampling_gap_seconds": round(max_gap, 2),
                        "notifications": 1,
                    }, ensure_ascii=False))
            finally:
                stop_server(process)


if __name__ == "__main__":
    main()
