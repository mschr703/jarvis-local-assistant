"""Fokusregel und einmalige Zustellung über echte Backend-Neustarts."""

import json
import socket
import time
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from smoke_reminder_restart import PORT, authenticated_client, start_server, stop_server


def main() -> None:
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            raise RuntimeError(f"Port {PORT} ist belegt; isolierte Probe nicht gestartet")
    with TemporaryDirectory(prefix="jarvis-focus-restart-") as temporary:
        data_dir = Path(temporary) / "data"
        with (Path(temporary) / "server.log").open("w", encoding="utf-8") as log:
            process = start_server(data_dir, log)
            try:
                with authenticated_client() as (client, headers):
                    due = (datetime.now(ZoneInfo("Europe/Paris"))
                           + timedelta(seconds=95)).replace(second=0, microsecond=0)
                    focus = client.put("/focus", headers=headers, json={
                        "manual_enabled": True, "schedule_enabled": False,
                        "start_time": "09:00", "end_time": "17:00",
                        "timezone": "Europe/Paris", "expected_version": 1,
                    }).raise_for_status().json()
                    assert focus["active"]
                    ids = []
                    for title, priority in (("Gesammelt", 2), ("Sofort", 5)):
                        task = client.post("/tasks", headers=headers, json={
                            "title": title, "priority": priority,
                            "local_due": due.strftime("%Y-%m-%dT%H:%M"),
                            "timezone": "Europe/Paris",
                        }).raise_for_status().json()
                        ids.append(task["id"])
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=15)

            time.sleep(max(0, due.timestamp() + 2 - time.time()))
            prior_ids = None
            for restart in range(2):
                process = start_server(data_dir, log)
                try:
                    with authenticated_client() as (client, headers):
                        state = client.get("/focus").raise_for_status().json()
                        assert state["active"] and state["manual_enabled"]
                        notices = client.get("/notifications").raise_for_status().json()
                        assert len(notices) == 2
                        current_ids = {item["id"] for item in notices}
                        assert prior_ids is None or current_ids == prior_ids
                        prior_ids = current_ids
                        low = next(item for item in notices if item["task_id"] == ids[0])
                        high = next(item for item in notices if item["task_id"] == ids[1])
                        assert low["focus_suppressed"] and low["focus_deferred_at"]
                        assert not high["focus_suppressed"] and high["focus_deferred_at"] is None
                        if restart == 1:
                            updated = client.put("/focus", headers=headers, json={
                                "manual_enabled": False, "schedule_enabled": False,
                                "start_time": "09:00", "end_time": "17:00",
                                "timezone": "Europe/Paris", "expected_version": state["version"],
                            }).raise_for_status().json()
                            assert not updated["active"]
                            after = client.get("/notifications").raise_for_status().json()
                            assert len(after) == 2 and not any(
                                item["focus_suppressed"] for item in after)
                            assert next(item for item in after if item["task_id"] == ids[0])[
                                "focus_deferred_at"]
                finally:
                    stop_server(process)
            print(json.dumps({"tasks": 2, "notifications": 2,
                              "duplicate_after_restart": False,
                              "important_immediate": True, "normal_summary": True}))


if __name__ == "__main__":
    main()
