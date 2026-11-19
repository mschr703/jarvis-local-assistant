"""Exercise the bounded, read-only folder freshness report in real Edge."""

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
    with TemporaryDirectory(prefix="jarvis-freshness-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed"
        allowed.mkdir()
        source = allowed / "Vertrag_Orion.md"
        source.write_text("Projekt Orion: Die Miete beträgt 900 Euro.", encoding="utf-8")
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
                        check = page.get_by_role("button", name="Frische prüfen")
                        check.click()
                        expect(page.locator("small").filter(has_text="Frischecheck: 1 aktuell, 0 geändert")).to_be_visible(
                            timeout=30000)
                        source.write_text("Projekt Orion: Die Miete beträgt 700 Euro.", encoding="utf-8")
                        check.click()
                        expect(page.locator("small").filter(has_text="Frischecheck: 0 aktuell, 1 geändert")).to_be_visible(
                            timeout=30000)
                        expect(page.get_by_text(f"Neu indexieren: {source}")).to_be_visible()
                        assert len(docs.documents()) == 1
                        page.get_by_role("button", name="Index aktualisieren").click()
                        expect(page.get_by_text("Index aktualisiert: 1 neu/geändert", exact=False)).to_be_visible(
                            timeout=30000)
                        check.click()
                        expect(page.locator("small").filter(has_text="Frischecheck: 1 aktuell, 0 geändert")).to_be_visible(
                            timeout=30000)
                        assert not overflow_nodes(page), overflow_nodes(page)
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"changed": "detected", "reindex": "current",
                                          "mobile_width": "fits", "model_calls": 0}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
