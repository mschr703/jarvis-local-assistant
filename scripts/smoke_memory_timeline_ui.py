"""Exercise the manually confirmed life timeline in real Edge at mobile width."""

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


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Lebenschronik-Test stoppen")
    with TemporaryDirectory(prefix="jarvis-timeline-ui-") as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(root / "data"), JARVIS_PROVIDER="ollama",
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
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Lebenschronik", exact=True).click()
                        expect(page.get_by_role("heading", name="Lebenschronik"))\
                            .to_be_visible()
                        page.get_by_label("Ereignisdatum").fill("2024-08-12")
                        page.get_by_label("Ereignistitel").fill("Café in Lyon")
                        page.get_by_label("Ereignisort").fill("Lyon")
                        page.get_by_label("Ereignisbeschreibung").fill("Mit Alex am Fluss")
                        page.get_by_role("button", name="Ereignis speichern").click()
                        expect(page.get_by_text("2024-08-12 · Café in Lyon"))\
                            .to_be_visible()
                        page.get_by_label("Ereignisdatum").fill("2023-01-03")
                        page.get_by_label("Ereignistitel").fill("Winterfahrt")
                        page.get_by_label("Ereignisort").fill("Zürich")
                        page.get_by_role("button", name="Ereignis speichern").click()
                        expect(page.locator(".item-list .item-row")).to_have_count(2)
                        page.get_by_label("Erlebnisse durchsuchen").fill("cafe")
                        page.get_by_role("button", name="Suchen").click()
                        row = page.locator(".item-list .item-row")
                        expect(row).to_have_count(1)
                        expect(row).to_contain_text("Café in Lyon")
                        row.get_by_role("button", name="Bearbeiten").click()
                        page.get_by_label("Ereignistitel").fill("Café am Fluss")
                        page.get_by_role("button", name="Änderung speichern").click()
                        expect(page.get_by_text("Ereignis gespeichert.")).to_be_visible()
                        page.get_by_label("Erlebnisse durchsuchen").fill("Lyon")
                        page.get_by_role("button", name="Suchen").click()
                        expect(row).to_contain_text("Café am Fluss")
                        with page.expect_download() as download_info:
                            page.get_by_role("button", name="Gefilterte Zeitleiste als Markdown herunterladen").click()
                        download = download_info.value
                        assert download.suggested_filename == "lebenschronik.md"
                        content = Path(download.path()).read_text(encoding="utf-8")
                        assert "Café am Fluss" in content and "Winterfahrt" not in content
                        page.once("dialog", lambda dialog: dialog.accept())
                        row.get_by_role("button", name="Löschen").click()
                        expect(row).to_have_count(0)
                        page.get_by_label("Erlebnisse durchsuchen").fill("")
                        page.get_by_role("button", name="Suchen").click()
                        expect(row).to_have_count(1)
                        expect(row).to_contain_text("Winterfahrt")
                        assert not overflow_nodes(page), overflow_nodes(page)
                        db = Database(Path(environment["JARVIS_DATA_DIR"]) / "jarvis.db")
                        with db.connect() as connection:
                            assert connection.execute("SELECT count(*) FROM memory_events").fetchone()[0] == 1
                            assert connection.execute("SELECT count(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"create": 2, "search": "accent_insensitive",
                                          "correct": True, "download": "filtered_markdown",
                                          "delete": True, "model_usage": 0,
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
