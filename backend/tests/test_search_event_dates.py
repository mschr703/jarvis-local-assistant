"""Date scopes find older events without filtering unrelated local sources."""

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.memory_events import MemoryEventStore
from jarvis.semantic_search import search as semantic_search
from jarvis.unified_search import search as keyword_search


def sample(tmp_path):
    db = Database(tmp_path / "data" / "jarvis.db")
    events = MemoryEventStore(db)
    old = events.create("2020-06-10", "Paddeln am See", "Sommerferien")
    events.create("2024-06-10", "Paddeln am Fluss")
    events.create("2025-06-10", "Paddeln am Meer")
    memory = DocumentStore(db).add_memory("Paddeln mit Freunden", "Eigene Notiz",
                                          "local_only")
    return db, old, memory


def test_date_scope_reaches_older_keyword_and_semantic_events(tmp_path, monkeypatch):
    db, old, memory = sample(tmp_path)
    monkeypatch.setattr("jarvis.unified_search.MAX_EVENT_ROWS", 2)
    monkeypatch.setattr("jarvis.semantic_search.MAX_EVENTS", 2)
    monkeypatch.setattr("jarvis.semantic_search.embed_texts",
                        lambda _url, texts, _cache_path=None: [[1.0] for _ in texts])
    plain = keyword_search(db, "Paddeln", "life_event")
    assert old["id"] not in {item["id"] for item in plain["results"]}
    scoped = keyword_search(db, "Paddeln", "all", to_date="2020-12-31")
    assert scoped["event_to_date"] == "2020-12-31"
    assert old["id"] in {item["id"] for item in scoped["results"]}
    assert memory["id"] in {item["id"] for item in scoped["results"]}
    assert not scoped["limited"]
    plain_semantic = semantic_search(db, "Wo war ich paddeln?",
                                     "http://127.0.0.1:11434", source_kind="life_event")
    assert old["id"] not in {item["id"] for item in plain_semantic["results"]}
    scoped_semantic = semantic_search(db, "Wo war ich paddeln?",
                                      "http://127.0.0.1:11434", source_kind="all",
                                      to_date="2020-12-31")
    assert scoped_semantic["event_to_date"] == "2020-12-31"
    assert scoped_semantic["checked_events"] == 1
    assert old["id"] in {item["id"] for item in scoped_semantic["results"]}
    assert memory["id"] in {item["id"] for item in scoped_semantic["results"]}


@pytest.mark.parametrize("from_date,to_date", [
    ("2024-02-30", None), ("2024/01/01", None),
    ("2025-01-01", "2024-01-01"),
])
def test_event_date_scope_rejects_invalid_ranges(tmp_path, monkeypatch,
                                                  from_date, to_date):
    db, _, _ = sample(tmp_path)
    with pytest.raises(ValueError):
        keyword_search(db, "Paddeln", "life_event", from_date, to_date)
    monkeypatch.setattr("jarvis.semantic_search.embed_texts",
                        lambda *_args, **_kwargs: pytest.fail("invalid range reached Ollama"))
    with pytest.raises(ValueError):
        semantic_search(db, "Paddeln", "http://127.0.0.1:11434",
                        source_kind="life_event", from_date=from_date, to_date=to_date)


def test_date_scope_api_validation_and_empty_results(tmp_path, monkeypatch):
    db, old, _ = sample(tmp_path)
    monkeypatch.setattr("jarvis.semantic_search.embed_texts",
                        lambda _url, texts, _cache_path=None: [[1.0] for _ in texts])
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=db.path.parent,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        client.get("/session")
        params = {"q": "Paddeln", "kind": "life_event",
                  "from_date": "2020-01-01", "to_date": "2020-12-31"}
        lexical = client.get("/search/unified", params=params)
        semantic = client.get("/search/semantic", params=params)
        assert lexical.status_code == semantic.status_code == 200
        assert lexical.json()["results"][0]["id"] == old["id"]
        assert semantic.json()["results"][0]["id"] == old["id"]
        assert semantic.json()["event_from_date"] == "2020-01-01"
        params["from_date"] = "2030-01-01"
        params["to_date"] = "2030-12-31"
        assert client.get("/search/unified", params=params).json()["results"] == []
        assert client.get("/search/semantic", params=params).json()["results"] == []
        params["kind"] = "memory"
        assert client.get("/search/unified", params=params).status_code == 422
        assert client.get("/search/semantic", params=params).status_code == 422
        params["kind"] = "life_event"
        params["document_id"] = "wrong"
        assert client.get("/search/semantic", params=params).status_code == 422
