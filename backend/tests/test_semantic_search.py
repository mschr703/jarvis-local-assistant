"""Semantic search only exposes current, explicitly granted local documents."""

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.semantic_cache import PersistentEmbeddingCache
from jarvis.semantic_search import SemanticSearchError, embed_texts, search


def fixture_store(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    agreement = root / "Wohnung.md"
    agreement.write_text("Die Vereinbarung zur Wohnung liegt im roten Ordner.", encoding="utf-8")
    cake = root / "Kuchen.md"
    cake.write_text("Das Rezept für Apfelkuchen enthält Zimt.", encoding="utf-8")
    db = Database(tmp_path / "data" / "jarvis.db")
    docs = DocumentStore(db)
    folder = docs.add_folder(str(root), "local_only")
    assert docs.index_folder(folder["id"])["indexed"] == 2
    return db, docs, folder, agreement, cake


def fake_embed(_url, texts, _cache_path=None):
    assert texts[0] == "Wo liegt mein Mietvertrag?"
    return [[1.0, 0.0] if "Vereinbarung" in text or "Mietvertrag" in text
            else [0.0, 1.0] for text in texts]


def test_synonym_search_returns_current_source_and_proof(tmp_path, monkeypatch):
    db, _, _, agreement, _ = fixture_store(tmp_path)
    monkeypatch.setattr("jarvis.semantic_search.embed_texts", fake_embed)
    found = search(db, "Wo liegt mein Mietvertrag?", "http://127.0.0.1:11434")
    assert found["model"] == "embeddinggemma:300m"
    assert found["checked_passages"] == 2
    first = found["results"][0]
    assert first["source"] == str(agreement.resolve())
    assert first["similarity"] == 1.0 and first["passage_id"]
    assert len(first["sha256"]) == 64 and first["policy"] == "local_only"
    assert "Mietvertrag" not in first["excerpt"]


def test_changed_and_excluded_sources_never_reach_embedding_model(tmp_path, monkeypatch):
    db, docs, _, agreement, cake = fixture_store(tmp_path)
    agreement.write_text("Anderer Vertrag", encoding="utf-8")
    docs.set_excluded(next(item["id"] for item in docs.documents()
                           if item["path"] == str(cake.resolve())), True)
    monkeypatch.setattr("jarvis.semantic_search.embed_texts",
                        lambda *_: pytest.fail("No current passages should be embedded"))
    found = search(db, "Wo liegt mein Mietvertrag?", "http://127.0.0.1:11434")
    assert found["results"] == []
    assert found["stale_sources"][0]["path"] == str(agreement.resolve())
    assert "excerpt" not in found["stale_sources"][0]


def test_source_changed_during_embedding_is_not_returned(tmp_path, monkeypatch):
    db, _, _, agreement, cake = fixture_store(tmp_path)
    cache = PersistentEmbeddingCache(db.path.parent / "semantic-embeddings.sqlite")
    cache.write("a" * 64, {"b" * 64: [1.0]})

    def mutate(_url, texts, _cache_path=None):
        agreement.write_text("Änderung während des Aufrufs", encoding="utf-8")
        return fake_embed(_url, texts)

    monkeypatch.setattr("jarvis.semantic_search.embed_texts", mutate)
    found = search(db, "Wo liegt mein Mietvertrag?", "http://127.0.0.1:11434")
    assert [item["source"] for item in found["results"]] == [str(cake.resolve())]
    assert found["stale_sources"][0]["path"] == str(agreement.resolve())
    assert cache.read("a" * 64, ["b" * 64]) == {}


def test_passage_limit_and_first_three_per_document_are_visible(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "a.txt").write_text("Absatz " * 800, encoding="utf-8")
    (root / "b.txt").write_text("Ein zweiter Text", encoding="utf-8")
    db = Database(tmp_path / "data" / "jarvis.db")
    docs = DocumentStore(db)
    folder = docs.add_folder(str(root), "local_only")
    docs.index_folder(folder["id"])
    import jarvis.semantic_search as module
    monkeypatch.setattr(module, "MAX_PASSAGES", 3)
    observed = []
    cache_paths = []

    def collect(_url, texts, _cache_path=None):
        observed.extend(texts)
        cache_paths.append(_cache_path)
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(module, "embed_texts", collect)
    result = search(db, "Absatz?", "http://127.0.0.1:11434")
    assert result["limited"] and result["checked_passages"] == 3
    assert len(observed) == 4
    assert cache_paths == [db.path.parent / "semantic-embeddings.sqlite"]
    assert len(result["results"]) == 1


def test_document_limit_bounds_original_checks(tmp_path, monkeypatch):
    db, docs, folder, _, _ = fixture_store(tmp_path)
    root = tmp_path / "allowed"
    for index in range(4):
        (root / f"extra_{index}.md").write_text("Noch ein Text", encoding="utf-8")
    docs.index_folder(folder["id"])
    import jarvis.semantic_search as module
    monkeypatch.setattr(module, "MAX_DOCUMENTS", 2)
    monkeypatch.setattr(module, "embed_texts", lambda _url, texts, _cache_path=None: [[1.0] for _ in texts])
    original = DocumentStore.source_is_current
    checked = []

    def count(row):
        checked.append(row["document_id"])
        return original(row)

    monkeypatch.setattr(DocumentStore, "source_is_current", staticmethod(count))
    found = search(db, "eine Frage", "http://127.0.0.1:11434")
    assert found["limited"] and found["checked_passages"] == 2
    assert len(set(checked)) == 2


def test_folder_filter_makes_later_corpus_accessible(tmp_path, monkeypatch):
    db, docs, _, _, _ = fixture_store(tmp_path)
    other = tmp_path / "more"
    other.mkdir()
    target = other / "Later.md"
    target.write_text("Der Mietvertrag liegt im grünen Umschlag.", encoding="utf-8")
    folder = docs.add_folder(str(other), "local_only")
    docs.index_folder(folder["id"])
    import jarvis.semantic_search as module
    monkeypatch.setattr(module, "MAX_DOCUMENTS", 1)
    monkeypatch.setattr(module, "embed_texts", lambda _url, texts, _cache_path=None: [[1.0] for _ in texts])
    found = search(db, "Wo liegt der Vertrag?", "http://127.0.0.1:11434", folder["id"])
    assert not found["limited"] and found["checked_passages"] == 1
    assert found["results"][0]["source"] == str(target.resolve())
    with pytest.raises(ValueError, match="Freigabeordner"):
        search(db, "Wo liegt der Vertrag?", "http://127.0.0.1:11434", "missing")


def test_local_url_and_bad_embedding_response_are_rejected(monkeypatch):
    with pytest.raises(SemanticSearchError, match="lokale Ollama"):
        embed_texts("https://remote.example:11434", ["private text"])
    with pytest.raises(SemanticSearchError, match="lokale Ollama"):
        embed_texts("http://127.0.0.1:bad", ["private text"])

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"embeddings": [[float("nan")]]}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr("jarvis.semantic_search.httpx.Client", FakeClient)
    with pytest.raises(SemanticSearchError, match="ungültige Embeddings"):
        embed_texts("http://localhost:11434", ["query"])


def test_api_reports_missing_local_model(tmp_path, monkeypatch):
    db, _, _, _, _ = fixture_store(tmp_path)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=db.path.parent,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        client.get("/session")
        monkeypatch.setattr("jarvis.semantic_search.embed_texts",
                            lambda *_: (_ for _ in ()).throw(SemanticSearchError("Ollama fehlt")))
        response = client.get("/search/semantic", params={"q": "Wohnungsvereinbarung"})
        assert response.status_code == 503
        assert response.json()["detail"] == "Ollama fehlt"
        monkeypatch.setattr("jarvis.semantic_search.embed_texts", fake_embed)
        response = client.get("/search/semantic", params={"q": "Wo liegt mein Mietvertrag?"})
        assert response.status_code == 200
        assert response.json()["results"][0]["title"] == "Wohnung.md"
