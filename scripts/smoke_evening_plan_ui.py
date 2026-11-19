"""Isolierter Edge-Klicktest für Abendplan, Replan und Mobilansicht."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from smoke_ui import ROOT, port_free, stop_test_server, wait_ready

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database
from jarvis.tasks import TaskStore
from jarvis.time_model import ExceptionInput, TimeModelStore


def block(store: TimeModelStore, start: str, end: str, before: int = 0, after: int = 0) -> None:
    store.create_exception(ExceptionInput(
        date_local="2027-01-04", mode="block", title="Termin", kind="other",
        start_time=start, end_time=end, timezone="Europe/Paris",
        buffer_before=before, buffer_after=after))


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-evening-plan-ui-") as temporary:
        root = Path(temporary)
        database = Database(root / "data" / "jarvis.db")
        tasks = TaskStore(database)
        tasks.create("Niedrig", "", None, "Europe/Paris")
        tasks.create("Hoch", "", None, "Europe/Paris")
        time_model = TimeModelStore(database)
        block(time_model, "19:00", "20:00", 10, 10)
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
                        page = browser.new_page(viewport={"width": 390, "height": 844})
                        page.set_default_timeout(60_000)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Abendplanung", exact=True).click()
                        page.get_by_label("Datum", exact=True).fill("2027-01-04")
                        page.get_by_label("Schlafziel").fill("21:00")
                        page.locator(".plan-task").filter(has_text="Niedrig").locator("input").check()
                        page.locator(".plan-task").filter(has_text="Hoch").locator("input").check()
                        high = page.locator(".plan-item").filter(has_text="Hoch")
                        high.get_by_label("Schätzung (Minuten)").fill("120")
                        high.get_by_label("Priorität (1–5)").fill("5")
                        low = page.locator(".plan-item").filter(has_text="Niedrig")
                        low.get_by_label("Schätzung (Minuten)").fill("120")
                        low.get_by_label("Priorität (1–5)").fill("1")
                        page.get_by_role("button", name="Plan berechnen").click()
                        preview = page.get_by_role("region", name="Planvorschau")
                        expect(preview).to_be_visible(timeout=60_000)
                        expect(preview).to_contain_text("Frei: 100 Min. Eingeplant: 90 Min. Pausen: 10 Min.")
                        expect(preview).to_contain_text("Hoch: 30 Min. offen")
                        expect(preview).to_contain_text("Niedrig: 120 Min. offen")
                        page.get_by_role("button", name="Plan speichern", exact=True).click()
                        expect(page.get_by_text("Abendplan gespeichert.")).to_be_visible()
                        expect(page.get_by_text("Gespeichert · Version 1")).to_be_visible()
                        block(time_model, "18:00", "18:30")
                        page.get_by_role("button", name="2027-01-04 · 18:00–21:00 · Version 1").click()
                        expect(page.get_by_text("Aufgaben oder Zeitblöcke wurden geändert.", exact=False)).to_be_visible()
                        page.get_by_role("button", name="Plan berechnen").click()
                        expect(preview).to_contain_text("Frei: 70 Min.")
                        page.get_by_role("button", name="Neu berechnen und speichern").click()
                        expect(page.get_by_text("Gespeichert · Version 2")).to_be_visible()
                        page.reload()
                        page.get_by_role("button", name="Abendplanung", exact=True).click()
                        page.get_by_role("button", name="2027-01-04 · 18:00–21:00 · Version 2").click()
                        expect(preview).to_contain_text("Frei: 70 Min.")
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                        print(json.dumps({"plan": "saved_replanned_reloaded", "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
