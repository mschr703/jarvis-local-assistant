"""Unified local search returns current, cited records from three existing stores."""

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.source_preferences import SourcePreferences
from jarvis.tasks import TaskStore
from jarvis.unified_search import search
from jarvis.work_states import WorkStateStore


def sample(tmp_path):
    root = tmp_path / "freigabe"
    root.mkdir()
    file = root / "Vertrag_Orion.md"
    file.write_text("Projekt Orion: Mietvertrag für die neue Wohnung.", encoding="utf-8")
    db = Database(tmp_path / "data" / "jarvis.db")
    docs = DocumentStore(db)
    folder = docs.add_folder(str(root), "local_only")
    assert docs.index_folder(folder["id"])["indexed"] == 1
    document = docs.documents()[0]
    memory = docs.add_memory("Orion braucht die Wohnungsunterlagen bis Freitag.",
                             "Gespräch vom 2. Mai", "local_only")
    states = WorkStateStore(db, docs, TaskStore(db))
    project = states.create("Projekt Orion", "Vertrag erhalten", "Kosten prüfen",
                            "Unterlagen abgleichen", [], [], [])
    return db, docs, states, folder, document, memory, project, file


def test_search_combines_sources_with_exact_references(tmp_path):
    db, docs, _, _, document, memory, project, file = sample(tmp_path)
    result = search(db, "Was ist über Orion bekannt?")
    assert result["counts"] == {"document": 1, "memory": 1, "project": 1}
    assert not result["limited"]
    by_kind = {item["kind"]: item for item in result["results"]}
    found = by_kind["document"]
    assert found["id"] == document["id"] and found["source"] == str(file.resolve())
    assert found["sha256"] == document["sha256"]
    assert found["passage_id"] and "Mietvertrag" in found["excerpt"]
    assert found["policy"] == "local_only"
    assert by_kind["memory"]["id"] == memory["id"]
    assert by_kind["memory"]["source"] == "Gespräch vom 2. Mai"
    assert by_kind["project"]["id"] == project["id"]
    assert by_kind["project"]["version"] == 1
    # A filename is findable even when its exact spelling is absent from the passage.
    assert search(db, "Vertrag_Orion")["counts"]["document"] == 1
    assert docs.get_document(document["id"])["status"] == "indexed"


def test_search_reflects_updates_exclusion_and_deletion(tmp_path):
    db, docs, states, folder, _, memory, project, file = sample(tmp_path)
    file.write_text("Neuer Wohnungsvertrag für Projekt Vega.", encoding="utf-8")
    assert docs.index_folder(folder["id"])["indexed"] == 1
    docs.update_memory(memory["id"], "Vega braucht neue Unterlagen.",
                       "Korrigiertes Gespräch", "local_only")
    project = states.update(project["id"], project["version"], "Projekt Vega",
                            "Neue Aufgabe", "", "Unterlagen abgleichen", [], [], [])
    assert search(db, "Mietvertrag")["counts"]["document"] == 0
    file.rename(file.with_name("Vertrag_Vega.md"))
    docs.index_folder(folder["id"])
    assert search(db, "Orion")["counts"] == {"document": 0, "memory": 0, "project": 0}
    current = search(db, "Vega")
    assert current["counts"] == {"document": 1, "memory": 1, "project": 1}
    current_document = next(item for item in current["results"] if item["kind"] == "document")
    docs.set_excluded(current_document["document_id"], True)
    docs.delete_memory(memory["id"])
    states.delete(project["id"], project["version"])
    assert search(db, "Vega")["results"] == []


def test_search_finds_unicode_filename_without_matching_content(tmp_path):
    db, docs, _, folder, _, _, _, file = sample(tmp_path)
    file.with_name("Übersicht.md").write_text("Nur allgemeine Notizen.", encoding="utf-8")
    assert docs.index_folder(folder["id"])["indexed"] == 1
    found = search(db, "übersicht")
    assert found["counts"]["document"] == 1
    assert found["results"][0]["source"].endswith("Übersicht.md")


def test_search_is_bounded_and_rejects_empty_words(tmp_path, monkeypatch):
    db, docs, _, _, _, _, _, _ = sample(tmp_path)
    docs.add_memory("Orion zweiter Hinweis", "Eigene Notiz", "local_only")
    monkeypatch.setattr("jarvis.unified_search.MAX_PER_KIND", 1)
    result = search(db, "Orion")
    assert result["counts"] == {"document": 1, "memory": 1, "project": 1}
    assert result["limited"]
    for query in (" ", "was ist das?", "x" * 161):
        try:
            search(db, query)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid query accepted: {query}")


def test_search_api_requires_session_and_is_read_only(tmp_path):
    db, _, _, _, _, _, _, _ = sample(tmp_path)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=db.path.parent,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        assert client.get("/search/unified", params={"q": "Orion"}).status_code == 401
        client.get("/session")
        answer = client.get("/search/unified", params={"q": "Orion"})
        assert answer.status_code == 200
        assert answer.json()["counts"] == {"document": 1, "memory": 1, "project": 1}
        assert client.get("/search/unified", params={"q": "was ist das?"}).status_code == 422
        with db.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0


