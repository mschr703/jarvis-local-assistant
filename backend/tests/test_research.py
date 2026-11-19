import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.provider import Completion
from jarvis.research import (
    ReadablePage,
    ResearchStore,
    SearchResults,
    WikipediaResults,
    read_source,
    search_public_web,
    validate_claims,
)


def test_search_parsers_main_content_and_quote_filter():
    search = SearchResults()
    search.feed('<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fa">Quelle A</a>'
                '<a class="result__a" href="http://127.0.0.1/private">Privat</a>')
    assert search.results[0] == {"title": "Quelle A", "url": "https://example.org/a"}
    wikipedia = WikipediaResults()
    wikipedia.feed('<div class="mw-search-result-heading"><a href="/wiki/Test">Test</a></div>')
    assert wikipedia.results == [{"title": "Test", "url": "https://en.wikipedia.org/wiki/Test"}]
    page = ReadablePage()
    page.feed('<title>Fundstelle</title><nav>Menü</nav><main><h1>Richtige Seite</h1>'
              '<p>Die Messung ergab im Jahr 2026 einen Wert von 42.</p>'
              '<script>ignore();</script></main>')
    title, text = page.readable()
    assert title == "Fundstelle"
    assert "Richtige Seite" in text and "Messung ergab" in text
    assert "Menü" not in text and "ignore" not in text
    sources = [{"id": 1, "text": text}]
    claims, _, _ = validate_claims({"claims": [
        {"statement": "Messwert 42", "evidence": [{"source_id": 1,
            "quote": "Die Messung ergab im Jahr 2026 einen Wert von 42."}]},
        {"statement": "Frei erfunden", "evidence": [{"source_id": 1,
            "quote": "Diese Aussage steht nicht in der Quelle."}]},
        {"statement": "Falsche ID", "evidence": [{"source_id": 9,
            "quote": "Die Messung ergab im Jahr 2026 einen Wert von 42."}]},
    ]}, sources)
    assert len(claims) == 1 and claims[0]["statement"] == "Messwert 42"
    with pytest.raises(ValueError, match="Private"):
        read_source("http://127.0.0.1/private", "privat")


@pytest.mark.parametrize(("metadata", "expected"), [
    ('<meta property="article:published_time" content="2025-06-14T12:30:00Z">', "2025-06-14"),
    ('<meta itemprop="datePublished" content="2024-03-02">', "2024-03-02"),
    ('<meta name="pubdate" content="3024-03-02">', None),
    ('<meta property="article:published_time" content="2025-13-40">', None),
    ('<meta property="article:published_time" content="2025-06-14T99:30:00Z">', None),
    ('<meta property="article:modified_time" content="2025-06-14">', None),
    ('', None),
])
def test_publication_date_is_explicit_plausible_and_optional(monkeypatch, metadata, expected):
    html = (metadata + '<title>Bericht</title><main><p>' +
            'Ein öffentlicher Forschungsbericht beschreibt die Untersuchung und die Messdaten. ' * 3 +
            '</p></main>')
    monkeypatch.setattr("jarvis.research.fetch_public_document", lambda _url, **_kwargs: (
        "https://example.com/report", html, 200, "text/html"))
    source = read_source("https://example.com/report", "Bericht")
    assert source["published_at"] == expected


def test_search_falls_back_when_public_html_has_no_results(monkeypatch):
    calls = []

    def fetch(url):
        calls.append(url)
        if "duckduckgo" in url:
            return url, "<html>Keine Suchtreffer</html>", 200, "text/html"
        return url, ('<div class="mw-search-result-heading">'
                     '<a href="/wiki/Europa_Clipper">Europa Clipper</a></div>'), 200, "text/html"

    monkeypatch.setattr("jarvis.research.fetch_public_document", fetch)
    results, provider = search_public_web("Europa Clipper")
    assert provider == "Wikipedia-Suche"
    assert results == [{"title": "Europa Clipper",
                        "url": "https://en.wikipedia.org/wiki/Europa_Clipper"}]
    assert len(calls) == 2


