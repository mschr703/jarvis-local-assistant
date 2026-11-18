"""The embedding readiness check is local, bounded and does not run a model."""

from typing import ClassVar

import httpx
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.semantic_search import MODEL, model_status


class Reply:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


class Client:
    body: ClassVar[dict] = {"models": []}
    unavailable = False
    seen: ClassVar[list[tuple[str, object]]] = []

    def __init__(self, **kwargs):
        self.seen.append(("options", kwargs))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def get(self, url):
        self.seen.append(("get", url))
        if self.unavailable:
            raise httpx.ConnectError("service unavailable")
        return Reply(self.body)


def test_model_readiness_states_and_local_only_request(monkeypatch):
    Client.body = {"models": [{"name": MODEL, "digest": "a" * 64}]}
    Client.unavailable = False
    Client.seen = []
    monkeypatch.setattr("jarvis.semantic_search.httpx.Client", Client)
    assert model_status("https://remote.example:11434")["status"] == "invalid_url"
    assert not Client.seen
    ready = model_status("http://127.0.0.1:11434")
    assert ready["ready"] is True and ready["status"] == "ready"
    assert Client.seen == [("options", {"timeout": 3, "trust_env": False}),
                           ("get", "http://127.0.0.1:11434/api/tags")]
    Client.body = {"models": [{"name": "qwen3:4b-instruct"}]}
    missing = model_status("http://localhost:11434")
    assert missing["status"] == "model_missing"
    assert "ollama pull embeddinggemma:300m" in missing["message"]
    Client.body = {"models": "invalid"}
    assert model_status("http://127.0.0.1:11434")["status"] == "invalid_response"
    Client.unavailable = True
    assert model_status("http://127.0.0.1:11434")["status"] == "service_unavailable"


def test_status_api_requires_session_and_rejects_remote_address(tmp_path):
    settings = Settings(JARVIS_DATA_DIR=tmp_path / "data",
                        JARVIS_OLLAMA_URL="https://remote.example:11434",
                        JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        assert client.get("/search/semantic/status").status_code == 401
        client.get("/session")
        response = client.get("/search/semantic/status")
        assert response.status_code == 200
        assert response.json()["status"] == "invalid_url"
        assert response.json()["ready"] is False
