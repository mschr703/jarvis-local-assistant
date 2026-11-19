"""Echter Freigabeablauf über die lokale HTTP-API."""

import json
import time
from pathlib import Path
from uuid import uuid4

import httpx


def await_status(client: httpx.Client, run_id: str, expected: set[str]) -> dict:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        item = client.get(f"/runs/{run_id}").json()
        if item["status"] in expected:
            return item
        time.sleep(0.05)
    raise RuntimeError(f"Lauf {run_id} erreichte keinen Status aus {expected}")


def main() -> None:
    with httpx.Client(base_url="http://127.0.0.1:8765", timeout=30) as client:
        token = client.get("/session").json()["csrf"]
        headers = {"X-Jarvis-CSRF": token}
        response = client.post("/runs", headers=headers, json={
            "tool_name": "save_note", "args": {"title": "Freigabeprobe", "content": "Bestätigt."},
            "client_request_id": str(uuid4()), "background": True,
        })
        response.raise_for_status()
        run = await_status(client, response.json()["id"], {"waiting_approval", "failed"})
        assert run["status"] == "waiting_approval", run
        changed = client.post(f"/runs/{run['id']}/decision", headers=headers,
                              json={"args_hash": "0" * 64, "approved": True})
        assert changed.status_code == 422, changed.text
        approved = client.post(f"/runs/{run['id']}/decision", headers=headers,
                               json={"args_hash": run["args_hash"], "approved": True})
        approved.raise_for_status()
        result = approved.json()
        assert result["status"] == "succeeded", result
        path = Path(json.loads(result["result_json"])["path"])
        assert path.read_text(encoding="utf-8").endswith("Bestätigt.\n")
        print(json.dumps({"run_id": run["id"], "status": result["status"],
                          "note": str(path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
