"""Recherche nutzt einen überprüften lokalen Arbeitsstand als eingefrorenen Kontext."""

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.provider import Completion
from jarvis.research import ResearchStore

PUBLIC_TEXT = "Europa Clipper untersucht die eisige Oberflaeche des Jupitermonds Europa. " * 5


def mock_public_research(monkeypatch, prompts):
    monkeypatch.setattr("jarvis.app.search_public_web", lambda _query: (
        [{"title": "Öffentliche Mission", "url": "https://example.com/mission"}], "Fixture-Suche"))
    monkeypatch.setattr("jarvis.app.read_source", lambda url, title: {
        "title": title, "url": url, "text": PUBLIC_TEXT, "media_type": "web",
        "retrieved_at": "2026-10-27T01:00:00+00:00"})

    async def generate(_db, _config, _purpose, messages):
        prompts.append(messages[-1]["content"])
        return Completion("Die öffentliche Quelle betrifft den Projektschritt.",
                          80, 20, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)


def test_project_snapshot_only_with_ollama_and_public_quotes(tmp_path, monkeypatch):
    prompts = []
    mock_public_research(monkeypatch, prompts)
    root = tmp_path / "data"
    app = create_app(Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture"))
    with TestClient(app) as client:
        state = app.state.work_states.create("Atlas", "Prüfung freigegeben",
                                              "Budget noch offen", "Nächsten Entwurf prüfen",
                                              [], [], [])
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        payload = {"question": "Was hilft dem Projekt Atlas?", "project_id": state["id"]}
        assert client.post("/research", json=payload).status_code in {401, 403}
        response = client.post("/research", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        report = response.json()
        context = report["project_context"]
        assert context == {"id": state["id"], "project": "Atlas", "version": 1,
                           "updated_at": state["updated_at"], "confirmed": "Prüfung freigegeben",
                           "open_questions": "Budget noch offen",
                           "next_step": "Nächsten Entwurf prüfen", "truncated": False}
        assert "kein öffentlicher Beleg" in prompts[0]
        assert "Prüfung freigegeben" in prompts[0]
        assert "[1] Europa Clipper" in prompts[0]
        assert len(report["claims"]) == 1
        assert report["claims"][0]["evidence"][0]["quote"] in report["sources"][0]["text"]
        linked = client.get(f"/work-states/{state['id']}/research")
        assert linked.status_code == 200
        assert linked.json() == [{"report_id": report["id"], "question": report["question"],
                                  "interpretation": report["interpretation"],
                                  "provider": "ollama", "model": "fixture",
                                  "created_at": report["created_at"], "source_count": 1,
                                  "project_version": 1}]
        assert client.get("/work-states/missing/research").status_code == 404
        changed = app.state.work_states.update(state["id"], 1, "Atlas", "Neuer Stand",
                                                "Andere Frage", "Anderer Schritt", [], [], [])
        assert changed["version"] == 2
        assert client.get(f"/research/{report['id']}").json()["project_context"] == context
        app.state.work_states.delete(state["id"], 2)
        assert client.get(f"/research/{report['id']}").json()["project_context"] == context
        assert client.get(f"/work-states/{state['id']}/research").status_code == 404
        missing = client.post("/research", headers=headers,
                              json={**payload, "project_id": "00000000-0000-0000-0000-000000000000"})
        assert missing.status_code == 404
    archive = tmp_path / "backup.zip"
    assert create_backup(root, archive)["schema"] == 59
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    assert ResearchStore(Database(restored / "jarvis.db")).get(report["id"]) == report


def test_stale_project_file_and_cloud_context_are_rejected(tmp_path, monkeypatch):
    prompts = []
    mock_public_research(monkeypatch, prompts)
    root = tmp_path / "data"
    app = create_app(Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture"))
    with TestClient(app) as client:
        folder_path = tmp_path / "documents"
        folder_path.mkdir()
        source = folder_path / "stand.md"
        source.write_text("Bestätigter Projektstand", encoding="utf-8")
        folder = app.state.documents.add_folder(str(folder_path), "local_only")
        app.state.documents.index_folder(folder["id"])
        document = app.state.documents.documents()[0]
        state = app.state.work_states.create("Atlas", "Prüfung freigegeben", "", "Weiter",
                                              [document["id"]], [], [])
        source.write_text("Später geändert", encoding="utf-8")
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        response = client.post("/research", headers=headers,
                               json={"question": "Was ist neu?", "project_id": state["id"]})
        assert response.status_code == 409
        assert "Arbeitsstand-Dateien" in response.json()["detail"]
        assert prompts == []
        assert client.get("/research").json() == []

    cloud = create_app(Settings(JARVIS_DATA_DIR=tmp_path / "cloud", JARVIS_PROVIDER="openrouter",
                                JARVIS_OPENROUTER_API_KEY="fixture", JARVIS_OPENROUTER_MODEL="fixture"))
    with TestClient(cloud) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        denied = client.post("/research", headers=headers, json={
            "question": "Was ist neu?", "project_id": state["id"], "allow_cloud": True})
        assert denied.status_code == 422
        assert "nur mit lokalem Ollama" in denied.json()["detail"]


def test_schema_28_report_migrates_without_invented_project_context(tmp_path):
    path = tmp_path / "jarvis.db"
    db = Database(path)
    report = ResearchStore(db).save("Eine Frage", "Eine Suche", "Fixture", [], [], "", "", [],
                                    "ollama", "fixture")
    with db.connect() as connection:
        connection.execute("DROP TABLE research_evaluations")
        connection.execute("DROP INDEX research_reports_parent")
        connection.execute("ALTER TABLE research_reports DROP COLUMN open_point")
        connection.execute("ALTER TABLE research_reports DROP COLUMN parent_report_id")
        connection.execute("ALTER TABLE research_reports DROP COLUMN project_context_json")
        connection.execute("DROP INDEX chats_archive_updated")
        connection.execute("ALTER TABLE chats DROP COLUMN archived_at")
        connection.execute("DROP TABLE style_profile_revisions")
        connection.execute("DROP TABLE chat_compactions")
        connection.execute("DROP TABLE draft_named_style_usage")
        connection.execute("DROP TABLE named_style_profile_revisions")
        connection.execute("DROP TABLE named_style_profiles")
        connection.execute("DROP TABLE project_memory_links")
        connection.execute("DROP TABLE project_person_merges")
        connection.execute("DROP TABLE project_person_aliases")
        connection.execute("DROP TABLE project_person_tasks")
        connection.execute("DROP TABLE project_people")
        connection.execute("ALTER TABLE messages DROP COLUMN context_latest_omitted_id")
        connection.execute("ALTER TABLE messages DROP COLUMN context_omitted_count")
        connection.execute("ALTER TABLE messages DROP COLUMN context_token_budget")
        connection.execute("ALTER TABLE messages DROP COLUMN context_estimated_tokens")
        connection.execute("ALTER TABLE messages DROP COLUMN compaction_version")
        connection.execute("DROP TABLE learning_sessions")
        connection.execute("DROP TABLE memory_proposal_events")
        connection.execute("DROP TABLE memory_proposals")
        connection.execute("DROP TABLE memory_context_uses")
        connection.execute("PRAGMA user_version=28")
    migrated = ResearchStore(Database(path)).get(report["id"])
    assert migrated["project_context"] is None
    assert migrated["id"] == report["id"]


def test_project_research_filters_by_explicit_id_and_limits_to_fifty(tmp_path):
    store = ResearchStore(Database(tmp_path / "jarvis.db"))
    for number in range(51):
        store.save(f"Frage {number}", "", "Fixture", [], [], "", "", [], "ollama", "fixture",
                   {"id": "projekt-a", "version": 2})
    other = store.save("Anderes Projekt", "", "Fixture", [], [], "", "", [],
                       "ollama", "fixture", {"id": "projekt-b", "version": 1})
    unlinked = store.save("Ohne Projekt", "", "Fixture", [], [], "", "", [],
                          "ollama", "fixture")
    linked = store.for_project("projekt-a")
    assert len(linked) == 50
    assert linked[0]["question"] == "Frage 50"
    assert all(item["project_version"] == 2 for item in linked)
    assert other["id"] not in {item["report_id"] for item in linked}
    assert unlinked["id"] not in {item["report_id"] for item in linked}
