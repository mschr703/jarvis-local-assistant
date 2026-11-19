import json
from pathlib import Path

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings


def test_real_write_tool_waits_for_exact_approval_and_is_idempotent(tmp_path):
    settings = Settings(JARVIS_DATA_DIR=tmp_path)
    with TestClient(create_app(settings)) as client:
        token = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": token}
        run = client.post("/runs", headers=headers, json={
            "tool_name": "save_note", "args": {"title": "Projekt", "content": "Entscheidung A"},
            "client_request_id": "approval-flow-request",
        }).json()
        assert run["status"] == "waiting_approval"
        assert not (tmp_path / "notes").exists()
        assert client.post(f"/runs/{run['id']}/decision", headers=headers,
                           json={"args_hash": "0" * 64, "approved": True}).status_code == 422
        approved = client.post(f"/runs/{run['id']}/decision", headers=headers,
                               json={"args_hash": run["args_hash"], "approved": True})
        assert approved.status_code == 200, approved.text
        result = approved.json()
        assert result["status"] == "succeeded"
        path = Path(json.loads(result["result_json"])["path"])
        assert path.read_text(encoding="utf-8") == "Projekt\n\nEntscheidung A\n"
        again = client.post("/runs", headers=headers, json={
            "tool_name": "save_note", "args": {"title": "Projekt", "content": "Entscheidung A"},
            "client_request_id": "approval-flow-request",
        }).json()
        assert again["id"] == run["id"]
        assert len(list((tmp_path / "notes").glob("*.txt"))) == 1
    with TestClient(create_app(settings)) as restarted:
        restarted.get("/session")
        assert restarted.get(f"/runs/{run['id']}").json()["status"] == "succeeded"
