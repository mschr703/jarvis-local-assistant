"""Find confirmed contact plans with real local embeddings in Edge."""

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


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Kontaktsuchtest stoppen")
    with TemporaryDirectory(prefix="jarvis-contact-semantic-ui-") as temporary:
        root = Path(temporary)
        data = root / "data"
        db = Database(data / "jarvis.db")
        contacts = ContactStore(db)
        private = contacts.create("Alex", "München privat")
        work = contacts.create("Alex", "Arbeit")
        contacts.add_note(private["id"], "Wir lieben Kunstmuseen")
        plan = contacts.add_plan(private["id"], "Museum besuchen", "Am Samstag")
        contacts.add_plan(work["id"], "Quartalsbericht", "Im Büro")
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
                        page.get_by_role("button", name="Kontakte", exact=True).click()
                        page.get_by_label("Kontakte durchsuchen").fill(
                            "Mit wem wollte ich ins Kunstmuseum?")
                        page.get_by_role("button", name="Sinngemäß lokal suchen").click()
                        results = page.get_by_role("region", name="Sinngemäße Kontakttreffer")
                        expect(results).to_contain_text("2 Kontakte geprüft", timeout=120000)
                        expect(results.locator(".item-row").first)\
                            .to_contain_text("Alex · München privat")
                        expect(results.locator(".item-row").first).to_contain_text("Museum besuchen")
                        assert "Quartalsbericht" not in results.locator(".item-row").first.inner_text()
                        results.locator(".item-row").first.get_by_role(
                            "button", name="Kontakt öffnen").click()
                        expect(page.get_by_role("heading", name="Alex · München privat"))\
                            .to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)

                        contacts.update_plan(private["id"], plan["id"], plan["version"],
                                             "Galerie besuchen", "Am Sonntag", "open")
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Kontakte", exact=True).click()
                        page.get_by_label("Kontakte durchsuchen").fill(
                            "Mit wem wollte ich ins Kunstmuseum?")
                        page.get_by_role("button", name="Sinngemäß lokal suchen").click()
                        results = page.get_by_role("region", name="Sinngemäße Kontakttreffer")
                        expect(results).to_contain_text("Galerie besuchen", timeout=120000)
                        assert "Museum besuchen" not in results.locator(".item-row").first.inner_text()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        with db.connect() as connection:
                            assert connection.execute("SELECT count(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"same_name": "separate_contexts",
                                          "local_model": "embeddinggemma:300m",
                                          "plan_update": "fresh", "source": "opened",
                                          "mobile_width": "fits", "cloud_calls": 0}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
