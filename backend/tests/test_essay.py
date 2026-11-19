"""Gegliederte lokale Essays bleiben begrenzt, editierbar und wiederherstellbar."""

import re
import sqlite3

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.drafts import DraftStore
from jarvis.provider import Completion, ProviderError


def headers(client):
    return {"x-jarvis-csrf": client.get("/session").json()["csrf"]}


def set_style(client, token):
    response = client.put("/style/profile", headers=token,
                          json={"description": "Ruhig, klar und mit kurzen Sätzen.",
                                "policy": "local_only"})
    assert response.status_code == 200, response.text


def payload(**extra):
    return {"kind": "essay", "brief": "Erkläre die Europa-Clipper-Mission sachlich.",
            "word_target": 300, "outline": ["Einleitung", "Instrumente", "Fazit"], **extra}


def test_essay_uses_selected_style_source_and_survives_edit_export_restore(tmp_path, monkeypatch):
    calls = []

    async def generate(_db, config, purpose, messages):
        prompt = messages[-1]["content"]
        calls.append(prompt)
        assert config.provider == "ollama" and config.max_output_tokens <= 1024
        assert purpose == "draft" and "Schreibprobe 1" in prompt
        assert "Quelle [1]" in prompt and "Europa Clipper" in prompt
        index = int(re.search(r"Jetzt nur Abschnitt (\d+)", prompt).group(1))
        return Completion((f"Teil {index}: Die Mission untersucht Europa mit Instrumenten. [1] " * 15),
                          60, 45, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    root = tmp_path / "data"
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "mission.txt").write_text(
        "Europa Clipper untersucht den Jupitermond Europa mit wissenschaftlichen Instrumenten. " * 5,
        encoding="utf-8")
    settings = Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        token = headers(client)
        set_style(client, token)
        sample = client.post("/style/samples", headers=token, json={
            "title": "Mein Bericht", "content": "Ich schreibe sachlich und ohne Floskeln.",
            "policy": "local_only"}).json()
        folder = client.post("/folders", headers=token, json={
            "path": str(source_dir), "policy": "local_only"}).json()
        assert client.post(f"/folders/{folder['id']}/index", headers=token).status_code == 200
        passage = client.get("/search?q=Europa").json()[0]["passage_id"]
        response = client.post("/drafts/generate", headers=token, json=payload(
            passage_ids=[passage], style_sample_ids=[sample["id"]]))
        assert response.status_code == 200, response.text
        draft = response.json()
        assert draft["kind"] == "essay" and draft["status"] == "editing"
        assert draft["word_target"] == 300 and draft["outline"] == ["Einleitung", "Instrumente", "Fazit"]
        assert draft["actual_words"] > 20 and len(draft["sources"]) == 1
        assert draft["rule_version"] is not None
        assert "## Einleitung" in draft["content"] and "## Fazit" in draft["content"]
        assert len(calls) == 3
        assert "Bisheriger Text" in calls[1] and "Teil 1" in calls[1]
        assert client.get(f"/drafts/{draft['id']}").json() == draft
        edited = client.patch(f"/drafts/{draft['id']}", headers=token, json={
            "content": draft["content"] + "\n\nEigene Korrektur.", "expected_version": 1})
        assert edited.status_code == 200 and edited.json()["actual_words"] > draft["actual_words"]
        saved = client.post(f"/drafts/{draft['id']}/export", headers=token, json={
            "format": "md", "expected_version": 2})
        assert saved.status_code == 200, saved.text
        assert "Eigene Korrektur" in (root / "outputs" / f"{draft['id']}.md").read_text(encoding="utf-8")
        assert client.post(f"/drafts/{draft['id']}/export", headers=token, json={
            "format": "eml", "expected_version": 2}).status_code == 409
    with TestClient(create_app(settings)) as client:
        client.get("/session")
        assert client.get(f"/drafts/{draft['id']}").json()["word_target"] == 300
    archive = tmp_path / "essay.zip"
    assert create_backup(root, archive)["schema"] == 62
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    recovered = DraftStore(Database(restored / "jarvis.db"), restored).get_draft(draft["id"])
    assert recovered["outline"] == draft["outline"]
    assert recovered["word_target"] == 300
    assert "Eigene Korrektur" in recovered["content"]
    assert (restored / "outputs" / f"{draft['id']}.md").exists()


