"""Teilfragenvorschläge bleiben lokal und starten keine öffentliche Suche."""

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.provider import Completion


def test_local_suggestion_returns_two_editable_queries_without_search(tmp_path, monkeypatch):
    calls = []

    async def generate(_db, config, purpose, messages):
        calls.append((config.provider, config.max_output_tokens, purpose, messages))
        return Completion('```json\n{"queries":["Europa Clipper Eisoberfläche",'
                          '"Europa Clipper Instrumente"]}\n```', 30, 20, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    monkeypatch.setattr("jarvis.app.search_public_web", lambda _query: pytest.fail("Suchdienst aufgerufen"))
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        response = client.post("/research/suggest-queries", headers=headers,
                               json={"question": "Was untersucht Europa Clipper auf Europa?"})
        assert response.status_code == 200, response.text
        assert response.json() == {"first": "Europa Clipper Eisoberfläche",
                                   "second": "Europa Clipper Instrumente"}
        assert client.get("/research").json() == []
    assert calls[0][0:3] == ("ollama", 120, "research_queries")
    assert "Was untersucht Europa Clipper" in calls[0][3][1]["content"]


@pytest.mark.parametrize("response_text", [
    "kein JSON", '{"queries":["gleich","GLEICH"]}',
    '{"queries":["kurz","eine zweite längere Frage"]}',
    '{"queries":["https://example.com/a","eine zweite längere Frage"]}',
    '{"queries":["' + "x" * 201 + '","eine zweite längere Frage"]}',
    '{"queries":["nur eine Frage"]}',
])
def test_invalid_local_suggestions_fail_without_search(tmp_path, monkeypatch, response_text):
    async def generate(*_args):
        return Completion(response_text, 30, 20, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    monkeypatch.setattr("jarvis.app.search_public_web", lambda _query: pytest.fail("Suchdienst aufgerufen"))
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        response = client.post("/research/suggest-queries", headers=headers,
                               json={"question": "Was untersucht Europa Clipper auf Europa?"})
        assert response.status_code == 502
        assert "brauchbaren Suchfragen" in response.json()["detail"]
        assert client.get("/research").json() == []


def test_suggestion_timeout_and_cloud_provider_are_explicit(tmp_path, monkeypatch):
    async def timed_out(*_args):
        raise TimeoutError

    monkeypatch.setattr("jarvis.app.generate_text", timed_out)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path / "local",
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        response = client.post("/research/suggest-queries", headers=headers,
                               json={"question": "Was untersucht Europa Clipper auf Europa?"})
        assert response.status_code == 504
        assert "45 Sekunden" in response.json()["detail"]
    settings = Settings(JARVIS_DATA_DIR=tmp_path / "cloud", JARVIS_PROVIDER="openrouter",
                        JARVIS_OPENROUTER_API_KEY="fixture", JARVIS_OPENROUTER_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        response = client.post("/research/suggest-queries", headers=headers,
                               json={"question": "Was untersucht Europa Clipper auf Europa?"})
        assert response.status_code == 422
        assert "nur mit lokalem Ollama" in response.json()["detail"]
