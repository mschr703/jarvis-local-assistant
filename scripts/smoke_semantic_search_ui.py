"""Exercise optional local embedding search in Edge with a real Ollama model."""

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
    with TemporaryDirectory(prefix="jarvis-semantic-search-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed"
        allowed.mkdir()
        agreement = allowed / "Wohnung.md"
        agreement.write_text("Die Vereinbarung zur Wohnung liegt im roten Ordner.",
                             encoding="utf-8")
        (allowed / "Kuchen.md").write_text("Das Rezept für Apfelkuchen enthält Zimt.",
                                          encoding="utf-8")
        data = root / "data"
        db = Database(data / "jarvis.db")
        docs = DocumentStore(db)
        folder = docs.add_folder(str(allowed), "local_only")
        assert docs.index_folder(folder["id"])["indexed"] == 2
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
                        folder_select = page.get_by_label("Ordner für sinngemäße Suche")
                        expect(folder_select.locator("option")).to_have_count(2, timeout=30000)
                        folder_select.select_option(folder["id"])
                        page.get_by_label("Gemeinsame Suche").fill("Wo liegt mein Mietvertrag?")
                        page.get_by_role("button", name="Sinngemäß in Dokumenten suchen").click()
                        results = page.get_by_role("region", name="Sinngemäße Suchergebnisse")
                        expect(results.locator("article").first).to_contain_text("Wohnung.md", timeout=120000)
                        expect(results.locator("article").first).to_contain_text("SHA-256")
                        expect(results.locator("article").first).to_contain_text("Ähnlichkeit")
                        assert not overflow_nodes(page), overflow_nodes(page)

                        agreement.write_text("Die neue Vereinbarung liegt im blauen Ordner.",
                                             encoding="utf-8")
                        page.get_by_role("button", name="Sinngemäß in Dokumenten suchen").click()
                        expect(results.get_by_text("Originaldateien und Index weichen ab")).to_be_visible(timeout=120000)
                        assert "roten Ordner" not in results.inner_text()
                        assert docs.index_folder(folder["id"])["indexed"] == 1
                        page.get_by_role("button", name="Sinngemäß in Dokumenten suchen").click()
                        expect(results.get_by_text("blauen Ordner", exact=False)).to_be_visible(timeout=120000)
                        assert not overflow_nodes(page), overflow_nodes(page)
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"synonym": "found", "stale": "excluded",
                                          "reindexed": "found", "mobile_width": "fits",
                                          "cloud_calls": 0}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
