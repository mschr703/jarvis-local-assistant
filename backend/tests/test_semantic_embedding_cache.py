"""RAM and disk embeddings may be reused only for the same local model and text."""

import hashlib
import sqlite3
from typing import ClassVar

import httpx
import pytest

import jarvis.semantic_search as semantic
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.semantic_cache import PersistentEmbeddingCache


@pytest.fixture(autouse=True)
def empty_cache():
    semantic._embedding_cache.clear()
    semantic._active_digest.clear()
    yield
    semantic._embedding_cache.clear()
    semantic._active_digest.clear()


class Reply:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class FakeClient:
    digest = "a" * 64
    batches: ClassVar[list[list[str]]] = []
    tags_available = True
    invalid = False
    flip_on_post = False

    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def get(self, *_args, **_kwargs):
        if not self.tags_available:
            raise httpx.ConnectError("tags unavailable")
        return Reply({"models": [{"name": semantic.MODEL, "digest": self.digest}]})

    def post(self, _url, *, json):
        texts = json["input"]
        self.batches.append(texts)
        if self.flip_on_post:
            self.digest = "b" * 64
        if self.invalid:
            return Reply({"embeddings": [[float("nan")]] * len(texts)})
        return Reply({"embeddings": [[float(len(text)), 1.0] for text in texts]})


def install(monkeypatch):
    FakeClient.digest = "a" * 64
    FakeClient.batches = []
    FakeClient.tags_available = True
    FakeClient.invalid = False
    FakeClient.flip_on_post = False
    monkeypatch.setattr(semantic.httpx, "Client", FakeClient)


def test_repeated_question_embeds_query_only_and_model_change_invalidates(monkeypatch):
    install(monkeypatch)
    url = "http://127.0.0.1:11434"
    first = semantic.embed_texts(url, ["Frage eins", "Passage A", "Passage B"])
    second = semantic.embed_texts(url, ["Frage zwei", "Passage A", "Passage B"])
    assert first[1:] == second[1:]
    assert FakeClient.batches == [["Frage eins", "Passage A", "Passage B"], ["Frage zwei"]]
    assert len(semantic._embedding_cache) == 2
    assert all("Passage" not in str(key) for key in semantic._embedding_cache)
    FakeClient.digest = "b" * 64
    semantic.embed_texts(url, ["Frage drei", "Passage A", "Passage B"])
    assert FakeClient.batches[-1] == ["Frage drei", "Passage A", "Passage B"]
    assert len(semantic._embedding_cache) == 2
    semantic.embed_texts(url, ["Frage vier", "Passage A geändert", "Passage B"])
    assert FakeClient.batches[-1] == ["Frage vier", "Passage A geändert"]


def test_missing_tags_disables_cache_and_invalid_vectors_are_not_saved(monkeypatch):
    install(monkeypatch)
    url = "http://127.0.0.1:11434"
    FakeClient.tags_available = False
    semantic.embed_texts(url, ["Frage", "Passage"])
    semantic.embed_texts(url, ["Noch eine Frage", "Passage"])
    assert FakeClient.batches[-1] == ["Noch eine Frage", "Passage"]
    assert not semantic._embedding_cache
    FakeClient.tags_available = True
    FakeClient.invalid = True
    with pytest.raises(semantic.SemanticSearchError, match="ungültige Embeddings"):
        semantic.embed_texts(url, ["Frage", "Passage"])
    assert not semantic._embedding_cache


def test_ram_cache_has_fixed_limit(monkeypatch):
    install(monkeypatch)
    semantic.embed_texts("http://127.0.0.1:11434",
                         ["Frage"] + [f"Passage {i}" for i in range(220)])
    assert len(semantic._embedding_cache) == semantic.CACHE_LIMIT


def test_model_change_during_embedding_rejects_mixed_vectors(monkeypatch):
    install(monkeypatch)
    FakeClient.flip_on_post = True
    with pytest.raises(semantic.SemanticSearchError, match="während der Suche"):
        semantic.embed_texts("http://127.0.0.1:11434", ["Frage", "Passage"])
    assert not semantic._embedding_cache


