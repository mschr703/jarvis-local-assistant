"""Isolierter Edge-Klicktest für Fokus, wichtige Ausnahmen und Sammlung."""

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


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Fokus-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-focus-ui-") as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(root / "data"), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct",
                           JARVIS_TIMEZONE="Europe/Paris")
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
                        page.set_default_timeout(30_000)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.locator("nav button").filter(has_text="Aufgaben").click()
                        focus = page.get_by_role("heading", name="Fokusmodus").locator("..")
                        focus.get_by_role("button", name="Manuellen Fokus einschalten").click()
                        expect(focus.get_by_text("Aktiv: wichtige Erinnerungen", exact=False)).to_be_visible()
                        focus.get_by_label("Fokusbeginn").fill("22:00")
                        focus.get_by_label("Fokusende").fill("07:00")
                        focus.get_by_label("Fokus-Zeitzone").fill("Europe/Paris")
                        focus.get_by_role("button", name="Fokuszeiten speichern").click()
                        expect(page.get_by_text("Fokusmodus aktiv.", exact=False)).to_be_visible()
                        tasks = TaskStore(Database(root / "data" / "jarvis.db"))
                        low = tasks.create("Normaler Hinweis", "", "2027-01-05T09:00",
                                           "Europe/Paris", priority=2)
                        high = tasks.create("Wichtiger Hinweis", "", "2027-01-05T09:00",
                                            "Europe/Paris", priority=5)
                        assert tasks.fire_due("2027-01-05T08:00:00+00:00") == 2
                        page.reload()
                        expect(page.get_by_text("Fokusmodus aktiv", exact=True)).to_be_visible()
                        expect(page.get_by_text("1 neue Erinnerung", exact=False)).to_be_visible()
                        page.locator("nav button").filter(has_text="Aufgaben").click()
                        new = page.get_by_role("heading", name="Neue Erinnerungen").locator("..")
                        expect(new).to_contain_text("Wichtiger Hinweis")
                        expect(new).not_to_contain_text("Normaler Hinweis")
                        held = page.get_by_role("heading", name="Im Fokus gesammelt").locator("..")
                        expect(held).to_contain_text("Normaler Hinweis")
                        high_notice = next(item for item in tasks.notifications()
                                           if item["task_id"] == high["id"])
                        low_notice = next(item for item in tasks.notifications()
                                          if item["task_id"] == low["id"])
                        assert high_notice["focus_deferred_at"] is None
                        assert low_notice["focus_deferred_at"]
                        new.get_by_role("button", name="Bestätigen").click()
                        focus.get_by_role("button", name="Manuellen Fokus ausschalten").click()
                        after = page.get_by_role("heading", name="Nach dem Fokus gesammelt").locator("..")
                        expect(after).to_contain_text("Normaler Hinweis")
                        page.locator("nav button").filter(has_text="Übersicht").click()
                        expect(page.get_by_text("davon 1 im Fokus gesammelt", exact=False)).to_be_visible()
                        page.locator("nav button").filter(has_text="Aufgaben").click()
                        after.get_by_role("button", name="Gesehen").click()
                        page.reload()
                        expect(page.get_by_text("1 neue Erinnerung", exact=False)).to_be_hidden()
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                        print(json.dumps({"urgent": "immediate", "normal": "deferred_then_summary",
                                          "high_ack": high_notice["id"],
                                          "normal_read": low_notice["id"], "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
