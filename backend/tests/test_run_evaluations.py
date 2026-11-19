"""Bewertungen hängen an echten Laufbelegen und bleiben nach Restore prüfbar."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.engine import canonical_hash


def completed_run(db: Database) -> dict:
    args = {"url": "https://example.com/"}
    run = db.create_run("browser_read", json.dumps(args), canonical_hash("browser_read", args),
                        "read")
    db.ledger_start(run["id"], "browser_read", run["args_hash"], "read")
    db.ledger_finish(f"{run['id']}:0", "succeeded", '{"url":"https://example.com/"}')
    db.set_run(run["id"], "succeeded", '{"url":"https://example.com/"}')
    return run


def test_evaluation_requires_final_run_and_keeps_ledger_evidence(tmp_path):
    db = Database(tmp_path / "data" / "jarvis.db")
    pending = db.create_run("browser_read", "{}", "hash", "read")
    with pytest.raises(ValueError, match="abgeschlossene"):
        db.evaluate_run(pending["id"], "achieved", "Seite gelesen", False, "", 0)
    run = completed_run(db)
    with db.connect() as connection:
        connection.execute("UPDATE tool_ledger SET started_at=?,finished_at=? WHERE run_id=?",
                           ("2026-10-27T01:00:00+00:00", "2026-10-27T01:00:02.500000+00:00",
                            run["id"]))
    with pytest.raises(ValueError, match="Fehlerfall"):
        db.evaluate_run(run["id"], "achieved", "Seite gelesen", True, "Anders", 0)
    first = db.evaluate_run(run["id"], "partial", "Titel fehlte trotz HTTP-Antwort",
                            True, "Titel aus HTML extrahieren", 0)
    assert first["version"] == 1
    listed = next(item for item in db.list_runs() if item["id"] == run["id"])
    assert listed["execution_seconds"] == 2.5
    assert listed["model_cost_usd"] is None
    assert listed["evaluation"]["verdict"] == "partial"
    assert listed["result_json"] == '{"url":"https://example.com/"}'
    cases = db.list_evaluation_cases()
    assert len(cases) == 1 and cases[0]["args_hash"] == run["args_hash"]
    assert cases[0]["expected_result"] == "Titel aus HTML extrahieren"
    with pytest.raises(RuntimeError, match="zwischenzeitlich"):
        db.evaluate_run(run["id"], "failed", "Alter Stand", True, "Titel", 0)
    updated = db.evaluate_run(run["id"], "achieved", "Titel nachträglich geprüft",
                              False, "wird entfernt", 1)
    assert updated["version"] == 2 and updated["expected_result"] == ""
    assert db.list_evaluation_cases() == []


def test_parallel_evaluation_version_and_backup_roundtrip(tmp_path):
    data = tmp_path / "data"
    db = Database(data / "jarvis.db")
    run = completed_run(db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(db.evaluate_run, run["id"], "failed", f"Fehler {i}",
                               True, "Vollständiger Text", 0) for i in range(2)]
        successes = 0
        for future in futures:
            try:
                future.result()
                successes += 1
            except RuntimeError:
                pass
    assert successes == 1
    archive = tmp_path / "backup.zip"
    assert create_backup(data, archive)["schema"] == 59
    restored = tmp_path / "restored"
    assert restore_backup(archive, restored)["schema"] == 59
    reopened = Database(restored / "jarvis.db")
    assert len(reopened.list_evaluation_cases()) == 1
    assert reopened.list_runs()[0]["evaluation"]["version"] == 1


def test_evaluation_api_csrf_validation_and_export(tmp_path):
    data = tmp_path / "data"
    db = Database(data / "jarvis.db")
    run = completed_run(db)
    settings = Settings(JARVIS_DATA_DIR=data, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        url = f"/runs/{run['id']}/evaluation"
        body = {"verdict": "failed", "reason": "Titel fehlt im Ergebnis",
                "regression_case": True, "expected_result": "Titel anzeigen", "version": 0}
        assert client.put(url, json=body).status_code == 401
        csrf = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": csrf}
        assert client.put(url, json=body).status_code == 403
        assert client.put(url, json={**body, "unknown": 1}, headers=headers).status_code == 422
        assert client.put(url, json=body, headers=headers).status_code == 200
        assert client.put(url, json=body, headers=headers).status_code == 409
        assert client.put("/runs/missing/evaluation", json=body, headers=headers).status_code == 404
        listed = client.get("/runs").json()
        assert listed[0]["evaluation"]["verdict"] == "failed"
        assert listed[0]["model_cost_usd"] is None
        cases = client.get("/evaluation-cases").json()
        assert len(cases) == 1 and cases[0]["run_id"] == run["id"]
        assert cases[0]["expected_result"] == "Titel anzeigen"
