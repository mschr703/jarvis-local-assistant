"""Check the generic personal profile label and its persisted UI setting."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, stop_test_server, wait_ready


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-public-profile-ui-") as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(root / "data"), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct",
                           JARVIS_PERSONAL_STYLE_LABEL="Mein Stil")
        with (root / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                                       cwd=ROOT, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT)
            try:
                wait_ready(process, timeout=120)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge", headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 390, "height": 844})
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        expect(page.get_by_label("Schreibprofil Mein Stil", exact=True)).to_be_visible()
                        page.get_by_role("button", name="Übersicht", exact=True).click()
                        page.get_by_label("Name für persönliches Schreibprofil").fill("Eigener Stil")
                        page.get_by_role("button", name="Einstellungen speichern", exact=True).click()
                        expect(page.get_by_text("Gespeichert.", exact=True)).to_be_visible()
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        expect(page.get_by_label("Schreibprofil Eigener Stil", exact=True)).to_be_visible()
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        expect(page.get_by_label("Schreibprofil Eigener Stil", exact=True)).to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"default_label": True, "persisted_rename": True,
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
