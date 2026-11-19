"""Find confirmed life events with real local Ollama embeddings in Edge."""

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
from jarvis.contacts import ContactStore
from jarvis.db import Database
from jarvis.memory_events import MemoryEventStore


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Edge-Test stoppen")
    with TemporaryDirectory(prefix="jarvis-semantic-event-ui-") as temporary:
        root = Path(temporary)
        data = root / "data"
        db = Database(data / "jarvis.db")
        events = MemoryEventStore(db)
        person = ContactStore(db).create("Alex", "Lyon privat")
        event = events.create("2024-08-12", "Café am Fluss",
                              "Sommerwochenende mit Alex an der Rhône", "Lyon")
        events.link_contact(event["id"], person["id"], event["version"])
        unrelated = events.create("2024-01-03", "Autowerkstatt",
                                  "Winterreifen wechseln", "Köln")
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama",
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
                        page.get_by_label("Quelltyp der gemeinsamen Suche").select_option(
                            "life_event")
                        assert page.get_by_label("Dokumentordner für sinngemäße Suche").is_disabled()
                        page.get_by_label("Gemeinsame Suche").fill(
                            "Wo war unser Sommerwochenende am Fluss mit Alex?")
                        page.get_by_role("button", name="Sinngemäß lokal suchen").click()
                        results = page.get_by_role("region", name="Sinngemäße Suchergebnisse")
                        expect(results).to_contain_text("2 Lebensereignisse", timeout=120000)
                        expect(results.locator("article").first).to_contain_text("Café am Fluss")
                        expect(results.locator("article").first).to_contain_text("Alex (Lyon privat)")
                        assert "Autowerkstatt" not in results.locator("article").first.inner_text()
                        assert str(root) not in results.inner_text()
                        results.locator("article").first.get_by_role(
                            "button", name="Quelle öffnen").click()
                        expect(page.get_by_role("region", name="Geöffnetes Lebensereignis"))\
                            .to_contain_text("Café am Fluss")
                        assert not overflow_nodes(page), overflow_nodes(page)

                        events.update(event["id"], 2, "2024-08-12", "Café am Fluss",
                                      "Abendessen mit Alex an der Rhône", "Lyon")
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Quelltyp der gemeinsamen Suche").select_option(
                            "life_event")
                        page.get_by_label("Gemeinsame Suche").fill(
                            "Wo war unser Sommerwochenende am Fluss mit Alex?")
                        page.get_by_role("button", name="Sinngemäß lokal suchen").click()
                        results = page.get_by_role("region", name="Sinngemäße Suchergebnisse")
                        expect(results).to_contain_text("Abendessen mit Alex", timeout=120000)
                        assert "Sommerwochenende mit Alex" not in results.inner_text()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        with db.connect() as connection:
                            assert connection.execute("SELECT count(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"events": [event["id"], unrelated["id"]],
                                          "local_model": "embeddinggemma:300m",
                                          "source": "opened", "update": "fresh",
                                          "mobile_width": "fits", "cloud_calls": 0}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
