"""Exercise local contacts and confirmed notes in a real Edge browser."""

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
        raise RuntimeError("Jarvis vor dem isolierten Kontakt-Test stoppen")
    with TemporaryDirectory(prefix="jarvis-contacts-ui-") as temporary:
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
                        page = browser.new_page(viewport={"width": 390, "height": 844})
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Kontakte", exact=True).click()
                        expect(page.get_by_role("heading", name="Kontakte")).to_be_visible()
                        page.get_by_label("Kontaktname").fill("Alex")
                        page.get_by_label("Unterscheidender Kontext").fill("München")
                        page.get_by_label("Beziehung").fill("Freund")
                        page.get_by_role("button", name="Kontakt anlegen").click()
                        expect(page.get_by_role("heading", name="Alex · München")).to_be_visible()
                        page.get_by_label("Neue Kontaktnotiz").fill("Wir wollen ins Museum")
                        page.get_by_role("button", name="Notiz hinzufügen").click()
                        expect(page.get_by_text("Wir wollen ins Museum", exact=True)).to_be_visible()

                        page.get_by_label("Kontaktname").fill("Alex")
                        page.get_by_label("Unterscheidender Kontext").fill("Arbeit")
                        page.get_by_role("button", name="Kontakt anlegen").click()
                        expect(page.get_by_role("heading", name="Alex · Arbeit")).to_be_visible()
                        page.get_by_label("Kontakte durchsuchen").fill("Museum")
                        page.get_by_role("button", name="Suchen").click()
                        rows = page.locator(".item-list .item-row").filter(has_text="Alex")
                        expect(rows).to_have_count(1)
                        expect(rows.first).to_contain_text("München")
                        rows.first.get_by_role("button", name="Öffnen").click()
                        page.get_by_role("button", name="Bearbeiten", exact=True).click()
                        page.get_by_label("Notiz bearbeiten").fill("Wir gehen ins Kino")
                        page.get_by_role("button", name="Notiz speichern").click()
                        expect(page.get_by_text("Wir gehen ins Kino", exact=True)).to_be_visible()
                        page.get_by_role("button", name="Kontakt bearbeiten").click()
                        page.get_by_label("Unterscheidender Kontext").fill("München privat")
                        page.get_by_role("button", name="Änderungen speichern").click()
                        expect(page.get_by_role("heading", name="Alex · München privat"))\
                            .to_be_visible()
                        page.get_by_label("Neues Vorhaben").fill("Museum besuchen")
                        page.get_by_label("Vorhaben-Details").fill("Im Dezember")
                        page.get_by_role("button", name="Vorhaben hinzufügen").click()
                        plan_row = page.locator(".item-row").filter(has_text="Museum besuchen")
                        expect(plan_row).to_contain_text("Offen")
                        plan_row.get_by_role("button", name="Erledigt").click()
                        expect(plan_row).to_contain_text("Erledigt")
                        plan_row.get_by_role("button", name="Bearbeiten").click()
                        page.get_by_label("Vorhaben-Titel bearbeiten").fill("Kunstmuseum besuchen")
                        page.get_by_role("button", name="Vorhaben speichern").click()
                        expect(page.get_by_text("Kunstmuseum besuchen", exact=True))\
                            .to_be_visible()
                        page.get_by_label("Kontakte durchsuchen").fill("Kunstmuseum")
                        page.get_by_role("button", name="Suchen").click()
                        expect(page.locator(".item-list .item-row").filter(has_text="Alex"))\
                            .to_have_count(1)
                        assert not overflow_nodes(page), overflow_nodes(page)
                        db = Database(Path(environment["JARVIS_DATA_DIR"]) / "jarvis.db")
                        with db.connect() as connection:
                            assert connection.execute("SELECT count(*) FROM contacts").fetchone()[0] == 2
                            assert connection.execute("SELECT status FROM contact_plans").fetchone()[0] == "completed"
                            assert connection.execute("SELECT count(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"same_name": "distinct_contexts",
                                          "note": "created_and_corrected",
                                          "plan": "created_completed_and_corrected",
                                          "search": "notes_and_plans",
                                          "model_usage": 0, "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