def test_essay_partial_after_later_model_error(tmp_path, monkeypatch):
    calls = 0

    async def generate(*_args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ProviderError("upstream", "Ollama unterbrochen")
        return Completion("Ein erster Abschnitt mit eigenem Text.", 20, 12, 0,
                          "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    root = tmp_path / "data"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture"))) as client:
        token = headers(client)
        set_style(client, token)
        response = client.post("/drafts/generate", headers=token, json=payload())
        assert response.status_code == 200, response.text
        draft = response.json()
        assert draft["status"] == "partial" and draft["word_target"] == 300
        assert "## Einleitung" in draft["content"] and "## Instrumente" not in draft["content"]
        assert "Ollama unterbrochen" in draft["uncertainty"]
        assert calls == 2
        assert client.get(f"/drafts/{draft['id']}").json() == draft
        assert client.patch(f"/drafts/{draft['id']}", headers=token, json={
            "content": draft["content"] + " Ergänzung.", "expected_version": 1}).status_code == 200


def test_essay_input_style_and_cloud_boundaries(tmp_path, monkeypatch):
    async def forbidden(*_args):
        raise AssertionError("Ungültiger Auftrag darf kein Modell aufrufen")

    monkeypatch.setattr("jarvis.app.generate_text", forbidden)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path / "local",
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        token = headers(client)
        assert client.post("/drafts/generate", headers=token, json=payload()).status_code == 422
        set_style(client, token)
        assert client.post("/drafts/generate", headers=token,
                           json=payload(word_target=299)).status_code == 422
        assert client.post("/drafts/generate", headers=token,
                           json=payload(outline=["Gleich", "Gleich"])).status_code == 422
        assert client.post("/drafts/generate", headers=token,
                           json=payload(outline=["Nur eins"])).status_code == 422
        assert client.post("/drafts/generate", headers=token,
                           json=payload(word_target=900,
                                        outline=["Einleitung", "Fazit"])).status_code == 422
        assert client.post("/drafts/generate", headers=token, json={
            "kind": "text", "brief": "Text", "word_target": 300}).status_code == 422
    cloud = Settings(JARVIS_DATA_DIR=tmp_path / "cloud", JARVIS_PROVIDER="openrouter",
                     JARVIS_OPENROUTER_API_KEY="fixture", JARVIS_OPENROUTER_MODEL="fixture")
    with TestClient(create_app(cloud)) as client:
        token = headers(client)
        assert client.post("/drafts/generate", headers=token,
                           json=payload(allow_cloud=True)).status_code == 422


