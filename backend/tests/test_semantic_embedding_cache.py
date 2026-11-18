"""RAM embeddings may be reused only for the same local model and text."""

from typing import ClassVar

import httpx
import pytest

import jarvis.semantic_search as semantic


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
