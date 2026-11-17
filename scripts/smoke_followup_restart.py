"""Prüft eine dringende Erinnerung und genau eine Nachfrage über echte Neustarts."""

import json
import socket
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from smoke_reminder_restart import PORT, authenticated_client, start_server, stop_server


def main() -> None:
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            raise RuntimeError(f"Port {PORT} ist belegt; isolierte Probe nicht gestartet")
    with TemporaryDirectory(prefix="jarvis-followup-restart-") as temporary:
        data_dir = Path(temporary) / "data"
        with (Path(temporary) / "server.log").open("w", encoding="utf-8") as log:
            process = start_server(data_dir, log)
            try:
                with authenticated_client() as (client, headers):
                    due = (datetime.now(ZoneInfo("Europe/Paris"))
                           + timedelta(seconds=105)).replace(second=0, microsecond=0)
                    response = client.post("/tasks", headers=headers, json={
                        "title": "Dringende Neustartprobe", "priority": 5,
                        "local_due": due.strftime("%Y-%m-%dT%H:%M"),
                        "timezone": "Europe/Paris",
                    })
                    response.raise_for_status()
                    task = response.json()
                    assert task["priority"] == 5 and task["reminder_status"] == "pending"
            finally:
                # Abrupter Stopp vor der Erstfälligkeit, wie bei einem beendeten Laptop-Prozess.
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=15)

            time.sleep(max(0, due.timestamp() + 2 - time.time()))
            process = start_server(data_dir, log)
            try:
                with authenticated_client() as (client, headers):
                    original = client.get("/notifications").raise_for_status().json()
                    assert len(original) == 1
                    notice = original[0]
                    assert notice["job_id"] == task["reminder_job_id"]
                    assert notice["followup_count"] == 0 and notice["followup_due_utc"]
                    client.post(f"/notifications/{notice['id']}/read", headers=headers).raise_for_status()
            finally:
                stop_server(process)

            # Den fälligen Datenbankzustand beschleunigen; die Produktionsfrist bleibt 1 Stunde.
            with closing(sqlite3.connect(data_dir / "jarvis.db")) as db, db:
                db.execute("UPDATE notifications SET followup_due_utc=? WHERE id=?", (
                    (datetime.now(UTC) - timedelta(minutes=1)).isoformat(), notice["id"]))

            for restart in range(2):
                process = start_server(data_dir, log)
                try:
                    with authenticated_client() as (client, headers):
                        current = client.get("/notifications").raise_for_status().json()
                        assert len(current) == 1 and current[0]["id"] == notice["id"]
                        assert current[0]["followup_count"] == 1, (restart, current)
                        assert current[0]["read_at"] is None
                        if restart == 1:
                            confirmed = client.post(f"/notifications/{notice['id']}/ack",
                                                    headers=headers).raise_for_status().json()
                            assert confirmed["acknowledged_at"]
                finally:
                    stop_server(process)

            process = start_server(data_dir, log)
            try:
                with authenticated_client() as (client, _headers):
                    final = client.get("/notifications").raise_for_status().json()
                    assert len(final) == 1 and final[0]["followup_count"] == 1
                    assert final[0]["acknowledged_at"] and final[0]["followup_due_utc"] is None
            finally:
                stop_server(process)
            print(json.dumps({"task_id": task["id"], "notification_id": notice["id"],
                              "first_after_restart": 1, "followup_after_restart": 1,
                              "duplicates": 0, "acknowledged": True}))


if __name__ == "__main__":
    main()
