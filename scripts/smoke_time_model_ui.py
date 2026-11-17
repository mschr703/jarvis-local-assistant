"""Isolierter Edge-Klicktest für Wochenzeiten, Ausnahmen und freie Tageszeit."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from smoke_ui import ROOT, port_free, stop_test_server, wait_ready


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-time-model-ui-") as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(root / "data"), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct", JARVIS_TIMEZONE="Europe/Paris")
        with (root / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                                       cwd=ROOT, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT)
            try:
                wait_ready(process, timeout=120)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge", headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 390, "height": 800})
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Zeitmodell", exact=True).click()
                        page.get_by_label("Zeitmodell-Datum").fill("2027-01-04")
                        page.get_by_label("Zeitregel-Titel").fill("Arbeit Test")
                        page.get_by_label("Puffer davor", exact=True).fill("15")
                        page.get_by_label("Puffer danach", exact=True).fill("15")
                        page.get_by_role("button", name="Regel anlegen").click()
                        expect(page.get_by_text("930 Minuten frei", exact=False)).to_be_visible()
                        expect(page.get_by_text("Arbeit Test · Arbeit", exact=True).first).to_be_visible()

                        page.get_by_label("Ausnahme-Datum").fill("2027-01-04")
                        page.get_by_label("Ausfallende Regel").select_option(label="Arbeit Test")
                        page.get_by_role("button", name="Ausnahme anlegen").click()
                        expect(page.get_by_text("1440 Minuten frei", exact=False)).to_be_visible()

                        page.get_by_label("Ausnahme-Art").select_option("block")
                        page.get_by_label("Ausnahme-Datum").fill("2027-01-04")
                        page.get_by_label("Ausnahme-Titel").fill("Sport Test")
                        page.get_by_label("Ausnahme-Beginn").fill("19:00")
                        page.get_by_label("Ausnahme-Ende").fill("20:00")
                        page.get_by_label("Ausnahme-Puffer davor").fill("10")
                        page.get_by_label("Ausnahme-Puffer danach").fill("5")
                        page.get_by_role("button", name="Ausnahme anlegen").click()
                        expect(page.get_by_text("1365 Minuten frei", exact=False)).to_be_visible()
                        expect(page.get_by_text("Sport Test · Sonstiges", exact=True)).to_be_visible()

                        rule_row = page.get_by_text("Arbeit Test · Arbeit", exact=True).last.locator("..").locator("..")
                        rule_row.get_by_role("button", name="Ändern").click()
                        page.get_by_label("Zeitregel-Beginn").fill("10:00")
                        page.get_by_label("Zeitregel-Ende").fill("18:00")
                        page.get_by_role("button", name="Regel speichern").click()
                        expect(page.get_by_text("10:00–18:00", exact=False)).to_be_visible()

                        skip_row = page.get_by_text("2027-01-04 · Ausfall: Arbeit Test", exact=True).locator("..").locator("..")
                        skip_row.get_by_role("button", name="Löschen…").click()
                        skip_row.get_by_role("button", name="Löschen bestätigen").click()
                        expect(page.get_by_text("855 Minuten frei", exact=False)).to_be_visible()
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                        print(json.dumps({"rule": "created_and_edited", "skip": "applied_then_deleted",
                                          "extra_block": "with_buffers", "free_minutes": 855,
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
