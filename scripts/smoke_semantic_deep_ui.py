"""Exercise later-passage semantic retrieval from one verified document in Edge."""

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
    with TemporaryDirectory(prefix="jarvis-semantic-deep-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed"
        allowed.mkdir()
        source = allowed / "Langer_Vertrag.md"
        prelude = "Garten und Blumen. " * 320
        source.write_text(prelude + "Die Kaution beträgt 900 Euro.", encoding="utf-8")
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
                        page = browser.new_page(viewport={"width": 390, "height": 844})
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded",
                                  timeout=120000)
                        page.get_by_role("button", name="Suche", exact=True).click()
                        selector = page.get_by_label("Einzelnes Dokument tief durchsuchen")
                        expect(selector.locator("option")).to_have_count(2, timeout=30000)
                        page.get_by_label("Gemeinsame Suche").fill("Wie hoch ist die Kaution?")
                        button = page.get_by_role("button", name="Sinngemäß lokal suchen")
                        button.click()
                        results = page.get_by_role("region", name="Sinngemäße Suchergebnisse")
                        expect(results.locator("article").first).to_be_visible(timeout=120000)
                        assert "Kaution beträgt" not in results.locator("article").first.inner_text()

                        selector.select_option(document["id"])
                        button.click()
                        expect(results.get_by_text("5 Fundstellen aus Langer_Vertrag.md")).to_be_visible(timeout=120000)
                        expect(results.locator("article").first).to_contain_text("Kaution beträgt 900 Euro")
                        expect(results.get_by_role("link", name="Geprüftes Original herunterladen").first).to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)

                        source.write_text(prelude + "Die Kaution beträgt 1200 Euro.",
                                          encoding="utf-8")
                        button.click()
                        expect(results.get_by_text("Originaldateien und Index weichen ab")).to_be_visible(timeout=120000)
                        assert "Kaution beträgt 900 Euro" not in results.inner_text()
                        assert docs.index_folder(folder["id"])["indexed"] == 1
                        button.click()
                        expect(results.locator("article").first).to_contain_text("Kaution beträgt 1200 Euro", timeout=120000)
                        assert not overflow_nodes(page), overflow_nodes(page)
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"later_passage": "found", "stale": "blocked",
                                          "reindexed": "found", "mobile_width": "fits",
                                          "cloud_calls": 0}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
