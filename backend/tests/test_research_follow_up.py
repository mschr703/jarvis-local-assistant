"""Ein begrenzter Nachlauf speichert Herkunft und Teilerfolg getrennt vom Ursprung."""

import sqlite3

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.provider import Completion
from jarvis.research import ResearchStore


def original(root):
    store = ResearchStore(Database(root / "jarvis.db"))
    report = store.save(
        "Was untersucht Europa Clipper?", "Europa Clipper", "Fixture",
        [{"id": 1, "url": "https://example.org/old", "title": "Ursprung",
          "text": "Die Mission untersucht den Jupitermond Europa. " * 5,
          "retrieved_at": "2026-10-27T07:00:00Z"}], [],
        "Die Mission untersucht Europa.", "Ein Instrument ist noch offen.", [],
        "ollama", "fixture")
    return store, report


def csrf(client):
    return {"x-jarvis-csrf": client.get("/session").json()["csrf"]}


def test_follow_up_one_new_source_one_local_call_and_restore(tmp_path, monkeypatch):
    root = tmp_path / "data"
    _, parent = original(root)
    searched, read, modeled = [], [], []

    def search(query):
        searched.append(query)
        return ([{"title": "Alt", "url": "https://example.org/old"},
                 {"title": "Neu", "url": "https://example.org/new"},
                 {"title": "Zu viel", "url": "https://example.org/third"}], "DuckDuckGo HTML")

    def source(url, title):
        read.append(url)
        return {"url": url, "title": title,
                "text": "Das Instrument trägt eine Kamera für die Untersuchung des Jupitermondes Europa. " * 4,
                "retrieved_at": "2026-10-27T08:00:00Z", "media_type": "web"}

    async def generate(_db, config, purpose, messages):
        modeled.append((config.provider, purpose, messages[-1]["content"]))
        return Completion("Die neue Quelle nennt eine Kamera.", 40, 20, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.search_public_web", search)
    monkeypatch.setattr("jarvis.app.read_source", source)
    monkeypatch.setattr("jarvis.app.generate_text", generate)
    settings = Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        path = f"/research/{parent['id']}/follow-up"
        headers = csrf(client)
        assert client.post(path, json={"open_point": "Welches Instrument?",
                                       "search_query": "Europa Clipper Kamera"}).status_code == 403
        response = client.post(path, headers=headers, json={
            "open_point": "Welches Instrument?", "search_query": "Europa Clipper Kamera"})
        assert response.status_code == 200, response.text
        follow_up = response.json()
        assert follow_up["parent_report_id"] == parent["id"]
        assert follow_up["open_point"] == "Welches Instrument?"
        assert follow_up["search_query"] == "Europa Clipper Kamera"
        assert len(follow_up["sources"]) == len(follow_up["claims"]) == 1
        assert follow_up["sources"][0]["found_by_query"] == "Europa Clipper Kamera"
        assert follow_up["interpretation"] == "Die neue Quelle nennt eine Kamera."
        assert client.get(f"/research/{parent['id']}").json() == parent
        assert client.get(f"/research/{follow_up['id']}").json() == follow_up
    assert searched == ["Europa Clipper Kamera"]
    assert read == ["https://example.org/new"]
    assert len(modeled) == 1 and modeled[0][:2] == ("ollama", "research")
    assert "Das Instrument trägt" in modeled[0][2]
    with TestClient(create_app(settings)) as client:
        assert client.get("/session").status_code == 200
        assert client.get(f"/research/{parent['id']}").json() == parent
        assert client.get(f"/research/{follow_up['id']}").json() == follow_up
    archive = tmp_path / "research.zip"
    assert create_backup(root, archive)["schema"] == 52
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    restored_store = ResearchStore(Database(restored / "jarvis.db"))
    assert restored_store.get(parent["id"]) == parent
    assert restored_store.get(follow_up["id"]) == follow_up


def test_follow_up_failed_search_is_separate_and_skips_model(tmp_path, monkeypatch):
    root = tmp_path / "data"
    _, parent = original(root)
    monkeypatch.setattr("jarvis.app.search_public_web", lambda _query: (_ for _ in ()).throw(
        ValueError("Suchdienst nicht erreichbar")))

    async def should_not_model(*_args):
        raise AssertionError("Ohne neue Quelle darf kein Modellaufruf erfolgen")

    monkeypatch.setattr("jarvis.app.generate_text", should_not_model)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture"))) as client:
        response = client.post(f"/research/{parent['id']}/follow-up", headers=csrf(client), json={
            "open_point": "Welches Instrument?", "search_query": "Europa Clipper Kamera"})
        assert response.status_code == 200, response.text
        follow_up = response.json()
        assert follow_up["parent_report_id"] == parent["id"]
        assert follow_up["sources"] == [] and follow_up["interpretation"] == ""
        assert "Suchdienst nicht erreichbar" in follow_up["errors"][0]
        assert client.get(f"/research/{parent['id']}").json() == parent


def test_follow_up_model_timeout_keeps_source_and_validates_inputs(tmp_path, monkeypatch):
    root = tmp_path / "data"
    _, parent = original(root)
    monkeypatch.setattr("jarvis.app.search_public_web", lambda _query: (
        [{"title": "Neu", "url": "https://example.org/new"}], "Fixture"))
    monkeypatch.setattr("jarvis.app.read_source", lambda url, title: {
        "url": url, "title": title,
        "text": "Das Instrument trägt eine Kamera für die Untersuchung des Jupitermondes Europa. " * 4,
        "retrieved_at": "2026-10-27T08:00:00Z"})

    async def timeout(*_args):
        raise TimeoutError

    monkeypatch.setattr("jarvis.app.generate_text", timeout)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture"))) as client:
        path = f"/research/{parent['id']}/follow-up"
        headers = csrf(client)
        payload = {"open_point": "Welches Instrument?", "search_query": "Europa Clipper Kamera"}
        assert client.post("/research/missing/follow-up", headers=headers, json=payload).status_code == 404
        assert client.post(path, headers=headers, json={**payload,
                           "search_query": "     "}).status_code == 422
        assert client.post(path, headers=headers, json={**payload,
                           "source_urls": ["https://example.org"]}).status_code == 422
        response = client.post(path, headers=headers, json=payload)
        assert response.status_code == 200, response.text
        follow_up = response.json()
        assert len(follow_up["sources"]) == 1
        assert len(follow_up["claims"]) == 1
        assert follow_up["interpretation"] == ""
        assert "90 Sekunden" in follow_up["errors"][0]
        assert client.get(f"/research/{parent['id']}").json() == parent


def test_follow_up_refuses_cloud_provider(tmp_path, monkeypatch):
    root = tmp_path / "data"
    _, parent = original(root)
    monkeypatch.setattr("jarvis.app.search_public_web", lambda _query: (_ for _ in ()).throw(
        AssertionError("Cloud-Pfad darf nicht suchen")))
    settings = Settings(JARVIS_DATA_DIR=root, JARVIS_PROVIDER="openrouter",
                        JARVIS_OPENROUTER_API_KEY="fixture", JARVIS_OPENROUTER_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        response = client.post(f"/research/{parent['id']}/follow-up", headers=csrf(client), json={
            "open_point": "Welches Instrument?", "search_query": "Europa Clipper Kamera"})
        assert response.status_code == 422
        assert client.get(f"/research/{parent['id']}").json() == parent


def test_schema_30_migrates_reports_without_follow_up(tmp_path):
    root = tmp_path / "data"
    _, parent = original(root)
    with sqlite3.connect(root / "jarvis.db") as db:
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
        db.execute("PRAGMA user_version=30")
    migrated = ResearchStore(Database(root / "jarvis.db")).get(parent["id"])
    assert migrated["parent_report_id"] is None and migrated["open_point"] is None
    assert migrated["sources"] == parent["sources"]
    with sqlite3.connect(root / "jarvis.db") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 52


def test_direct_follow_ups_only_and_restore(tmp_path):
    root = tmp_path / "data"
    store, parent = original(root)
    another = store.save("Andere Recherche", "Andere Suche", "Fixture", [], [], "", "", [],
                         "ollama", "fixture")
    child = store.save("Welche Kamera?", "Europa Clipper Kamera", "Fixture",
                       [{"id": 1, "url": "https://example.org/camera", "title": "Kamera",
                         "text": "Die Kamera untersucht Europa. " * 6,
                         "retrieved_at": "2026-10-27T08:00:00Z"}], [],
                       "Die Kamera wird genannt.", "Quelle prüfen.", [], "ollama", "fixture",
                       parent_report_id=parent["id"], open_point="Welche Kamera?")
    failed = store.save("Welche Antenne?", "Europa Clipper Antenne", "Suche fehlgeschlagen",
                        [], [], "", "Kein neuer Beleg.", ["Suchdienst nicht erreichbar"],
                        "ollama", "fixture", parent_report_id=parent["id"],
                        open_point="Welche Antenne?")
    grandchild = store.save("Welche Frequenz?", "Kamera Frequenz", "Fixture", [], [], "", "", [],
                            "ollama", "fixture", parent_report_id=child["id"],
                            open_point="Welche Frequenz?")
    summaries = store.follow_ups(parent["id"])
    assert [item["report_id"] for item in summaries] == [failed["id"], child["id"]]
    assert summaries[0]["source_count"] == 0 and summaries[0]["has_errors"]
    assert not summaries[0]["has_model_answer"]
    assert summaries[1]["source_count"] == 1 and summaries[1]["has_model_answer"]
    assert not summaries[1]["has_errors"]
    assert store.follow_ups(child["id"])[0]["report_id"] == grandchild["id"]
    assert store.follow_ups(another["id"]) == []
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture"))) as client:
        assert client.get("/session").status_code == 200
        assert client.get(f"/research/{parent['id']}/follow-ups").json() == summaries
        assert client.get("/research/missing/follow-ups").status_code == 404
    archive = tmp_path / "followups.zip"
    create_backup(root, archive)
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    assert ResearchStore(Database(restored / "jarvis.db")).follow_ups(parent["id"]) == summaries


def test_follow_up_list_is_stably_limited_to_fifty(tmp_path):
    store, parent = original(tmp_path / "data")
    with store.db.connect() as db:
        for number in range(51):
            db.execute("""INSERT INTO research_reports
                (id,question,search_query,search_provider,sources_json,claims_json,
                 interpretation,uncertainty,errors_json,provider,model,created_at,
                 parent_report_id,open_point)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (f"child-{number:02}", f"Frage {number}", f"Suche {number}", "Fixture",
                 "[]", "[]", "", "", "[]", "ollama", "fixture",
                 "2026-10-27T09:00:00Z", parent["id"], f"Punkt {number}"))
    results = store.follow_ups(parent["id"])
    assert len(results) == 50
    assert results[0]["report_id"] == "child-50"
    assert results[-1]["report_id"] == "child-01"
