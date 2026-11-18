"""Isolated Edge path for changed originals in both local search views."""

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
    with TemporaryDirectory(prefix="jarvis-stale-search-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed"
        allowed.mkdir()
        original = allowed / "Vertrag_Orion.md"
        original.write_text("Projekt Orion: Die Miete beträgt 900 Euro.", encoding="utf-8")
        db = Database(root / "data" / "jarvis.db")
        docs = DocumentStore(db)
        folder = docs.add_folder(str(allowed), "local_only")
        assert docs.index_folder(folder["id"])["indexed"] == 1
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(db.path.parent), JARVIS_PROVIDER="ollama",
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
                        page = browser.new_page(viewport={"width": 390, "height": 844})
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded",
                                  timeout=120000)
                        page.get_by_role("button", name="Dokumente", exact=True).click()

                        def document_search() -> None:
                            page.get_by_label("Dokumente durchsuchen").fill("Orion")
                            page.get_by_role("button", name="Suchen", exact=True).click()

                        document_search()
                        source = page.locator(".source-result").filter(has_text="Vertrag_Orion.md")
                        expect(source.get_by_role("checkbox")).to_be_enabled(timeout=30000)
                        original.write_text("Projekt Orion: Die Miete beträgt 700 Euro.", encoding="utf-8")
                        document_search()
                        expect(source.get_by_role("checkbox")).to_be_disabled(timeout=30000)
                        expect(source.get_by_text("Original geändert oder fehlt", exact=False)).to_be_visible()
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Gemeinsame Suche").fill("Orion")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        results = page.get_by_role("region", name="Suchergebnisse")
                        expect(results.get_by_text("0 Dokumente · 0 Erinnerungen · 0 Arbeitsstände")).to_be_visible(
                            timeout=30000)
                        expect(results.get_by_text("Originaldateien und Index weichen ab")).to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_role("button", name="Index aktualisieren").click()
                        expect(page.get_by_text("Index aktualisiert: 1 neu/geändert", exact=False)).to_be_visible(
                            timeout=30000)
                        expect(page.get_by_role("button", name="Index aktualisieren")).to_be_enabled()
                        document_search()
                        expect(source.get_by_role("checkbox")).to_be_enabled(timeout=30000)
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        expect(results.get_by_text("1 Dokumente · 0 Erinnerungen · 0 Arbeitsstände")).to_be_visible(
                            timeout=30000)
                        expect(results.get_by_text("Originaldateien und Index weichen ab")).to_have_count(0)
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"changed": "marked", "selection": "blocked",
                                          "unified": "stale_separate", "reindex": "current",
                                          "mobile_width": "fits", "model_calls": 0}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
