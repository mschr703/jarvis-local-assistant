"""Isolated Edge path for local document, memory and project search."""

import json
import os
import re
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
from jarvis.project_aliases import ProjectAliasStore
from jarvis.project_memories import ProjectMemoryStore
from jarvis.project_people import ProjectPeopleStore
from jarvis.project_person_tasks import ProjectPersonTaskStore
from jarvis.tasks import TaskStore
from jarvis.work_states import WorkStateStore


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-unified-search-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed"
        allowed.mkdir()
        file = allowed / "Vertrag_Orion.md"
        file.write_text("Projekt Orion: Der Vertrag ist unterschrieben.", encoding="utf-8")
        data = root / "data"
        db = Database(data / "jarvis.db")
        docs = DocumentStore(db)
        folder = docs.add_folder(str(allowed), "local_only")
        assert docs.index_folder(folder["id"])["indexed"] == 1
        memory = docs.add_memory("Orion braucht den Vertrag am Freitag.", "Eigene Notiz", "local_only")
        document = docs.documents()[0]
        tasks = TaskStore(db)
        task = tasks.create("Budgetplanung", "", None, "Europe/Paris")
        states = WorkStateStore(db, docs, tasks)
        project = states.create("Projekt Orion", "Vertrag liegt vor",
                                "", "Unterlagen sammeln", [document["id"]], [task["id"]], [])
        people = ProjectPeopleStore(db, states)
        person = people.add(project["id"], project["version"], "Míra Weber",
                            "reviewer", "Prüft die Heizkosten", None)
        aliases = ProjectAliasStore(db, states, people)
        alias = aliases.add(
            project["id"], project["version"], person["id"], person["version"],
            "Mími", "Rufname aus eigener Angabe", None)
        memory_links = ProjectMemoryStore(db, states)
        memory_link = memory_links.add(project["id"], project["version"],
                                       memory["id"], "Absprache aus dem Erstgespräch")
        person_tasks = ProjectPersonTaskStore(db, states, people)
        person_task = person_tasks.add(project["id"], project["version"],
                                       person["id"], task["id"], "responsible",
                                       "Kontrolliert Nebenkostenaufstellung", None)
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
                        page.get_by_label("Gemeinsame Suche").fill("Was ist über Orion bekannt?")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        results = page.get_by_role("region", name="Suchergebnisse")
                        expect(results.get_by_text("1 Dokumente · 1 Erinnerungen · 1 Arbeitsstände")).to_be_visible(timeout=30000)
                        expect(results.locator("article").filter(
                            has_text="Dokument · Vertrag_Orion.md")).to_be_visible()
                        expect(results.locator("article").filter(
                            has_text="Erinnerung · Erinnerung").get_by_text("Eigene Notiz")).to_be_visible()
                        expect(results.get_by_text("Arbeitsstand v1", exact=False)).to_be_visible()
                        expect(results.get_by_text("SHA-256", exact=False)).to_be_visible()
                        for label, kind, source_id in (
                            ("Dokument · Vertrag_Orion.md", "document", document["id"]),
                            ("Erinnerung · Erinnerung", "memory", memory["id"]),
                            ("Arbeitsstand v1", "project", project["id"]),
                        ):
                            results.locator("article").filter(has_text=label).get_by_role(
                                "button", name="Quelle öffnen").click()
                            target = page.locator(f"#search-source-{kind}-{source_id}")
                            expect(target).to_be_visible()
                            expect(target).to_have_class(re.compile("source-opened"))
                            if kind == "document":
                                page.get_by_label("Erinnerungstext durchsuchen").fill("nicht-vorhanden")
                                page.get_by_role("button", name="Wissen suchen").click()
                            page.get_by_role("button", name="Suche", exact=True).click()
                            expect(results.get_by_text("1 Dokumente · 1 Erinnerungen · 1 Arbeitsstände")).to_be_visible()
                        page.get_by_label("Gemeinsame Suche").fill("Heizkosten")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        person_hit = results.locator("article").filter(has_text="Projektperson: Míra Weber")
                        expect(person_hit).to_contain_text("Prüft die Heizkosten")
                        person_hit.get_by_role("button", name="Passenden Beleg öffnen").click()
                        expect(page.locator(f"#project-person-{person['id']}")).to_be_in_viewport()
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Gemeinsame Suche").fill("Mimi")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        alias_hit = results.locator("article").filter(has_text="Personenalias: Mími → Míra Weber")
                        expect(alias_hit).to_contain_text("Rufname aus eigener Angabe")
                        alias_hit.get_by_role("button", name="Passenden Beleg öffnen").click()
                        expect(page.locator(f"#project-alias-{alias['id']}")).to_be_in_viewport()
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Gemeinsame Suche").fill("Budgetplanung")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        task_hit = results.locator("article").filter(has_text="Projektaufgabe: Budgetplanung")
                        expect(task_hit).to_contain_text("offen")
                        task_hit.get_by_role("button", name="Passenden Beleg öffnen").click()
                        expect(page.locator(f"#project-task-{task['id']}")).to_be_in_viewport()
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Gemeinsame Suche").fill("Nebenkostenaufstellung")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        person_task_hit = results.locator("article").filter(
                            has_text="Person-Aufgabe-Beleg: Míra Weber")
                        expect(person_task_hit).to_contain_text("Kontrolliert Nebenkostenaufstellung")
                        person_task_hit.get_by_role("button", name="Passenden Beleg öffnen").click()
                        expect(page.locator(f"#project-person-task-{person_task['id']}")).to_be_in_viewport()
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Gemeinsame Suche").fill("Nebenkostenaufstellung")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        stale_person_task_hit = results.locator("article").filter(
                            has_text="Person-Aufgabe-Beleg: Míra Weber")
                        expect(stale_person_task_hit).to_be_visible()
                        person_tasks.delete(project["id"], person_task["id"], project["version"],
                                            person_task["version"])
                        stale_person_task_hit.get_by_role("button", name="Passenden Beleg öffnen").click()
                        expect(page.locator(f"#project-person-task-{person_task['id']}")).to_have_count(0)
                        expect(page.locator(f"#search-source-project-{project['id']}")).to_be_in_viewport()
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Gemeinsame Suche").fill("Was ist über Orion bekannt?")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        document_hit = results.locator("article").filter(
                            has_text="Dokument · Vertrag_Orion.md")
                        document_hit.get_by_role("button", name="Mit dieser Fundstelle Entwurf starten").click()
                        expect(page.get_by_role("heading", name="Entwürfe", exact=True)).to_be_visible()
                        expect(page.get_by_text("1 Fundstelle(n) ausgewählt.", exact=False)).to_be_visible()
                        expect(page.get_by_role("list", name="Ausgewählte Fundstellen")).to_contain_text(
                            "Vertrag_Orion.md")
                        page.get_by_role("button", name="Suche", exact=True).click()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.get_by_label("Gemeinsame Suche").fill("Vertrag_Orion")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        expect(results.get_by_text("1 Dokumente · 0 Erinnerungen · 1 Arbeitsstände")).to_be_visible(timeout=30000)
                        project_file_hit = results.locator("article").filter(
                            has_text="Projektdatei: Vertrag_Orion.md")
                        expect(project_file_hit).to_contain_text("Original und Freigabe im Arbeitsstand prüfen")
                        assert str(allowed) not in project_file_hit.inner_text()
                        project_file_hit.get_by_role("button", name="Passenden Beleg öffnen").click()
                        expect(page.locator(f"#project-document-{document['id']}")).to_be_in_viewport()
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Gemeinsame Suche").fill("Freitag")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        memory_project_hit = results.locator("article").filter(
                            has_text="Projekterinnerung (gespeichert)")
                        expect(memory_project_hit).to_contain_text("Absprache aus dem Erstgespräch")
                        memory_project_hit.get_by_role("button", name="Passenden Beleg öffnen").click()
                        expect(page.locator(f"#project-memory-{memory_link['id']}")).to_be_in_viewport()
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Gemeinsame Suche").fill("Freitag")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        stale_memory_hit = results.locator("article").filter(
                            has_text="Projekterinnerung (gespeichert)")
                        expect(stale_memory_hit).to_be_visible()
                        memory_links.delete(project["id"], memory_link["id"], project["version"],
                                            memory_link["version"])
                        stale_memory_hit.get_by_role("button", name="Passenden Beleg öffnen").click()
                        expect(page.locator(f"#project-memory-{memory_link['id']}")).to_have_count(0)
                        expect(page.locator(f"#search-source-project-{project['id']}")).to_be_in_viewport()
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Gemeinsame Suche").fill("Mimi")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        stale_alias_hit = results.locator("article").filter(
                            has_text="Personenalias: Mími")
                        expect(stale_alias_hit).to_be_visible()
                        aliases.delete(project["id"], alias["id"], project["version"],
                                       alias["version"])
                        stale_alias_hit.get_by_role("button", name="Passenden Beleg öffnen").click()
                        expect(page.locator(f"#project-alias-{alias['id']}")).to_have_count(0)
                        expect(page.locator(f"#search-source-project-{project['id']}")).to_be_in_viewport()
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Gemeinsame Suche").fill("Saturn")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        expect(results.get_by_text("Keine passenden gespeicherten Quellen gefunden.")).to_be_visible(timeout=30000)
                        page.get_by_label("Gemeinsame Suche").fill("Was ist über Orion bekannt?")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        expect(results.get_by_text("1 Dokumente · 1 Erinnerungen · 1 Arbeitsstände")).to_be_visible()
                        states.delete(project["id"], project["version"])
                        results.locator("article").filter(has_text="Arbeitsstand v1").get_by_role(
                            "button", name="Quelle öffnen").click()
                        expect(page.get_by_text("Arbeitsstand nicht gefunden", exact=False)).to_be_visible()
                        page.get_by_role("button", name="Suche", exact=True).click()
                        docs.delete_memory(memory["id"])
                        results.locator("article").filter(has_text="Erinnerung · Erinnerung").get_by_role(
                            "button", name="Quelle öffnen").click()
                        expect(page.get_by_text("Die gesuchte Quelle ist nicht mehr vorhanden.", exact=False)).to_be_visible()
                        page.get_by_role("button", name="Suche", exact=True).click()
                        file.write_text("Projekt Orion: Der Vertrag wurde geändert.", encoding="utf-8")
                        results.locator("article").filter(
                            has_text="Dokument · Vertrag_Orion.md").get_by_role(
                            "button", name="Quelle öffnen").click()
                        expect(page.get_by_text("Das Original hat sich seit der Suche geändert", exact=False)).to_be_visible()
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        expect(page.locator(f"#search-source-document-{document['id']}")).not_to_have_class(
                            re.compile("source-opened"))
                        page.get_by_role("button", name="Suche", exact=True).click()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"sources": 3, "source_navigation": "current_and_stale",
                                          "draft_source": "selected_without_model",
                                          "filename": "found",
                                          "empty": "shown", "mobile_width": "fits", "model_calls": 0}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
