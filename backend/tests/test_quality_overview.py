"""The quality overview counts stored evidence without inventing cost or success."""

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database, utcnow
from jarvis.quality import overview
from jarvis.research import ResearchStore


def test_quality_overview_empty_and_mixed_evidence(tmp_path):
    data = tmp_path / "data"
    db = Database(data / "jarvis.db")
    empty = overview(db)
    assert all(item["total"] == item["reviewed"] == 0
               for item in empty["categories"].values())
    assert empty["recent"] == empty["regression_cases"] == []

    chat_id = db.create_chat()["id"]
    db.add_message(chat_id, "user", "Wie ist der Stand?")
    usage_id = db.reserve_usage("chat", "openrouter", "fixture", .2, 10, utcnow())
    db.finish_usage(usage_id, 8, 3, .1, "reported", "complete")
    answer = db.add_message(chat_id, "assistant", "Teilweise fertig", provider="openrouter",
                            model="fixture", usage_id=usage_id, cost_usd=.1,
                            cost_kind="reported")
    db.add_message(chat_id, "assistant", "Unbewertete Antwort")
    db.evaluate_chat_answer(chat_id, answer["id"], "partial", "unsupported",
                            "Beleg fehlt", True, "Beleg nennen", 0)

    plan_usage = db.reserve_usage("plan", "openrouter", "fixture", .5, 10, utcnow())
    db.finish_usage(plan_usage, 5, 2, .4, "estimated_from_current_list_price", "complete")
    with db.connect() as conn:
        conn.execute("""INSERT INTO plans(id,objective,steps_json,status,provider,model,
            created_at,updated_at,usage_id) VALUES(?,?,?,?,?,?,?,?,?)""",
                     ("plan-1", "Datei prüfen", "[]", "succeeded", "openrouter", "fixture",
                      utcnow(), utcnow(), plan_usage))
    db.evaluate_plan("plan-1", "achieved", "not_applicable", "Datei geprüft",
                     False, "", 0)

    run = db.create_run("open_file", "{}", "hash", "read")
    db.ledger_start(run["id"], "open_file", "hash", "read")
    db.ledger_finish(f"{run['id']}:0", "succeeded", "{}")
    db.set_run(run["id"], "succeeded", "{}")
    with db.connect() as conn:
        conn.execute("UPDATE tool_ledger SET started_at=?,finished_at=? WHERE run_id=?",
                     ("2026-11-17T20:00:00+00:00", "2026-11-17T20:00:02+00:00",
                      run["id"]))
    db.evaluate_run(run["id"], "failed", "Falsche Datei", True,
                    "Richtige Datei öffnen", 0)

    research = ResearchStore(db)
    report = research.save("Welche Quelle?", "Quelle", "fixture", [], [], "", "", [],
                           "ollama", "fixture")
    research.evaluate(report["id"], "unclear", "unclear", "Keine Quelle", False, "", 0)

    result = overview(db)
    categories = result["categories"]
    assert (categories["chat"]["total"], categories["chat"]["reviewed"],
            categories["chat"]["unreviewed"]) == (2, 1, 1)
    assert categories["chat"]["unsupported_sources"] == 1
    assert categories["chat"]["reported_api_usd"] == .1
    assert categories["plan"]["estimated_or_reserved_usd"] == .4
    assert categories["plan"]["reported_api_usd"] == 0
    assert categories["tool"]["measured_duration_seconds"] == 2
    assert categories["tool"]["reported_api_usd"] == 0
    assert categories["research"]["measured_duration_count"] == 0
    assert {(item["kind"], item["evidence_id"]) for item in result["regression_cases"]} == {
        ("chat", answer["id"]), ("tool", run["id"])}
    assert next(item for item in result["recent"] if item["kind"] == "chat")[
        "parent_id"] == chat_id

    with TestClient(create_app(Settings(JARVIS_DATA_DIR=data,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        client.get("/session")
        response = client.get("/quality/overview")
        assert response.status_code == 200
        assert response.json()["categories"]["chat"]["verdicts"]["partial"] == 1

    db.evaluate_chat_answer(chat_id, answer["id"], "achieved", "supported",
                            "Beleg nachgetragen", False, "", 1)
    changed = overview(db)
    assert changed["categories"]["chat"]["regression_cases"] == 0
    assert len(changed["regression_cases"]) == 1