def test_essay_four_sections_never_exceed_four_calls(tmp_path, monkeypatch):
    calls = []

    async def generate(_db, _config, _purpose, messages):
        calls.append(messages[-1]["content"])
        return Completion("Ein kurzer Abschnitt.", 20, 10, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture"))) as client:
        token = headers(client)
        set_style(client, token)
        response = client.post("/drafts/generate", headers=token, json=payload(
            outline=["Einleitung", "Analyse", "Beispiele", "Fazit"], word_target=900))
        assert response.status_code == 200, response.text
        assert response.json()["word_target"] == 900
        assert len(calls) == 4
        assert "Wortziel" in response.json()["uncertainty"]


def test_short_essay_uses_only_remaining_calls_and_preserves_first_text(tmp_path, monkeypatch):
    calls = []
    root = tmp_path / "data"
    store = DraftStore(Database(root / "jarvis.db"), root)
    previous = store.create_draft("essay", "", "Alt", "Ein Text.", [], "", "klar",
                                  word_target=300, outline=["Anfang", "Ende"])
    store.add_essay_feedback(previous["id"], 1, "Bitte kurze und klare Sätze schreiben.")
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "fakten.txt").write_text(
        "Europa ist ein Mond des Jupiter. " * 8, encoding="utf-8")

    async def generate(_db, config, _purpose, messages):
        calls.append(messages[-1]["content"])
        assert config.max_output_tokens <= 700
        if len(calls) <= 2:
            return Completion(f"Erstfassung {len(calls)} bleibt erhalten.", 20, 10, 0,
                              "local_no_api_charge")
        return Completion("Ergänzung " * 120, 60, 180, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        csrf = headers(client)
        set_style(client, csrf)
        folder = client.post("/folders", headers=csrf,
                             json={"path": str(source_dir), "policy": "local_only"}).json()
        assert client.post(f"/folders/{folder['id']}/index", headers=csrf).status_code == 200
        passage = client.get("/search?q=Europa").json()[0]["passage_id"]
        response = client.post("/drafts/generate", headers=csrf, json=payload(
            outline=["Einleitung", "Schluss"], passage_ids=[passage]))
        assert response.status_code == 200, response.text
        draft = response.json()
        assert len(calls) == 4 and all("Ergänze nur Abschnitt" in call for call in calls[2:])
        assert "Quelle [1]" in calls[2] and "Europa ist ein Mond" in calls[2]
        assert "Bitte kurze und klare Sätze" in calls[2]
        assert "Erstfassung 1 bleibt erhalten." in draft["content"]
        assert "Erstfassung 2 bleibt erhalten." in draft["content"]
        assert draft["actual_words"] >= 240
        assert "2 begrenzte Ergänzungsaufrufe" in draft["uncertainty"]
        assert "Modellaufrufe: 4 von höchstens 4 versucht." in draft["uncertainty"]
        assert draft["status"] == "editing"


def test_sufficient_essay_does_not_use_continuation(tmp_path, monkeypatch):
    calls = 0

    async def generate(*_args):
        nonlocal calls
        calls += 1
        return Completion("Wort " * 150, 20, 160, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        csrf = headers(client)
        set_style(client, csrf)
        draft = client.post("/drafts/generate", headers=csrf, json=payload(
            outline=["Einleitung", "Schluss"])).json()
        assert calls == 2 and draft["actual_words"] >= 300
        assert "Ergänzungsaufrufe" not in draft["uncertainty"]


def test_failed_length_continuation_keeps_complete_outline(tmp_path, monkeypatch):
    calls = 0

    async def generate(*_args):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise ProviderError("upstream", "Ergänzung fehlgeschlagen")
        return Completion(f"Kurzer Ersttext {calls}.", 20, 10, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        csrf = headers(client)
        set_style(client, csrf)
        response = client.post("/drafts/generate", headers=csrf, json=payload(
            outline=["Einleitung", "Schluss"]))
        assert response.status_code == 200, response.text
        draft = response.json()
        assert calls == 3 and draft["status"] == "partial"
        assert "## Einleitung" in draft["content"] and "## Schluss" in draft["content"]
        assert "Kurzer Ersttext 1." in draft["content"]
        assert "Kurzer Ersttext 2." in draft["content"]
        assert "Ergänzung fehlgeschlagen" in draft["uncertainty"]


def test_schema_31_migrates_existing_draft_without_essay_metadata(tmp_path):
    path = tmp_path / "jarvis.db"
    store = DraftStore(Database(path), tmp_path)
    draft = store.create_draft("text", "", "Kurz", "Ein Text.", [], "", "")
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE essay_metadata")
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
        db.execute("PRAGMA user_version=31")
    migrated = DraftStore(Database(path), tmp_path).get_draft(draft["id"])
    assert migrated["word_target"] is None and migrated["outline"] == []
    assert migrated["actual_words"] == 2
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 62


def test_long_essay_word_and_outline_boundaries(tmp_path, monkeypatch):
    async def forbidden(*_args):
        raise AssertionError("Ungültiger Essay darf kein Modell aufrufen")

    monkeypatch.setattr("jarvis.app.generate_text", forbidden)
    five = ["Einleitung", "Analyse", "Beispiele", "Diskussion", "Fazit"]
    six = ["Einleitung", "Analyse", "Beispiele", "Diskussion", "Folgen", "Fazit"]
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        token = headers(client)
        set_style(client, token)
        for target, outline in ((900, five), (901, five[:4]), (1051, five),
                                (1200, six + ["Anhang"]), (1201, six)):
            result = client.post("/drafts/generate", headers=token,
                                 json=payload(word_target=target, outline=outline))
            assert result.status_code == 422, (target, result.text)


def test_long_essay_accepts_901_and_1050_with_five_sections(tmp_path, monkeypatch):
    calls = 0

    async def generate(*_args):
        nonlocal calls
        calls += 1
        return Completion("Wort " * 250, 30, 300, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    five = ["Einleitung", "Analyse", "Beispiele", "Diskussion", "Fazit"]
    six = ["Einleitung", "Analyse", "Beispiele", "Diskussion", "Folgen", "Fazit"]
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        token = headers(client)
        set_style(client, token)
        for target, outline in ((900, five[:4]), (901, five), (1050, five),
                                (1051, six), (1200, six)):
            before = calls
            response = client.post("/drafts/generate", headers=token, json=payload(
                word_target=target, outline=outline))
            assert response.status_code == 200, (target, response.text)
            assert response.json()["word_target"] == target
            assert len(response.json()["outline"]) == len(outline)
            assert calls - before == len(outline)


def test_long_essay_uses_at_most_eight_calls_with_bounded_context(tmp_path, monkeypatch):
    calls = []

    async def generate(_db, config, purpose, messages):
        calls.append((config.max_output_tokens, messages[-1]["content"]))
        assert purpose == "draft" and config.provider == "ollama"
        return Completion("## Zusätzliche Modellüberschrift\n\nEin kurzer sachlicher Absatz.", 30, 10, 0,
                          "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    source = tmp_path / "sources"
    source.mkdir()
    (source / "fakten.txt").write_text("Eine belegte lokale Angabe zur Planung. " * 8,
                                      encoding="utf-8")
    six = ["Einleitung", "Ziele", "Material", "Ablauf", "Risiken", "Fazit"]
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path / "data",
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        token = headers(client)
        set_style(client, token)
        assert client.put("/style/profiles/essay", headers=token, json={
            "description": "Essays mit kurzen klaren Sätzen.",
            "policy": "local_only"}).status_code == 200
        folder = client.post("/folders", headers=token, json={
            "path": str(source), "policy": "local_only"}).json()
        assert client.post(f"/folders/{folder['id']}/index", headers=token).status_code == 200
        passage = client.get("/search?q=Planung").json()[0]["passage_id"]
        result = client.post("/drafts/generate", headers=token, json=payload(
            word_target=1200, outline=six, passage_ids=[passage]))
        assert result.status_code == 200, result.text
        draft = result.json()
        assert draft["word_target"] == 1200 and draft["outline"] == six
        assert draft["style_snapshot"] == "Essays mit kurzen klaren Sätzen."
        assert draft["content"].count("## ") == 6 and draft["status"] == "editing"
        assert len(calls) == 8 and all(cap <= 900 for cap, _ in calls)
        assert all("Essays mit kurzen klaren Sätzen." in prompt for _, prompt in calls)
        assert all("Quelle [1]" in prompt for _, prompt in calls)
        assert all("Ergänze nur Abschnitt" in prompt for _, prompt in calls[6:])
        assert "2 begrenzte Ergänzungsaufrufe" in draft["uncertainty"]
        assert "Modellaufrufe: 8 von höchstens 8 versucht." in draft["uncertainty"]
        assert "deutlich unter dem Wortziel" in draft["uncertainty"]
        edited = client.patch(f"/drafts/{draft['id']}", headers=token, json={
            "content": draft["content"] + "\n\nEigene Korrektur.", "expected_version": 1})
        assert edited.status_code == 200
        exported = client.post(f"/drafts/{draft['id']}/export", headers=token, json={
            "format": "md", "expected_version": 2})
        assert exported.status_code == 200
        assert "Eigene Korrektur." in (tmp_path / "data" / "outputs" /
                                         f"{draft['id']}.md").read_text(encoding="utf-8")


def test_long_essay_continuation_error_preserves_six_sections(tmp_path, monkeypatch):
    calls = 0

    async def generate(*_args):
        nonlocal calls
        calls += 1
        if calls == 7:
            raise ProviderError("upstream", "Ollama brach bei der Ergänzung ab")
        return Completion(f"Vorhandener Abschnitt {calls} bleibt.", 20, 10, 0,
                          "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    six = ["Einleitung", "Ziele", "Material", "Ablauf", "Risiken", "Fazit"]
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        token = headers(client)
        set_style(client, token)
        response = client.post("/drafts/generate", headers=token,
                               json=payload(word_target=1200, outline=six))
        assert response.status_code == 200, response.text
        draft = response.json()
        assert calls == 7 and draft["status"] == "partial"
        assert "Modellaufrufe: 7 von höchstens 8 versucht." in draft["uncertainty"]
        assert draft["content"].count("## ") == 6
        assert "Vorhandener Abschnitt 1 bleibt." in draft["content"]
        assert "Vorhandener Abschnitt 6 bleibt." in draft["content"]
        assert "Ollama brach" in draft["uncertainty"]


def test_schema35_essay_metadata_migrates_to_longer_limit(tmp_path):
    path = tmp_path / "jarvis.db"
    store = DraftStore(Database(path), tmp_path)
    old = store.create_draft("essay", "", "Alt", "## Anfang\n\nText.", [], "", "klar",
                             word_target=300, outline=["Anfang", "Schluss"])
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE essay_metadata_old (
                   draft_id TEXT PRIMARY KEY REFERENCES drafts(id) ON DELETE CASCADE,
                   word_target INTEGER NOT NULL CHECK(word_target BETWEEN 300 AND 900),
                   outline_json TEXT NOT NULL)""")
        db.execute("INSERT INTO essay_metadata_old SELECT * FROM essay_metadata")
        db.execute("DROP TABLE essay_metadata")
        db.execute("ALTER TABLE essay_metadata_old RENAME TO essay_metadata")
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
        db.execute("PRAGMA user_version=35")
    migrated = DraftStore(Database(path), tmp_path)
    assert migrated.get_draft(old["id"])["word_target"] == 300
    six = ["Einleitung", "Ziele", "Material", "Ablauf", "Risiken", "Fazit"]
    long = migrated.create_draft("essay", "", "Lang", "## Einleitung\n\nText.", [], "", "klar",
                                 word_target=1200, outline=six)
    assert long["word_target"] == 1200 and long["outline"] == six
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 62
    archive = tmp_path.parent / f"{tmp_path.name}-long-essay.zip"
    assert create_backup(tmp_path, archive)["schema"] == 62
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    recovered = DraftStore(Database(restored / "jarvis.db"), restored).get_draft(long["id"])
    assert recovered["word_target"] == 1200 and recovered["outline"] == six
