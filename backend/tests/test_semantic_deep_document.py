"""A selected document exposes relevant later passages without broadening local access."""

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.semantic_search import search
from jarvis.tasks import TaskStore
from jarvis.work_states import WorkStateStore


def sample(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    file = root / "Langer_Vertrag.md"
    file.write_text("Garten und Blumen. " * 320 + "Die Kaution beträgt 900 Euro.",
                    encoding="utf-8")
    db = Database(tmp_path / "data" / "jarvis.db")
    docs = DocumentStore(db)
    folder = docs.add_folder(str(root), "local_only")
    docs.index_folder(folder["id"])
    document = docs.documents()[0]
    docs.add_memory("Die Schlüssel kommen morgen.", "Eigene Notiz", "local_only")
    WorkStateStore(db, docs, TaskStore(db)).create("Umzug", "Zusage erhalten", "",
                                                   "Schlüssel holen", [], [], [])
    return db, docs, folder, document, file


def fake_embed(_url, texts, _cache_path=None):
    return [[1.0, 0.0] if "Kaution" in text else [0.0, 1.0] for text in texts]


def test_deep_search_finds_later_passage_and_keeps_each_reference(tmp_path, monkeypatch):
    db, docs, _, document, file = sample(tmp_path)
    monkeypatch.setattr("jarvis.semantic_search.embed_texts", fake_embed)
    broad = search(db, "Wie hoch ist die Kaution?", "http://127.0.0.1:11434")
    assert broad["counts"] == {"document": 1, "memory": 1, "project": 1}
    assert "Kaution beträgt" not in next(item["excerpt"] for item in broad["results"]
                                         if item["kind"] == "document")
    deep = search(db, "Wie hoch ist die Kaution?", "http://127.0.0.1:11434",
                  document_id=document["id"])
    assert deep["document_scope"] == {"id": document["id"], "title": file.name}
    assert deep["checked_passages"] > 3
    assert deep["checked_memories"] == deep["checked_projects"] == 0
    assert deep["counts"] == {"document": 5, "memory": 0, "project": 0}
    assert deep["limited"]
    assert "Kaution beträgt 900 Euro" in deep["results"][0]["excerpt"]
    assert len({item["passage_id"] for item in deep["results"]}) == 5
    assert all(item["sha256"] == document["sha256"] and item["source"] == str(file.resolve())
               for item in deep["results"])
    assert docs.get_document(document["id"])["status"] == "indexed"


def test_deep_search_rejects_stale_excluded_and_wrong_folder(tmp_path, monkeypatch):
    db, docs, _, document, file = sample(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    other_folder = docs.add_folder(str(other), "local_only")
    with pytest.raises(ValueError, match="gewählten Ordner"):
        search(db, "Wie hoch ist die Kaution?", "http://127.0.0.1:11434",
               other_folder["id"], document["id"])
    file.write_text("Geänderter Vertrag", encoding="utf-8")
    monkeypatch.setattr("jarvis.semantic_search.embed_texts",
                        lambda *_: pytest.fail("Stale passage must not reach Ollama"))
    stale = search(db, "Wie hoch ist die Kaution?", "http://127.0.0.1:11434",
                   document_id=document["id"])
    assert stale["results"] == [] and stale["checked_memories"] == 0
    assert stale["stale_sources"][0]["document_id"] == document["id"]
    docs.set_excluded(document["id"], True)
    with pytest.raises(ValueError, match="Indexiertes Dokument"):
        search(db, "Wie hoch ist die Kaution?", "http://127.0.0.1:11434",
               document_id=document["id"])


def test_deep_search_has_visible_candidate_limit(tmp_path, monkeypatch):
    db, _, _, document, _ = sample(tmp_path)
    import jarvis.semantic_search as module
    monkeypatch.setattr(module, "MAX_PASSAGES", 4)
    monkeypatch.setattr(module, "embed_texts", fake_embed)
    found = search(db, "Wie hoch ist die Kaution?", "http://127.0.0.1:11434",
                   document_id=document["id"])
    assert found["checked_passages"] == 4 and found["limited"]
    assert all("Kaution beträgt" not in item["excerpt"] for item in found["results"])


def test_deep_search_api_validates_document_scope(tmp_path, monkeypatch):
    db, _, _, document, _ = sample(tmp_path)
    monkeypatch.setattr("jarvis.semantic_search.embed_texts", fake_embed)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=db.path.parent,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        client.get("/session")
        found = client.get("/search/semantic", params={"q": "Wie hoch ist die Kaution?",
                                                        "document_id": document["id"]})
        assert found.status_code == 200
        assert found.json()["document_scope"]["id"] == document["id"]
        assert found.json()["counts"]["document"] == 5
        assert client.get("/search/semantic", params={"q": "Kaution",
                                                      "document_id": "missing"}).status_code == 422
