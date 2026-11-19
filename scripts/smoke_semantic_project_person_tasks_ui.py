"""Real Edge/Ollama path for explicit project person-task evidence."""

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
from jarvis.project_people import ProjectPeopleStore
from jarvis.project_person_tasks import ProjectPersonTaskStore
from jarvis.tasks import TaskStore
from jarvis.work_states import WorkStateStore


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-semantic-person-task-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed"
        allowed.mkdir()
        file = allowed / "Werkstatt_Notiz.md"
        file.write_text("Vertraulicher Dateiinhalt ohne Suchwörter.", encoding="utf-8")
        data = root / "data"
        db = Database(data / "jarvis.db")
        docs = DocumentStore(db)
        folder = docs.add_folder(str(allowed), "local_only")
        assert docs.index_folder(folder["id"])["indexed"] == 1
        document = docs.documents()[0]
        tasks = TaskStore(db)
        task = tasks.create("Budgetplanung", "", None, "Europe/Paris")
        states = WorkStateStore(db, docs, tasks)
        project = states.create("Mosaik", "Projekt angelegt", "", "Unterlagen prüfen",
                                [document["id"]], [task["id"]], [])
        people = ProjectPeopleStore(db, states)
        person = people.add(project["id"], project["version"], "Mira Weber",
                            "reviewer", "Prüft die Heizkosten", None)
        links = ProjectPersonTaskStore(db, states, people)
        link = links.add(project["id"], project["version"], person["id"], task["id"],
                         "responsible", "Kontrolliert die Nebenkostenaufstellung",
                         document["id"])
        tasks.update(task["id"], "Kostenprüfung", "", None, "Europe/Paris")
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
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded",
                                  timeout=120000)
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Quelltyp der gemeinsamen Suche").select_option("project")
                        page.get_by_label("Gemeinsame Suche").fill("Nebenkostenaufstellung")
                        page.get_by_role("button", name="Sinngemäß lokal suchen").click()
                        results = page.get_by_role("region", name="Sinngemäße Suchergebnisse")
                        hit = results.locator("article").filter(has_text="Mosaik")
                        expect(hit).to_contain_text("Person-Aufgabe-Beleg: Mira Weber",
                                                    timeout=120000)
                        expect(hit).to_contain_text("Kostenprüfung (damals: Budgetplanung)")
                        expect(hit).to_contain_text("Kontrolliert die Nebenkostenaufstellung")
                        expect(hit).to_contain_text("Dateibeleg im Arbeitsstand prüfen")
                        assert str(allowed) not in hit.inner_text()
                        assert "Vertraulicher Dateiinhalt" not in hit.inner_text()
                        hit.get_by_role("button", name="Quelle öffnen").click()
                        expect(page.locator(f"#project-person-task-{link['id']}")).to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)

                        links.delete(project["id"], link["id"], project["version"],
                                     link["version"])
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Quelltyp der gemeinsamen Suche").select_option("project")
                        page.get_by_label("Gemeinsame Suche").fill("Mosaik")
                        page.get_by_role("button", name="Sinngemäß lokal suchen").click()
                        results = page.get_by_role("region", name="Sinngemäße Suchergebnisse")
                        expect(results.locator("article").filter(has_text="Mosaik")).to_be_visible(
                            timeout=120000)
                        assert "Person-Aufgabe-Beleg" not in results.inner_text()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"semantic_person_task": "found_and_opened",
                                          "removed_link": "refreshed", "mobile_width": "fits",
                                          "cloud_calls": 0}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