def test_disk_cache_reuses_passages_after_restart_and_invalidates_on_change(tmp_path, monkeypatch):
    install(monkeypatch)
    url = "http://127.0.0.1:11434"
    path = tmp_path / "semantic-embeddings.sqlite"
    first = semantic.embed_texts(url, ["Frage eins", "Passage A", "Passage B"], path)
    assert path.exists()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 2
        hashes = {row[0] for row in db.execute("SELECT text_hash FROM embeddings")}
    assert hashlib.sha256(b"Frage eins").hexdigest() not in hashes
    assert hashlib.sha256(b"Passage A").hexdigest() in hashes
    assert b"Passage A" not in path.read_bytes()

    semantic._embedding_cache.clear()
    second = semantic.embed_texts(url, ["Frage zwei", "Passage A", "Passage B"], path)
    assert first[1:] == second[1:]
    assert FakeClient.batches[-1] == ["Frage zwei"]

    semantic._embedding_cache.clear()
    FakeClient.digest = "b" * 64
    semantic.embed_texts(url, ["Frage drei", "Passage A geändert", "Passage B"], path)
    assert FakeClient.batches[-1] == ["Frage drei", "Passage A geändert", "Passage B"]
    with sqlite3.connect(path) as db:
        assert {row[0] for row in db.execute("SELECT digest FROM embeddings")} == {"b" * 64}


def test_disk_cache_is_bounded_and_corrupt_vectors_are_recomputed(tmp_path, monkeypatch):
    install(monkeypatch)
    url = "http://127.0.0.1:11434"
    path = tmp_path / "semantic-embeddings.sqlite"
    semantic.embed_texts(url, ["Frage"] + [f"Passage {i}" for i in range(220)], path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == semantic.CACHE_LIMIT
        db.execute("UPDATE embeddings SET vector_json='[0,0]' WHERE text_hash=?",
                   (hashlib.sha256(b"Passage 219").hexdigest(),))
    semantic._embedding_cache.clear()
    semantic.embed_texts(url, ["Neue Frage", "Passage 219"], path)
    assert FakeClient.batches[-1] == ["Neue Frage", "Passage 219"]


def test_unavailable_disk_cache_does_not_block_local_search(tmp_path, monkeypatch):
    install(monkeypatch)
    unavailable_path = tmp_path / "directory.sqlite"
    unavailable_path.mkdir()
    assert semantic.embed_texts("http://127.0.0.1:11434",
                                ["Frage", "Passage"], unavailable_path)[1] == [7.0, 1.0]
    malformed = tmp_path / "malformed.sqlite"
    malformed.write_bytes(b"not a SQLite database")
    assert semantic.embed_texts("http://127.0.0.1:11434",
                                ["Neue Frage", "Andere Passage"], malformed)[1] == [14.0, 1.0]


def test_missing_model_digest_never_reuses_disk_vector(tmp_path, monkeypatch):
    install(monkeypatch)
    url = "http://127.0.0.1:11434"
    path = tmp_path / "semantic-embeddings.sqlite"
    semantic.embed_texts(url, ["Frage eins", "Passage"], path)
    semantic._embedding_cache.clear()
    FakeClient.tags_available = False
    semantic.embed_texts(url, ["Frage zwei", "Passage"], path)
    assert FakeClient.batches[-1] == ["Frage zwei", "Passage"]


def test_model_change_mid_search_does_not_persist_vectors(tmp_path, monkeypatch):
    install(monkeypatch)
    FakeClient.flip_on_post = True
    path = tmp_path / "semantic-embeddings.sqlite"
    with pytest.raises(semantic.SemanticSearchError, match="während der Suche"):
        semantic.embed_texts("http://127.0.0.1:11434", ["Frage", "Passage"], path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 0


def test_deleting_personal_memory_purges_persistent_vectors(tmp_path):
    db = Database(tmp_path / "data" / "jarvis.db")
    documents = DocumentStore(db)
    memory = documents.add_memory("Private Notiz", "Eigene Eingabe", "local_only")
    cache = PersistentEmbeddingCache(db.path.parent / "semantic-embeddings.sqlite")
    digest, text_hash = "a" * 64, hashlib.sha256(b"Private Notiz").hexdigest()
    cache.write(digest, {text_hash: [1.0, 0.0]})
    assert cache.read(digest, [text_hash])
    documents.delete_memory(memory["id"])
    assert cache.read(digest, [text_hash]) == {}


def test_disk_vector_with_wrong_dimension_is_recomputed(tmp_path, monkeypatch):
    install(monkeypatch)
    path = tmp_path / "semantic-embeddings.sqlite"
    text_hash = hashlib.sha256(b"Passage").hexdigest()
    PersistentEmbeddingCache(path).write("a" * 64, {text_hash: [1.0]})
    vectors = semantic.embed_texts("http://127.0.0.1:11434",
                                   ["Frage", "Passage"], path)
    assert vectors[1] == [7.0, 1.0]
    assert FakeClient.batches == [["Frage"], ["Frage", "Passage"]]
    assert PersistentEmbeddingCache(path).read("a" * 64, [text_hash])[text_hash] == [7.0, 1.0]
