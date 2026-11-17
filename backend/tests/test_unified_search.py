"""Unified local search returns current, cited records from three existing stores."""

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore
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
