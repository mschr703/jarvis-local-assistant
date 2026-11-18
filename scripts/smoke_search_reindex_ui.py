"""Reindex a stale source from lexical and semantic search in real Edge/Ollama."""

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
    with TemporaryDirectory(prefix="jarvis-search-reindex-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed"
        allowed.mkdir()
        original = allowed / "Orion.md"
        neighbor = allowed / "Nachbar.md"
        original.write_text("Orion startet mit dem Stichwort Altwort.", encoding="utf-8")
        neighbor.write_text("Der Nachbar bleibt unverändert.", encoding="utf-8")
        db = Database(root / "data" / "jarvis.db")
        documents = DocumentStore(db)
        folder = documents.add_folder(str(allowed), "local_only")
        assert documents.index_folder(folder["id"])["indexed"] == 2
        indexed = {item["path"]: item for item in documents.documents()}
        neighbor_before = indexed[str(neighbor)]
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
                        page.get_by_label("Quelltyp der gemeinsamen Suche").select_option("document")
                        query = page.get_by_label("Gemeinsame Suche")
                        query.fill("Altwort")
                        lexical_button = page.get_by_role("button", name="Alle lokalen Quellen durchsuchen")
                        lexical_button.click()
                        lexical = page.get_by_role("region", name="Suchergebnisse")
                        expect(lexical.locator("article").filter(has_text="Altwort")).to_be_visible()
                        original.write_text("Orion enthält jetzt Neuwort und keinen alten Beleg.", encoding="utf-8")
                        lexical_button.click()
                        expect(lexical.get_by_text("Originaldateien und Index weichen ab"))\
                            .to_be_visible()
                        expect(lexical.locator("article").filter(has_text="Altwort"))\
                            .to_have_count(0)
                        lexical.get_by_role("button", name="Diese Datei neu indexieren").click()
                        expect(page.get_by_role("status").filter(has_text="Datei neu indexiert"))\
                            .to_be_visible()
                        expect(lexical.get_by_text("Originaldateien und Index weichen ab"))\
                            .to_have_count(0)
                        expect(lexical.locator("article")).to_have_count(0)
                        query.fill("Neuwort")
                        lexical_button.click()
                        expect(lexical.locator("article").filter(has_text="Neuwort"))\
                            .to_be_visible()

                        semantic_button = page.get_by_role("button", name="Sinngemäß lokal suchen")
                        semantic_button.click()
                        semantic = page.get_by_role("region", name="Sinngemäße Suchergebnisse")
                        expect(semantic.locator("article").filter(has_text="Neuwort"))\
                            .to_be_visible(timeout=120000)
                        original.write_text("Orion enthält jetzt das Stichwort Drittwort.", encoding="utf-8")
                        semantic_button.click()
                        expect(semantic.get_by_text("Originaldateien und Index weichen ab"))\
                            .to_be_visible(timeout=120000)
                        expect(semantic.locator("article").filter(has_text="Orion.md"))\
                            .to_have_count(0)
                        semantic.get_by_role("button", name="Diese Datei neu indexieren").click()
                        expect(semantic).to_have_count(0)
                        expect(page.get_by_role("status").filter(
                            has_text="Sinngemäße Suche bei Bedarf erneut starten"))\
                            .to_be_visible()
                        assert query.input_value() == "Neuwort"
                        query.fill("Drittwort")
                        semantic_button.click()
                        expect(semantic.locator("article").filter(has_text="Drittwort"))\
                            .to_be_visible(timeout=120000)
                        original.unlink()
                        lexical_button.click()
                        expect(lexical.get_by_text("Originaldateien und Index weichen ab"))\
                            .to_be_visible()
                        lexical.get_by_role("button", name="Diese Datei neu indexieren").click()
                        expect(page.get_by_role("status").filter(
                            has_text="Original fehlt; der alte Suchindexeintrag wurde entfernt"))\
                            .to_be_visible()
                        expect(lexical.locator("article").filter(has_text="Orion.md"))\
                            .to_have_count(0)
                        assert documents.get_document(indexed[str(original)]["id"]) is None
                        assert documents.get_document(neighbor_before["id"]) == neighbor_before
                        assert not overflow_nodes(page), overflow_nodes(page)
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"lexical": "refreshed", "semantic": "explicit_rerun",
                                          "stale_excerpt": "hidden", "missing": "removed",
                                          "neighbor": "preserved",
                                          "model_usage": 0, "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
