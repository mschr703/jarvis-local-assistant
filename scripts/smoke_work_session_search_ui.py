"""Edge-Pfad für lokale Stichwortsuche und Sprung zur Sitzungsnotiz."""

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
from jarvis.tasks import TaskStore
from jarvis.work_sessions import WorkSessionStore
from jarvis.work_states import WorkStateStore


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-session-search-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed"
        allowed.mkdir()
        file = allowed / "Quelle.md"
        file.write_text("Vertraulicher Dateiinhalt", encoding="utf-8")
        data = root / "data"
        db = Database(data / "jarvis.db")
        docs = DocumentStore(db)
        folder = docs.add_folder(str(allowed), "local_only")
        assert docs.index_folder(folder["id"])["indexed"] == 1
        document = docs.documents()[0]
        tasks = TaskStore(db)
        state_store = WorkStateStore(db, docs, tasks)
        state = state_store.create("Atlas", "Akte angelegt", "", "Weiter",
                                   [document["id"]], [], [])
        notes = WorkSessionStore(db, state_store)
        note = notes.create(state["id"], state["version"], "Quartalsbilanz abgeglichen",
                            "Rückfrage offen", "Maria kontaktieren",
                            [document["id"]], [], [])
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama")
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
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded",
                                  timeout=120000)
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Quelltyp der gemeinsamen Suche").select_option("project")
                        page.get_by_label("Gemeinsame Suche").fill("Quartalsbilanz")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        results = page.get_by_role("region", name="Suchergebnisse")
                        hit = results.locator("article").filter(has_text="Sitzungsnotiz vom")
                        expect(hit).to_contain_text("Quartalsbilanz abgeglichen")
                        assert str(allowed) not in hit.inner_text()
                        assert "Vertraulicher Dateiinhalt" not in hit.inner_text()
                        hit.get_by_role("button", name="Passenden Beleg öffnen").click()
                        target = page.locator(f"#project-session-{note['id']}")
                        expect(target).to_be_in_viewport(timeout=30000)
                        expect(target).to_contain_text("Quartalsbilanz abgeglichen")
                        assert not overflow_nodes(page), overflow_nodes(page)

                        page.get_by_role("button", name="Suche", exact=True).click()
                        notes.delete(state["id"], note["id"])
                        hit.get_by_role("button", name="Passenden Beleg öffnen").click()
                        expect(page.get_by_text("Die gesuchte Sitzungsnotiz wurde inzwischen entfernt.",
                                                exact=False)).to_be_visible()
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Gemeinsame Suche").fill("Quartalsbilanz")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        expect(results.locator("article")).to_have_count(0)
                        print(json.dumps({"session_search": "found_and_opened",
                                          "stale_hit": "reported_removed",
                                          "deleted_note": "absent_after_refresh",
                                          "mobile_width": "fits", "cloud_calls": 0}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
