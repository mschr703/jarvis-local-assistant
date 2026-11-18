"""Planbewertungen ergänzen unveränderliche Plan- und Modellbelege."""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database, utcnow
from jarvis.documents import DocumentStore
from jarvis.engine import RunEngine, ToolDefinition
from jarvis.plans import PlanManager
from jarvis.provider import Completion


class ReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


@pytest.mark.asyncio
async def test_plan_usage_evaluation_conflict_case_and_restore(tmp_path, monkeypatch):
    data = tmp_path / "data"
    db = Database(data / "jarvis.db")
    usage_id = db.reserve_usage("plan", "ollama", "fixture", 0, 10, utcnow())
    db.finish_usage(usage_id, 15, 8, 0.0, "local_no_api_charge", "complete")

    async def fake_generate(_db, _settings, _purpose, _messages):
        return Completion(json.dumps({"steps": [{"tool_name": "read", "args": {"value": "A"}}]}),
                          15, 8, 0.0, "local_no_api_charge", usage_id)

    monkeypatch.setattr("jarvis.plans.generate_text", fake_generate)
    engine = RunEngine(db, data / "checkpoints.db")
    engine.register(ToolDefinition("read", ReadArgs,
                                   lambda args: {"value": args.value}, "test", "read"))
    manager = PlanManager(db, engine, DocumentStore(db),
                          Settings(JARVIS_DATA_DIR=data, JARVIS_OLLAMA_MODEL="fixture"))
    plan = await manager.propose("Lies A")
    assert plan["usage_id"] == usage_id
    assert plan["usage"]["input_tokens"] == 15
    assert plan["usage"]["cost_kind"] == "local_no_api_charge"
    assert plan["usage_duration_seconds"] is not None
    with pytest.raises(ValueError, match="abgeschlossene"):
        db.evaluate_plan(plan["id"], "failed", "unclear", "Zu früh", False, "", 0)
    manager.start(plan["id"])
    deadline = time.monotonic() + 15
    while manager.get(plan["id"])["status"] == "running" and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert manager.get(plan["id"])["status"] == "succeeded"
    with pytest.raises(ValueError, match="Fehlerfall"):
        db.evaluate_plan(plan["id"], "achieved", "supported", "Alles gut", True, "Anders", 0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        changes = [pool.submit(db.evaluate_plan, plan["id"], "partial", "unsupported",
                               f"Schritt {index} unvollständig", True, "Vollständiger Text", 0)
                   for index in range(2)]
        successes = 0
        for change in changes:
            try:
                change.result()
                successes += 1
            except RuntimeError:
                pass
    assert successes == 1
    stored = manager.get(plan["id"])
    assert stored["evaluation"]["verdict"] == "partial"
    assert stored["status"] == "succeeded" and stored["usage_id"] == usage_id
    cases = db.list_plan_evaluation_cases()
    assert len(cases) == 1 and cases[0]["objective"] == "Lies A"
    assert cases[0]["rule_version"] == 1 and cases[0]["usage_id"] == usage_id
    assert json.loads(cases[0]["steps_json"])[0]["tool_name"] == "read"
    assert cases[0]["run_results"][0]["status"] == "succeeded"
    assert json.loads(cases[0]["run_results"][0]["result_json"])["value"] == "A"
    manager.close()
    engine.close()
    archive = tmp_path / "backup.zip"
    assert create_backup(data, archive)["schema"] == 54
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    assert Database(restored / "jarvis.db").list_plan_evaluation_cases()[0]["usage_id"] == usage_id


def test_plan_evaluation_api_csrf_revision_and_missing_usage(tmp_path, monkeypatch):
    async def fake_generate(_db, _settings, _purpose, _messages):
        return Completion('{"steps":[{"tool_name":"open_app","args":{"app_id":"calculator"}}]}',
                          5, 4, 0.0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.plans.generate_text", fake_generate)
    settings = Settings(JARVIS_DATA_DIR=tmp_path / "data", JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        assert client.post("/plans", json={"objective": "Öffne Rechner"}).status_code == 401
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        proposed = client.post("/plans", headers=headers, json={"objective": "Öffne Rechner"})
        assert proposed.status_code == 200, proposed.text
        plan = proposed.json()
        assert plan["usage_id"] is None and plan["usage_duration_seconds"] is None
        url = f"/plans/{plan['id']}/evaluation"
        body = {"verdict": "failed", "source_quality": "not_applicable",
                "reason": "Es wurde noch nichts ausgeführt", "regression_case": True,
                "expected_result": "Rechner öffnen", "version": 0}
        assert client.put(url, json=body).status_code == 403
        assert client.put(url, json=body, headers=headers).status_code == 422
        assert client.post(f"/plans/{plan['id']}/cancel", headers=headers).status_code == 200
        assert client.put(url, json={**body, "extra": 1}, headers=headers).status_code == 422
        assert client.put(url, json=body, headers=headers).status_code == 200
        assert client.put(url, json=body, headers=headers).status_code == 409
        assert client.put("/plans/missing/evaluation", json=body, headers=headers).status_code == 404
        listed = client.get("/plans").json()
        assert listed[0]["evaluation"]["verdict"] == "failed"
        assert listed[0]["steps"][0]["tool_name"] == "open_app"
        cases = client.get("/plan-evaluation-cases").json()
        assert len(cases) == 1 and cases[0]["plan_id"] == plan["id"]
        updated = client.put(url, json={**body, "verdict": "achieved",
                                        "regression_case": False, "version": 1}, headers=headers)
        assert updated.status_code == 200 and updated.json()["expected_result"] == ""
        assert client.get("/plan-evaluation-cases").json() == []