def test_preferred_source_survives_reload_and_tracks_original_changes(tmp_path):
    db, docs, _, folder, document, _, _, file = sample(tmp_path)
    choices = SourcePreferences(db)
    question = "Welche Nebenkosten fallen an?"
    assert search(db, question)["counts"]["document"] == 0
    saved = choices.add(question, document["id"])["preferences"][0]
    assert saved["status"] == "current"
    assert saved["saved_sha256"] == document["sha256"]
    assert SourcePreferences(Database(db.path)).list(question.lower())["preferences"][0] == saved
    result = search(db, question)
    hit = next(item for item in result["results"] if item["kind"] == "document")
    assert hit["preferred"] and hit["source_status"] == "current"
    assert hit["source"] == str(file.resolve()) and hit["sha256"] == document["sha256"]
    assert hit["passage_id"] and "Mietvertrag" in hit["excerpt"]

    choices.add("Orion", document["id"])
    file.write_text("Projekt Orion: Die Nebenkosten betragen 100 Euro.", encoding="utf-8")
    stale = search(db, question)
    assert stale["preferences"][0]["status"] == "needs_reindex"
    assert stale["counts"]["document"] == 0
    assert not any(item["id"] == document["id"] for item in search(db, "Orion")["results"])
    assert docs.index_folder(folder["id"])["indexed"] == 1
    updated = search(db, question)
    assert updated["preferences"][0]["status"] == "updated"
    assert updated["preferences"][0]["current_sha256"] != saved["saved_sha256"]
    assert next(item for item in updated["results"] if item["kind"] == "document")["source_status"] == "updated"
    assert choices.add(question, document["id"])["preferences"][0]["status"] == "current"

    archive = tmp_path / "prefs.zip"
    assert create_backup(db.path.parent, archive)["schema"] == 54
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    assert SourcePreferences(Database(restored / "jarvis.db")).list(question)["preferences"][0]["status"] == "current"
    assert choices.remove(question, document["id"])["preferences"] == []
    assert search(db, question)["preferences"] == []


def test_preferred_source_becomes_unavailable_when_excluded_or_removed(tmp_path):
    db, docs, _, folder, document, _, _, file = sample(tmp_path)
    choices = SourcePreferences(db)
    choices.add("Wohnungskosten", document["id"])
    docs.set_excluded(document["id"], True)
    result = search(db, "Wohnungskosten")
    assert result["preferences"][0]["status"] == "unavailable"
    assert result["counts"]["document"] == 0
    assert choices.remove("Wohnungskosten", document["id"])["preferences"] == []
    docs.set_excluded(document["id"], False)
    docs.index_folder(folder["id"])
    choices.add("Wohnungskosten", document["id"])
    file.unlink()
    docs.index_folder(folder["id"])
    result = search(db, "Wohnungskosten")
    assert result["preferences"][0]["status"] == "unavailable"
    assert result["counts"]["document"] == 0


def test_source_preference_api_checks_session_scope_and_current_index(tmp_path):
    db, docs, _, folder, document, _, _, file = sample(tmp_path)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=db.path.parent,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        payload = {"query": "Welche Nebenkosten?", "document_id": document["id"]}
        assert client.put("/search/unified/preferences", json=payload).status_code == 401
        csrf = client.get("/session").json()["csrf"]
        assert client.put("/search/unified/preferences", json=payload).status_code == 403
        headers = {"x-jarvis-csrf": csrf}
        assert client.put("/search/unified/preferences", json=payload, headers=headers).status_code == 200
        assert client.get("/search/unified", params={"q": payload["query"]}).json()["preferences"][0]["status"] == "current"
        assert client.put("/search/unified/preferences", json={**payload, "document_id": "missing"},
                          headers=headers).status_code == 404
        assert client.put("/search/unified/preferences", json={**payload, "query": "was ist das?"},
                          headers=headers).status_code == 422
        file.write_text("Geänderte lokale Datei", encoding="utf-8")
        assert client.put("/search/unified/preferences", json=payload,
                          headers=headers).status_code == 422
        assert client.request("DELETE", "/search/unified/preferences", json=payload,
                              headers=headers).status_code == 200
        assert client.request("DELETE", "/search/unified/preferences", json=payload,
                              headers=headers).status_code == 404
    assert docs.get_folder(folder["id"]) is not None


def test_source_preference_has_bounded_idempotent_selection(tmp_path):
    db, docs, _, folder, document, _, _, file = sample(tmp_path)
    for index in range(5):
        file.with_name(f"Zusatz_{index}.md").write_text(f"Quelle {index}", encoding="utf-8")
    assert docs.index_folder(folder["id"])["indexed"] == 5
    choices = SourcePreferences(db)
    available = [document] + [item for item in docs.documents() if item["id"] != document["id"]]
    for item in available[:5]:
        assert len(choices.add("Zusatzfrage", item["id"])["preferences"]) <= 5
    assert len(choices.add("zusatzfrage", available[0]["id"])["preferences"]) == 5
    with pytest.raises(ValueError, match="Höchstens fünf"):
        choices.add("Zusatzfrage", available[5]["id"])
