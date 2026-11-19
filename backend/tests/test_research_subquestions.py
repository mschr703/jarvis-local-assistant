"""Zwei begrenzte Suchfragen behalten Quellenherkunft und Teilerfolge."""

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.provider import Completion
from jarvis.research import ResearchStore


async def completion(*_args):
    return Completion("Beide Fundstellen ergänzen sich.", 40, 15, 0, "local_no_api_charge")


def test_two_questions_read_one_source_each_and_survive_restore(tmp_path, monkeypatch):
    searches = []
    reads = []

    def search(query):
        searches.append(query)
        if query == "Eisoberfläche Europa":
            return ([{"title": "Sperre", "url": "https://example.com/bad"},
                     {"title": "PDF", "url": "https://example.com/a.pdf"}], "DuckDuckGo HTML")
        assert query == "Instrumente Europa"
        return ([{"title": "Doppelt", "url": "https://example.com/a.pdf"},
                 {"title": "Webseite", "url": "https://example.com/b"}], "Wikipedia-Suche")

    def read(url, title):
        reads.append(url)
        if url.endswith("/bad"):
            raise ValueError("HTTP 403")
        return {"url": url, "title": title,
                "text": f"Europa Clipper erforscht den Jupitermond Europa; diese Quelle heißt {title}. " * 4,
                "retrieved_at": "2026-10-27T07:00:00Z", "media_type": "pdf" if url.endswith(".pdf") else "web"}

    monkeypatch.setattr("jarvis.app.search_public_web", search)
    monkeypatch.setattr("jarvis.app.read_source", read)
    monkeypatch.setattr("jarvis.app.generate_text", completion)
    root = tmp_path / "data"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        payload = {"question": "Was untersucht Europa Clipper mit welchen Instrumenten?",
                   "search_query": "Eisoberfläche Europa", "second_search_query": "Instrumente Europa"}
        response = client.post("/research", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["search_provider"] == "Zwei Suchanfragen"
        assert report["search_query"] == "Eisoberfläche Europa | Instrumente Europa"
        assert [item["url"] for item in report["sources"]] == ["https://example.com/a.pdf", "https://example.com/b"]
        assert [item["found_by_query"] for item in report["sources"]] == [
            "Eisoberfläche Europa", "Instrumente Europa"]
        assert [item["found_by_provider"] for item in report["sources"]] == [
            "DuckDuckGo HTML", "Wikipedia-Suche"]
        assert [item["found_by_query"] for item in report["comparison"]["entries"]] == [
            "Eisoberfläche Europa", "Instrumente Europa"]
        assert len(report["claims"]) == 2 and "HTTP 403" in report["errors"][0]
        assert client.get(f"/research/{report['id']}").json() == report
    assert searches == ["Eisoberfläche Europa", "Instrumente Europa"]
    assert reads == ["https://example.com/bad", "https://example.com/a.pdf",
                     "https://example.com/b"]
    archive = tmp_path / "backup.zip"
    create_backup(root, archive)
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    assert ResearchStore(Database(restored / "jarvis.db")).get(report["id"]) == report


def test_second_search_partial_failures_and_incompatible_inputs(tmp_path, monkeypatch):
    def search(query):
        if query == "Erste Teilfrage":
            raise ValueError("Suchdienst nicht erreichbar")
        if query == "Zweite Teilfrage":
            return ([{"title": "Quelle", "url": "https://example.com/b"}], "DuckDuckGo HTML")
        return [], "Wikipedia-Suche"

    monkeypatch.setattr("jarvis.app.search_public_web", search)
    monkeypatch.setattr("jarvis.app.read_source", lambda url, title: {
        "url": url, "title": title, "text": "Eine öffentliche Quelle mit überprüfbarem Text zur zweiten Teilfrage. " * 4,
        "retrieved_at": "2026-10-27T07:00:00Z"})
    monkeypatch.setattr("jarvis.app.generate_text", completion)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        payload = {"question": "Was sagen zwei öffentliche Quellen zu Europa?",
                   "search_query": "Erste Teilfrage", "second_search_query": "Zweite Teilfrage"}
        response = client.post("/research", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        assert len(response.json()["sources"]) == 1
        assert "Suchdienst nicht erreichbar" in response.json()["errors"][0]
        assert response.json()["sources"][0]["found_by_query"] == "Zweite Teilfrage"
        assert client.post("/research", headers=headers,
                           json={**payload, "source_urls": ["https://example.com/b"]}).status_code == 422
        assert client.post("/research", headers=headers,
                           json={**payload, "second_search_query": "Erste Teilfrage"}).status_code == 422
        empty = client.post("/research", headers=headers,
                            json={**payload, "search_query": "Ohne Treffer", "second_search_query": "Noch ohne Treffer"})
        assert empty.status_code == 404


def test_second_search_still_requires_cloud_consent(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.app.search_public_web", lambda query: (
        [{"title": query, "url": "https://example.com/same"}], "DuckDuckGo HTML"))
    monkeypatch.setattr("jarvis.app.read_source", lambda url, title: {
        "url": url, "title": title, "text": f"Öffentliche Quelle {title} mit einem langen geprüften Text. " * 4,
        "retrieved_at": "2026-10-27T07:00:00Z"})
    monkeypatch.setattr("jarvis.app.generate_text", completion)
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_PROVIDER="openrouter",
                        JARVIS_OPENROUTER_API_KEY="fixture", JARVIS_OPENROUTER_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        payload = {"question": "Was zeigen die öffentlichen Quellen?",
                   "search_query": "Erste Frage", "second_search_query": "Zweite Frage"}
        assert client.post("/research", headers=headers, json=payload).status_code == 422
        response = client.post("/research", headers=headers, json={**payload, "allow_cloud": True})
        assert response.status_code == 200, response.text
        assert len(response.json()["sources"]) == 1  # identische URL: zweiter Treffer ist Duplikat
