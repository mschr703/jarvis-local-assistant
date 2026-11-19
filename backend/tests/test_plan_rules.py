"""Planvorschläge behalten ihre verwendete Regelversion bei Ausführung und Restore."""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.chat_rules import ChatRuleConflict, PlanRuleStore, plan_system_prompt
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.engine import RunEngine, ToolDefinition
from jarvis.plans import PlanManager
from jarvis.provider import Completion


class ReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


def test_plan_rule_versions_conflict_rollback_and_backup(tmp_path):
    data = tmp_path / "data"
    store = PlanRuleStore(Database(data / "jarvis.db"))
    assert store.active()["version"] == 1 and store.active()["content"] == ""
    assert plan_system_prompt("Grundregel", "") == "Grundregel"
    assert "Werkzeugregister" in plan_system_prompt("Grundregel", "Kurz")
    with ThreadPoolExecutor(max_workers=2) as pool:
        changes = [pool.submit(store.create, f"Plane {i}.", 1) for i in range(2)]
        successes = 0
        for change in changes:
            try:
                change.result()
                successes += 1
            except ChatRuleConflict:
                pass
    assert successes == 1
    with pytest.raises(ChatRuleConflict):
        store.activate(1, 1)
    assert store.activate(1, 2)["active_version"] == 1
    archive = tmp_path / "backup.zip"
    assert create_backup(data, archive)["schema"] == 62
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    assert PlanRuleStore(Database(restored / "jarvis.db")).history()["revision"] == 3


def test_plan_rule_api_requires_csrf_and_current_revision(tmp_path):
    settings = Settings(JARVIS_DATA_DIR=tmp_path / "data", JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        assert client.post("/plan-rules/versions", json={"content": "Kurz", "expected_revision": 1}).status_code == 401
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        assert client.post("/plan-rules/versions", json={"content": "Kurz", "expected_revision": 1}).status_code == 403
        created = client.post("/plan-rules/versions", headers=headers,
                              json={"content": "Wenige Schritte", "expected_revision": 1})
        assert created.status_code == 200 and created.json()["active_version"] == 2
        assert client.post("/plan-rules/versions", headers=headers,
                           json={"content": "Veraltet", "expected_revision": 1}).status_code == 409
        assert client.post("/plan-rules/versions", headers=headers,
                           json={"content": "x" * 2001, "expected_revision": 2}).status_code == 422
        assert client.put("/plan-rules/active", headers=headers,
                          json={"version": 99, "expected_revision": 2}).status_code == 404
        rollback = client.put("/plan-rules/active", headers=headers,
                              json={"version": 1, "expected_revision": 2})
        assert rollback.status_code == 200 and rollback.json()["active_version"] == 1


@pytest.mark.asyncio
async def test_plan_keeps_rule_snapshot_during_model_switch_execution_and_restart(tmp_path, monkeypatch):
    data = tmp_path / "data"
    settings = Settings(JARVIS_DATA_DIR=data, JARVIS_OLLAMA_MODEL="fixture")
    db = Database(data / "jarvis.db")
    store = PlanRuleStore(db)
    store.create("Nur nötige Schritte", 1)
    seen = []

    async def fake_generate(_db, _settings, _purpose, messages):
        seen.append(messages)
        if len(seen) == 1:
            store.create("Zweite Vorgabe", 2)
        return Completion(json.dumps({"steps": [{"tool_name": "read", "args": {"value": "A"}}]}),
                          8, 5, 0.0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.plans.generate_text", fake_generate)
    engine = RunEngine(db, data / "checkpoints.db")
    engine.register(ToolDefinition("read", ReadArgs,
                                   lambda args: {"value": args.value}, "test", "read"))
    manager = PlanManager(db, engine, DocumentStore(db), settings)
    first = await manager.propose("Lies A")
    assert first["rule_version"] == 2
    assert "Nur nötige Schritte" in seen[0][0]["content"]
    assert "Zweite Vorgabe" not in seen[0][0]["content"]
    assert store.history()["active_version"] == 3
    manager.start(first["id"])
    deadline = time.monotonic() + 5
    while manager.get(first["id"])["status"] == "running" and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert manager.get(first["id"])["status"] == "succeeded"
    assert manager.get(first["id"])["rule_version"] == 2
    assert store.activate(1, 3)["active_version"] == 1
    second = await manager.propose("Lies B")
    assert second["rule_version"] == 1
    assert "Nur nötige Schritte" not in seen[1][0]["content"]
    manager.close()
    engine.close()
    archive = tmp_path / "backup.zip"
    assert create_backup(data, archive)["schema"] == 62
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    assert PlanRuleStore(Database(restored / "jarvis.db")).history()["active_version"] == 1
    restored_engine = RunEngine(Database(restored / "jarvis.db"), restored / "checkpoints.db")
    reloaded = PlanManager(Database(restored / "jarvis.db"), restored_engine,
                           DocumentStore(Database(restored / "jarvis.db")),
                           Settings(JARVIS_DATA_DIR=restored, JARVIS_OLLAMA_MODEL="fixture"))
    assert reloaded.get(first["id"])["rule_version"] == 2
    reloaded.close()
    restored_engine.close()
