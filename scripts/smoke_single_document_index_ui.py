"""Reindex only one changed local document through the real Edge UI."""

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
    with TemporaryDirectory(prefix="jarvis-single-index-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed"
        allowed.mkdir()
        first, second = allowed / "Erster.md", allowed / "Zweiter.md"
        first.write_text("Projekt Orion: Altwort als Beleg.", encoding="utf-8")
        second.write_text("Projekt Orion: Nachbarwort bleibt bestehen.", encoding="utf-8")
        db = Database(root / "data" / "jarvis.db")
        documents = DocumentStore(db)
        folder = documents.add_folder(str(allowed), "local_only")
        assert documents.index_folder(folder["id"])["indexed"] == 2
        indexed = {item["path"]: item for item in documents.documents()}
        neighbor = indexed[str(second)]
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
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        first_row = page.locator(".document-row").filter(has_text="Erster.md")
                        expect(first_row).to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        first.write_text("Projekt Orion: Neuwort als Beleg.", encoding="utf-8")
                        first_row.get_by_role("button", name="Diese Datei neu indexieren").click()
                        expect(page.get_by_role("status").filter(has_text="Datei neu indexiert"))\
                            .to_be_visible()
                        expect(first_row).to_contain_text("Indexstand:")
                        page.get_by_label("Dokumente durchsuchen").fill("Neuwort")
                        page.get_by_role("button", name="Suchen", exact=True).click()
                        expect(page.get_by_text("Neuwort als Beleg", exact=False)).to_be_visible()
                        first_row.get_by_role("button", name="Diese Datei neu indexieren").click()
                        expect(page.get_by_role("status").filter(has_text="Datei ist unverändert"))\
                            .to_be_visible()
                        assert documents.get_document(neighbor["id"]) == neighbor
                        first.unlink()
                        first_row.get_by_role("button", name="Diese Datei neu indexieren").click()
                        expect(page.get_by_role("status").filter(
                            has_text="dieser Eintrag wurde aus dem Index entfernt")).to_be_visible()
                        expect(first_row).to_have_count(0)
                        expect(page.locator(".document-row").filter(has_text="Zweiter.md"))\
                            .to_be_visible()
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        expect(page.locator(".document-row")).to_have_count(1)
                        assert not overflow_nodes(page), overflow_nodes(page)
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                            assert connection.execute(
                                "SELECT COUNT(*) FROM activity WHERE kind='folder_indexed'").fetchone()[0] == 1
                        print(json.dumps({"changed": "reindexed", "unchanged": "preserved",
                                          "missing": "removed", "neighbor": "preserved",
                                          "folder_scan": "not_repeated", "reload": "persists",
                                          "model_calls": 0, "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
