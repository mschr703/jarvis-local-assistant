"""A saved document preference reaches bounded semantic retrieval in Edge/Ollama."""

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
    with TemporaryDirectory(prefix="jarvis-semantic-preference-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed"
        allowed.mkdir()
        for number in range(36):
            (allowed / f"a_{number:02}.md").write_text(
                f"Gartenbeet und Zitronenbaum Nummer {number}.", encoding="utf-8")
        target = allowed / "z_Vertrag.md"
        target.write_text("Die Kaution beträgt 900 Euro.", encoding="utf-8")
        db = Database(root / "data" / "jarvis.db")
        docs = DocumentStore(db)
        folder = docs.add_folder(str(allowed), "local_only")
        assert docs.index_folder(folder["id"])["indexed"] == 37
        target_id = next(item["id"] for item in docs.documents() if item["path"] == str(target))
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
                        page.get_by_label("Gemeinsame Suche").fill("Wie hoch ist die Kaution?")
                        semantic_button = page.get_by_role("button", name="Sinngemäß lokal suchen")
                        semantic_button.click()
                        semantic = page.get_by_role("region", name="Sinngemäße Suchergebnisse")
                        expect(semantic).to_be_visible(timeout=120000)
                        assert "z_Vertrag.md" not in semantic.inner_text()

                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        results = page.get_by_role("region", name="Suchergebnisse")
                        expect(results).to_be_visible(timeout=30000)
                        page.get_by_label("Zusätzliches Dokument").select_option(target_id)
                        page.get_by_role("button", name="Dieses Dokument berücksichtigen").click()
                        expect(results.get_by_text("Aktueller gespeicherter Stand", exact=False)).to_be_visible(
                            timeout=30000)
                        semantic_button.click()
                        expect(semantic.locator("article").first).to_contain_text(
                            "z_Vertrag.md · bewusst vorgemerkt", timeout=120000)
                        expect(semantic.locator("article").first).to_contain_text("Kaution beträgt 900 Euro")

                        target.write_text("Die Kaution beträgt 1200 Euro.", encoding="utf-8")
                        semantic_button.click()
                        expect(semantic.get_by_text("Vorgemerkte Quellen nicht aktuell")).to_be_visible(
                            timeout=120000)
                        assert "Kaution beträgt 900 Euro" not in semantic.inner_text()
                        assert docs.index_folder(folder["id"])["indexed"] == 1
                        semantic_button.click()
                        expect(semantic.locator("article").first).to_contain_text(
                            "Kaution beträgt 1200 Euro", timeout=120000)
                        expect(semantic.locator("article").first).to_contain_text(
                            "seit Vormerkung neu indexiert")
                        assert not overflow_nodes(page), overflow_nodes(page)
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"preferred_late": "found", "stale": "blocked",
                                          "updated": "marked", "mobile_width": "fits",
                                          "cloud_calls": 0}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
