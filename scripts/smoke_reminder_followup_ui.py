"""Isolierter Edge-Klicktest für Priorität, Verschieben und Bestätigung."""

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


def refresh_tasks(page) -> None:
    page.get_by_role("button", name="Übersicht", exact=True).click()
    page.locator("nav button").filter(has_text="Aufgaben").click()


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-reminder-followup-ui-") as temporary:
        root = Path(temporary)
        data = root / "data"
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama",
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
                        page.locator("nav button").filter(has_text="Aufgaben").click()
                        page.get_by_label("Aufgabe", exact=True).fill("DST-Vorschauprobe")
                        page.get_by_label("Lokale Fälligkeit").fill("2027-10-31T02:30")
                        page.get_by_label("IANA-Zeitzone").fill("Europe/Paris")
                        try:
                            expect(page.get_by_text("Diese lokale Zeit kommt zweimal vor", exact=False)).to_be_visible(timeout=60_000)
                        except AssertionError as exc:
                            raise AssertionError(f"DST-Vorschau fehlt: {page.locator('main').inner_text()[:3000]}") from exc
                        page.get_by_label("Lokale Fälligkeit").fill("")
                        page.get_by_label("Aufgabe", exact=True).fill("Wichtige UI-Abgabe")
                        page.get_by_label("Aufgabenpriorität").select_option("5")
                        page.get_by_label("Lokale Fälligkeit").fill("2027-01-05T09:00")
                        expect(page.get_by_text("Fällig: 2027-01-05 09:00", exact=False)).to_be_visible()
                        page.get_by_role("button", name="Aufgabe speichern").click()
                        expect(page.get_by_text("Aufgabe gespeichert.", exact=False)).to_be_visible()
                        task_row = page.locator(".item-row").filter(has_text="Wichtige UI-Abgabe").last
                        expect(task_row).to_contain_text("Priorität 5")

                        tasks = TaskStore(Database(data / "jarvis.db"))
                        assert tasks.fire_due("2027-01-05T08:00:00+00:00") == 1
                        refresh_tasks(page)
                        new = page.get_by_role("heading", name="Neue Erinnerungen").locator("..")
                        expect(new).to_contain_text("Wichtige UI-Abgabe · Wichtig")
                        new.get_by_label("Erinnerung Wichtige UI-Abgabe verschieben um").select_option("15")
                        new.get_by_role("button", name="Verschieben").click()
                        postponed = page.get_by_role("heading", name="Zurückgestellte Erinnerungen").locator("..")
                        expect(postponed).to_contain_text("Verschoben bis")
                        notification = tasks.notifications()[0]
                        assert notification["snoozed_until"]
                        assert tasks.fire_due(notification["snoozed_until"]) == 1
                        refresh_tasks(page)
                        new = page.get_by_role("heading", name="Neue Erinnerungen").locator("..")
                        expect(new).to_contain_text("Wichtige UI-Abgabe · Wichtig")
                        new.get_by_role("button", name="Gesehen").click()
                        history = page.get_by_role("heading", name="Erinnerungsverlauf").locator("..")
                        expect(history).to_contain_text("Wichtige UI-Abgabe")
                        due = tasks.notifications()[0]["followup_due_utc"]
                        assert due and tasks.fire_due(due) == 1
                        refresh_tasks(page)
                        new = page.get_by_role("heading", name="Neue Erinnerungen").locator("..")
                        expect(new).to_contain_text("Nachfrage 1 von höchstens 2")
                        new.get_by_role("button", name="Bestätigen").click()
                        expect(page.get_by_role("heading", name="Neue Erinnerungen")).to_be_hidden()
                        assert tasks.fire_due("2027-01-06T00:00:00+00:00") == 0
                        page.reload()
                        page.locator("nav button").filter(has_text="Aufgaben").click()
                        expect(page.get_by_role("heading", name="Erinnerungsverlauf").locator("..")).to_contain_text("Bestätigt am")
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                        print(json.dumps({"priority": 5, "snooze": "redelivered_once",
                                          "followup": "acknowledged_after_first", "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
