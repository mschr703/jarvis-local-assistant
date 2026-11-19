"""Echter Ollama-Mehrschrittplan mit sichtbarer Vorschau und Freigabe."""

import json
import time
from pathlib import Path

import httpx


def await_plan(client: httpx.Client, plan_id: str, expected: set[str]) -> dict:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        value = client.get(f"/plans/{plan_id}").json()
        if value["status"] in expected:
            return value
        time.sleep(0.1)
    raise RuntimeError("Planstatus wurde nicht erreicht")


def main() -> None:
    with httpx.Client(base_url="http://127.0.0.1:8765", timeout=180) as client:
        health = client.get("/health").json()
        if health["provider"] != "ollama" or not health["configured"]:
            raise RuntimeError("Planprobe benötigt einen gestarteten lokalen Ollama-Provider")
        token = client.get("/session").json()["csrf"]
        headers = {"X-Jarvis-CSRF": token}
        rules = client.post("/plan-rules/versions", headers=headers, json={
            "content": "Bevorzuge wenige notwendige und überprüfbare Schritte.",
            "expected_revision": 1,
        })
        rules.raise_for_status()
        assert rules.json()["active_version"] == 2
        response = client.post("/plans", headers=headers, json={
            "objective": "Öffne zuerst die Windows-Rechner-App. Speichere danach eine lokale Notiz mit Titel Planprobe und Inhalt Plan-Test.",
        })
        if response.status_code != 200:
            raise RuntimeError(f"Planvorschlag HTTP {response.status_code}: {response.text}")
        response.raise_for_status()
        plan = response.json()
        assert plan["rule_version"] == 2, plan
        assert plan["usage_id"] and plan["usage_duration_seconds"] is not None, plan
        assert plan["usage"]["cost_kind"] == "local_no_api_charge", plan
        print(json.dumps({"plan_id": plan["id"], "steps": plan["steps"]}, ensure_ascii=False))
        assert [item["tool_name"] for item in plan["steps"]] == ["open_app", "save_note"], plan
        assert plan["steps"][0]["args"]["app_id"] == "calculator", plan
        assert "Plan-Test" in plan["steps"][1]["args"]["content"], plan
        rollback = client.put("/plan-rules/active", headers=headers, json={
            "version": 1, "expected_revision": 2,
        })
        rollback.raise_for_status()
        assert rollback.json()["active_version"] == 1
        client.post(f"/plans/{plan['id']}/start", headers=headers).raise_for_status()
        pending = await_plan(client, plan["id"], {"waiting_approval", "failed", "interrupted"})
        assert pending["status"] == "waiting_approval", pending
        run_id = pending["current_run_id"]
        run = client.get(f"/runs/{run_id}").json()
        approved = client.post(f"/runs/{run_id}/decision", headers=headers,
                               json={"args_hash": run["args_hash"], "approved": True})
        approved.raise_for_status()
        done = await_plan(client, plan["id"], {"succeeded", "failed", "interrupted"})
        assert done["status"] == "succeeded", done
        assert done["rule_version"] == 2, done
        assert done["usage_id"] == plan["usage_id"]
        note_run = client.get(f"/runs/{done['steps'][1]['run_id']}").json()
        path = Path(json.loads(note_run["result_json"])["path"])
        assert "Plan-Test" in path.read_text(encoding="utf-8")
        evaluation = client.put(f"/plans/{plan['id']}/evaluation", headers=headers, json={
            "verdict": "achieved", "source_quality": "not_applicable",
            "reason": "Rechner gestartet und Inhalt der Notizdatei geprüft.",
            "regression_case": False, "version": 0,
        })
        evaluation.raise_for_status()
        assert evaluation.json()["verdict"] == "achieved"
        assert client.get(f"/plans/{plan['id']}").json()["evaluation"]["version"] == 1
        print(json.dumps({"plan_id": plan["id"], "status": done["status"],
                          "note": str(path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
