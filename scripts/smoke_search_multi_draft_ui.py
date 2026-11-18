"""Pick up to five local search passages for one cited draft in Edge."""

import json
import os
import re
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
    with TemporaryDirectory(prefix="jarvis-multi-draft-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed"
        allowed.mkdir()
        for number in range(6):
            (allowed / f"Orion_{number}.md").write_text(
                f"Projekt Orion: Beleg Nummer {number} für den Entwurf.", encoding="utf-8")
        db = Database(root / "data" / "jarvis.db")
        docs = DocumentStore(db)
        folder = docs.add_folder(str(allowed), "local_only")
        assert docs.index_folder(folder["id"])["indexed"] == 6
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
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Gemeinsame Suche").fill("Orion")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        results = page.get_by_role("region", name="Suchergebnisse")
                        expect(results).to_contain_text("6 Dokumente · 0 Erinnerungen · 0 Arbeitsstände")
                        choices = results.get_by_role("checkbox", name=re.compile("für Entwurf auswählen"))
                        expect(choices).to_have_count(6)
                        for index in range(5):
                            choices.nth(index).check()
                        expect(choices.nth(5)).to_be_disabled()
                        choices.nth(0).uncheck()
                        expect(choices.nth(5)).to_be_enabled()
                        choices.nth(5).check()
                        page.get_by_role("button", name="Mit 5 Fundstellen Entwurf starten").click()
                        expect(page.get_by_role("heading", name="Entwürfe", exact=True)).to_be_visible()
                        selected = page.get_by_role("list", name="Ausgewählte Fundstellen")
                        expect(selected.locator("li")).to_have_count(5)
                        expect(selected).to_contain_text("Orion_")
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Gemeinsame Suche").fill("Saturn")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        expect(page.get_by_role("region", name="Fundstellen für Entwurf")).to_have_count(0)
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"selected": 5, "sixth": "enabled_after_removal",
                                          "new_search": "selection_cleared",
                                          "mobile_width": "fits", "model_calls": 0}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
