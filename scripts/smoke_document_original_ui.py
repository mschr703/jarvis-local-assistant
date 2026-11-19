"""Verify local original downloads from both search modes in Edge."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from smoke_code_project_ui import wait_ui
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, stop_test_server

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database
from jarvis.documents import DocumentStore


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-document-original-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed"
        allowed.mkdir()
        source = allowed / "Mietvertrag_Übung.md"
        source.write_text("Die Vereinbarung zur Wohnung gilt ab Sonntag.", encoding="utf-8")
        data = root / "data"
        db = Database(data / "jarvis.db")
        docs = DocumentStore(db)
        folder = docs.add_folder(str(allowed), "local_only")
        docs.index_folder(folder["id"])
        document = docs.documents()[0]
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="fixture")
        with (root / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                                       cwd=ROOT, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT)
            try:
                wait_ui(process)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge", headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 390, "height": 844},
                                                accept_downloads=True)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded",
                                  timeout=120000)
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Gemeinsame Suche").fill("Mietvertrag")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        results = page.get_by_role("region", name="Suchergebnisse")
                        link = results.get_by_role("link", name="Geprüftes Original herunterladen")
                        expect(link).to_be_visible(timeout=30000)
                        with page.expect_download() as attempt:
                            link.click()
                        download = attempt.value
                        assert download.suggested_filename == source.name
                        saved = root / "lexical.md"
                        download.save_as(saved)
                        assert saved.read_bytes() == source.read_bytes()

                        source.write_text("Die Vereinbarung zur Wohnung gilt ab Freitag.",
                                          encoding="utf-8")
                        response = page.request.get(
                            f"http://127.0.0.1:5173/api/documents/{document['id']}/original")
                        assert response.status == 409
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        expect(results.get_by_text("Originaldateien und Index weichen ab")).to_be_visible(timeout=30000)
                        expect(results.get_by_role("link", name="Geprüftes Original herunterladen")).to_have_count(0)

                        assert docs.index_folder(folder["id"])["indexed"] == 1
                        page.get_by_role("button", name="Sinngemäß lokal suchen").click()
                        semantic = page.get_by_role("region", name="Sinngemäße Suchergebnisse")
                        semantic_link = semantic.get_by_role("link", name="Geprüftes Original herunterladen")
                        expect(semantic_link).to_be_visible(timeout=120000)
                        with page.expect_download() as attempt:
                            semantic_link.click()
                        saved = root / "semantic.md"
                        attempt.value.save_as(saved)
                        assert saved.read_bytes() == source.read_bytes()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"lexical": "downloaded", "stale": "blocked",
                                          "semantic": "downloaded", "mobile_width": "fits",
                                          "cloud_calls": 0}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
