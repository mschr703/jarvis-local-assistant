"""Nutzerurteile über Rechercheberichte bleiben versioniert und wiederherstellbar."""

import sqlite3

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.research import ResearchStore


def report_at(root, project_context=None):
    store = ResearchStore(Database(root / "jarvis.db"))
    report = store.save("Was untersucht Europa Clipper?", "Europa Clipper", "Fixture",
                        [{"id": 1, "title": "Quelle", "url": "https://example.com/a",
                          "text": "Europa Clipper untersucht den Jupitermond Europa. " * 4,
                          "retrieved_at": "2026-10-27T07:00:00Z"}], [],
                        "Die Mission untersucht Europa.", "Quelle selbst prüfen.", [], "ollama", "fixture",
                        project_context=project_context)
    return store, report


def test_evaluation_conflict_case_and_backup_restore(tmp_path):
    root = tmp_path / "data"
    store, report = report_at(root)
    assert report["evaluation"] is None
    settings = Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        path = f"/research/{report['id']}/evaluation"
        payload = {"verdict": "partial", "source_quality": "unclear",
                   "reason": "Die Quelle belegt nur einen Teil der Frage.",
                   "regression_case": True, "expected_result": "Instrumente aus zweiter Quelle nennen.",
                   "version": 0}
        first = client.put(path, headers=headers, json=payload)
        assert first.status_code == 200, first.text
        assert first.json()["version"] == 1
        assert client.get(f"/research/{report['id']}").json()["evaluation"] == first.json()
        cases = client.get("/research/evaluation-cases").json()
        assert len(cases) == 1 and cases[0]["report_id"] == report["id"]
        assert cases[0]["question"] == report["question"]
        exported = client.get("/research/evaluation-cases/export")
        assert exported.status_code == 200
        snapshot = exported.json()[0]
        assert snapshot["format_version"] == 1
        assert snapshot["evaluation"] == first.json()
        assert snapshot["report"]["question"] == report["question"]
        assert snapshot["report"]["sources"] == report["sources"]
        assert snapshot["report"]["claims"] == report["claims"]
        assert "project_context_json" not in snapshot["report"]
        assert "sources_json" not in snapshot["report"]
        assert client.put(path, headers=headers, json=payload).status_code == 409
        invalid = client.put(path, headers=headers,
                             json={**payload, "verdict": "achieved", "version": 1})
        assert invalid.status_code == 422
        second = client.put(path, headers=headers, json={**payload,
                            "verdict": "achieved", "regression_case": False,
                            "expected_result": "", "version": 1})
        assert second.status_code == 200 and second.json()["version"] == 2
        assert second.json()["expected_result"] == ""
        assert client.get("/research/evaluation-cases").json() == []
        assert client.get("/research/evaluation-cases/export").json() == []
        assert client.put("/research/missing/evaluation", headers=headers, json=payload).status_code == 404
        assert client.put(path, json=payload).status_code == 403
    assert store.get(report["id"])["sources"] == report["sources"]
    archive = tmp_path / "backup.zip"
    assert create_backup(root, archive)["schema"] == 63
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    assert ResearchStore(Database(restored / "jarvis.db")).get(report["id"])["evaluation"] == second.json()


def test_schema_29_report_migrates_with_empty_evaluation(tmp_path):
    root = tmp_path / "data"
    _, report = report_at(root)
    with sqlite3.connect(root / "jarvis.db") as db:
        db.execute("DROP TABLE research_evaluations")
        db.execute("DROP INDEX research_reports_parent")
        db.execute("ALTER TABLE research_reports DROP COLUMN open_point")
        db.execute("ALTER TABLE research_reports DROP COLUMN parent_report_id")
        db.execute("DROP INDEX chats_archive_updated")
        db.execute("ALTER TABLE chats DROP COLUMN archived_at")
        db.execute("DROP TABLE style_profile_revisions")
        db.execute("DROP TABLE chat_compactions")
        db.execute("DROP TABLE draft_named_style_usage")
        db.execute("DROP TABLE named_style_profile_revisions")
        db.execute("DROP TABLE named_style_profiles")
        db.execute("DROP TABLE project_memory_links")
        db.execute("DROP TABLE project_person_merges")
        db.execute("DROP TABLE project_person_aliases")
        db.execute("DROP TABLE project_person_tasks")
        db.execute("DROP TABLE project_people")
        db.execute("ALTER TABLE messages DROP COLUMN context_latest_omitted_id")
        db.execute("ALTER TABLE messages DROP COLUMN context_omitted_count")
        db.execute("ALTER TABLE messages DROP COLUMN context_token_budget")
        db.execute("ALTER TABLE messages DROP COLUMN context_estimated_tokens")
        db.execute("ALTER TABLE messages DROP COLUMN compaction_version")
        db.execute("DROP TABLE learning_sessions")
        db.execute("DROP TABLE memory_proposal_events")
        db.execute("DROP TABLE memory_proposals")
        db.execute("DROP TABLE memory_context_uses")
        db.execute("PRAGMA user_version=29")
    migrated = ResearchStore(Database(root / "jarvis.db")).get(report["id"])
    assert migrated["evaluation"] is None
    with sqlite3.connect(root / "jarvis.db") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 63


def test_export_reflects_latest_case_version_after_restore(tmp_path):
    root = tmp_path / "data"
    store, report = report_at(root, {"project": "privat", "next_step": "C:/PRIVATE-NOTE"})
    store.evaluate(report["id"], "failed", "unsupported", "Beleg fehlt", True,
                   "Beleg ergänzen", 0)
    store.evaluate(report["id"], "partial", "unclear", "Nur ein Teil belegt", True,
                   "Zweite Quelle prüfen", 1)
    archive = tmp_path / "backup.zip"
    create_backup(root, archive)
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    settings = Settings(JARVIS_DATA_DIR=restored, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        assert client.get("/session").status_code == 200
        response = client.get("/research/evaluation-cases/export")
        assert response.status_code == 200, response.text
        exported = response.json()
        assert len(exported) == 1
        assert exported[0]["evaluation"]["version"] == 2
        assert exported[0]["evaluation"]["expected_result"] == "Zweite Quelle prüfen"
        assert exported[0]["report"]["sources"][0]["url"] == "https://example.com/a"
        assert "PRIVATE-NOTE" not in response.text


def test_export_limits_to_newest_hundred_cases(tmp_path):
    store = ResearchStore(Database(tmp_path / "jarvis.db"))
    with store.db.connect() as db:
        for number in range(101):
            report_id = f"r{number:03}"
            db.execute("""INSERT INTO research_reports
                (id,question,search_query,search_provider,sources_json,claims_json,
                 interpretation,uncertainty,errors_json,provider,model,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (report_id, report_id, report_id, "fixture", "[]", "[]", "", "", "[]",
                 "ollama", "fixture", f"{number:03}"))
            db.execute("""INSERT INTO research_evaluations
                (report_id,verdict,source_quality,reason,regression_case,
                 expected_result,version,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (report_id, "failed", "unsupported", "Fehler", 1,
                 "Erwartung", 1, f"{number:03}", f"{number:03}"))
    exported = store.export_evaluation_cases()
    assert len(exported) == 100
    assert exported[0]["report"]["id"] == "r100"
    assert exported[-1]["report"]["id"] == "r001"
