"""Isolated Edge flow for a user-selected extra document source."""

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
    with TemporaryDirectory(prefix="jarvis-source-preference-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed"
        allowed.mkdir()
        original = allowed / "Rechnung_Mars.md"
        original.write_text("Die Summe beträgt 300 Euro.", encoding="utf-8")
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
                        page.get_by_role("button", name="Suche", exact=True).click()
                        query = "Welche Kosten hat Projekt Venus?"

                        def search() -> None:
                            page.get_by_label("Gemeinsame Suche").fill(query)
                            page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                            expect(page.get_by_role("region", name="Suchergebnisse")).to_be_visible(
                                timeout=30000)

                        search()
                        results = page.get_by_role("region", name="Suchergebnisse")
                        expect(results.get_by_text("Keine passenden gespeicherten Quellen gefunden.")).to_be_visible()
                        page.get_by_label("Zusätzliches Dokument").select_option(label=str(original.resolve()))
                        page.get_by_role("button", name="Dieses Dokument berücksichtigen").click()
                        expect(results.get_by_text("1 Dokumente · 0 Erinnerungen · 0 Arbeitsstände")).to_be_visible(timeout=30000)
                        expect(results.get_by_text("bewusst vorgemerkt", exact=False)).to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)

                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Suche", exact=True).click()
                        search()
                        expect(results.get_by_text("bewusst vorgemerkt", exact=False)).to_be_visible(timeout=30000)
                        original.write_text("Die Summe beträgt jetzt 400 Euro.", encoding="utf-8")
                        search()
                        expect(results.get_by_text("Original und Index weichen ab", exact=False)).to_be_visible(timeout=30000)
                        expect(results.get_by_text("0 Dokumente · 0 Erinnerungen · 0 Arbeitsstände")).to_be_visible()
                        assert docs.index_folder(folder["id"])["indexed"] == 1
                        search()
                        expect(results.get_by_text("Seit der Vormerkung neu indexiert", exact=False)).to_be_visible(timeout=30000)
                        expect(results.get_by_text("1 Dokumente · 0 Erinnerungen · 0 Arbeitsstände")).to_be_visible()
                        results.get_by_role("button", name="Vormerkung entfernen").click()
                        expect(results.get_by_text("Noch keine zusätzliche Quelle vorgemerkt.")).to_be_visible(timeout=30000)
                        expect(results.get_by_text("Keine passenden gespeicherten Quellen gefunden.")).to_be_visible()
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"preferred": "shown", "reload": "persists",
                                          "changed": "flagged", "removed": "gone",
                                          "mobile_width": "fits", "model_calls": 0}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