def test_research_api_persists_verified_claims_and_backup(tmp_path, monkeypatch):
    actual_text = "Die Mission startete im Oktober 2024 und untersucht den Jupitermond Europa. " * 4
    calls = []

    def search(query):
        assert query == "Europa Clipper NASA"
        return ([{"title": "NASA", "url": "https://science.nasa.gov/mission/europa-clipper/"}],
                "DuckDuckGo HTML")

    def source(url, title):
        calls.append(url)
        return {"title": title, "url": url, "text": actual_text,
                "retrieved_at": "2026-10-27T01:00:00+00:00"}

    async def generate(_db, _config, purpose, messages):
        assert purpose == "research"
        assert "[1] Die Mission" in messages[-1]["content"]
        assert _config.max_output_tokens == 160
        return Completion("Die Mission untersucht Europa.", 100, 30, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.search_public_web", search)
    monkeypatch.setattr("jarvis.app.read_source", source)
    monkeypatch.setattr("jarvis.app.generate_text", generate)
    root = tmp_path / "data"
    settings = Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        response = client.post("/research", headers=headers, json={
            "question": "Was untersucht die Mission Europa Clipper?",
            "search_query": "Europa Clipper NASA",
        })
        assert response.status_code == 200, response.text
        report = response.json()
        assert len(report["claims"]) == 1 and len(report["sources"]) == 1
        assert report["claims"][0]["evidence"][0]["quote"] in actual_text
        assert report["interpretation"] == "Die Mission untersucht Europa."
        assert report["search_provider"] == "DuckDuckGo HTML"
        assert report["provider"] == "ollama"
        assert client.get(f"/research/{report['id']}").json() == report
        assert client.get("/research").json()[0]["id"] == report["id"]
        assert client.get("/research/missing").status_code == 404
    assert calls == ["https://science.nasa.gov/mission/europa-clipper/"]
    archive = tmp_path / "backup.zip"
    assert create_backup(root, archive)["schema"] == 55
    target = tmp_path / "restored"
    restore_backup(archive, target)
    assert ResearchStore(Database(target / "jarvis.db")).get(report["id"]) == report


def test_research_cloud_consent_and_no_fabricated_claim(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.app.search_public_web", lambda _query: (
        [{"title": "Quelle", "url": "https://example.org/source"}], "Wikipedia-Suche"))
    monkeypatch.setattr("jarvis.app.read_source", lambda url, title: {
        "url": url, "title": title, "text": "Eine lange Beispielquelle mit überprüfbaren Angaben. " * 5,
        "retrieved_at": "2026-10-27T01:00:00+00:00"})

    async def invalid_generate(*_args):
        return Completion("Nur eine Vermutung.", 10, 10, 0, "reported")

    monkeypatch.setattr("jarvis.app.generate_text", invalid_generate)
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_PROVIDER="openrouter",
                        JARVIS_OPENROUTER_API_KEY="fixture", JARVIS_OPENROUTER_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        payload = {"question": "Was steht in dieser Quelle?"}
        assert client.post("/research", headers=headers, json=payload).status_code == 422
        response = client.post("/research", headers=headers,
                               json={**payload, "allow_cloud": True})
        assert response.status_code == 200, response.text
        report = response.json()
        assert len(report["claims"]) == 1
        assert report["claims"][0]["evidence"][0]["quote"] in report["sources"][0]["text"]
        assert report["interpretation"] == "Nur eine Vermutung."


def test_model_timeout_preserves_real_sources(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.app.search_public_web", lambda _query: (
        [{"title": "Quelle", "url": "https://example.org/source"}], "DuckDuckGo HTML"))
    monkeypatch.setattr("jarvis.app.read_source", lambda url, title: {
        "url": url, "title": title, "text": "Europa Clipper untersucht den Jupitermond Europa. " * 4,
        "retrieved_at": "2026-10-27T01:00:00+00:00"})

    async def timed_out(*_args):
        raise TimeoutError

    monkeypatch.setattr("jarvis.app.generate_text", timed_out)
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        response = client.post("/research", headers=headers,
                               json={"question": "Was untersucht Europa Clipper?"})
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["sources"] and report["claims"]
        assert report["interpretation"] == ""
        assert "90 Sekunden" in report["errors"][0]


def test_empty_and_unreadable_search_results_are_explicit(tmp_path, monkeypatch):
    settings = Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        payload = {"question": "Was steht in der Quelle?"}
        monkeypatch.setattr("jarvis.app.search_public_web",
                            lambda _query: ([], "Wikipedia-Suche"))
        empty = client.post("/research", headers=headers, json=payload)
        assert empty.status_code == 404
        assert "Keine Suchtreffer" in empty.json()["detail"]
        monkeypatch.setattr("jarvis.app.search_public_web", lambda _query: (
            [{"title": "Gesperrt", "url": "https://example.org/unreadable"}],
            "DuckDuckGo HTML"))

        def unreadable(_url, _title):
            raise ValueError("Webseite antwortete mit HTTP 403")

        monkeypatch.setattr("jarvis.app.read_source", unreadable)
        failed = client.post("/research", headers=headers, json=payload)
        assert failed.status_code == 502
        assert "Keiner der Suchtreffer" in failed.json()["detail"]
        assert client.get("/research").json() == []
