"""Edge-Klickpfad für belegte Personen im vorhandenen Projekt-Arbeitsstand."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, stop_test_server, wait_ready

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.tasks import TaskStore
from jarvis.work_states import WorkStateStore


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten A03a-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-project-people-ui-") as temporary:
        root = Path(temporary)
        sources = root / "sources"
        sources.mkdir()
        source = sources / "protokoll.txt"
        source.write_text("Atlas: Mira leitet die technische Prüfung.", encoding="utf-8")
        data = root / "data"
        db = Database(data / "jarvis.db")
        documents = DocumentStore(db)
        memory = documents.add_memory("Atlas hat drei Prüfschritte",
                                      "Protokoll Abschnitt 2", "local_only")
        folder = documents.add_folder(str(sources), "local_only")
        documents.index_folder(folder["id"])
        document = documents.documents()[0]
        tasks = TaskStore(db)
        task = tasks.create("Atlas prüfen", "", "2030-10-27T02:30", "Europe/Paris", fold=0)
        states = WorkStateStore(db, documents, tasks)
        state = states.create("Atlas", "Arbeitsstand geprüft", "", "Mira fragen",
                              [document["id"]], [task["id"]], [])
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct")
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
                        page.get_by_role("button", name="Arbeitsstände", exact=True).click()
                        page.get_by_role("button", name="Öffnen", exact=True).click()
                        expect(page.get_by_text("Arbeitsstand geprüft")).to_be_visible()
                        expect(page.get_by_text("Atlas prüfen", exact=True).first).to_be_visible()
                        deadline_row = page.locator(f"#project-deadline-{task['id']}")
                        expect(deadline_row).to_contain_text("Frist unverändert")
                        expect(deadline_row).to_contain_text("UTC+02:00")
                        page.get_by_label("Projekt-Erinnerung suchen").fill("Atlas")
                        page.get_by_role("button", name="Lokale Erinnerungen suchen").click()
                        page.get_by_label("Projekt-Erinnerung auswählen").select_option(memory["id"])
                        page.get_by_label("Projekt-Erinnerung Herkunft").fill("Bewusst für Atlas ausgewählt")
                        page.get_by_label("Projekt-Erinnerung Vertrauen").select_option("confirmed")
                        page.get_by_role("button", name="Erinnerung zuordnen", exact=True).click()
                        memory_row = page.locator(".item-row").filter(has_text="Atlas hat drei Prüfschritte")
                        expect(memory_row).to_contain_text("Nur lokal")
                        expect(memory_row).to_contain_text("Erinnerung unverändert")
                        expect(memory_row).to_contain_text("Gespeicherter SHA-256")
                        memory_link_id = memory_row.get_attribute("id").removeprefix("project-memory-")
                        page.get_by_label("Personenname").fill("Mira")
                        page.get_by_label("Projektbeziehung").select_option("lead")
                        page.get_by_label("Herkunftsnotiz").fill("Im lokalen Protokoll genannt")
                        page.get_by_label("Personen-Quelldatei").select_option(document["id"])
                        page.get_by_label("Personen-Vertrauen").select_option("confirmed")
                        page.get_by_label("Personen gültig ab").fill("2999-01-01")
                        page.get_by_role("button", name="Person zuordnen", exact=True).click()
                        row = page.locator(".item-row").filter(has_text="Mira · Leitung")
                        expect(row).to_contain_text("Unverändert")
                        expect(row).to_contain_text("Noch nicht gültig")
                        person_id = row.get_attribute("id", timeout=5000).removeprefix("project-person-")
                        page.get_by_label("Alias-Person").select_option(person_id)
                        page.get_by_label("Aliasname").fill("M. Weber")
                        page.get_by_label("Alias-Herkunft").fill("Im lokalen Protokoll abgekürzt")
                        page.get_by_label("Alias-Quelldatei").select_option(document["id"])
                        page.get_by_label("Alias-Vertrauen").select_option("plausible")
                        page.get_by_role("button", name="Alias hinzufügen").click()
                        alias_row = page.locator(".item-row").filter(has_text="M. Weber → Mira")
                        expect(alias_row).to_contain_text("Unverändert")
                        alias_id = alias_row.get_attribute("id", timeout=5000).removeprefix("project-alias-")
                        page.get_by_label("Aufgabenbeziehung Person").select_option(person_id)
                        page.get_by_label("Aufgabenbeziehung Aufgabe").select_option(task["id"])
                        page.get_by_label("Aufgabenbeziehung Rolle").select_option("responsible")
                        page.get_by_label("Aufgabenbeziehung Herkunft").fill("Mira betreut die Prüfung")
                        page.get_by_label("Aufgabenbeziehung Quelldatei").select_option(document["id"])
                        page.get_by_label("Aufgabenbeziehung Vertrauen").select_option("confirmed")
                        page.get_by_label("Aufgabenbeziehung gültig bis").fill("2000-01-01")
                        page.get_by_role("button", name="Aufgabe und Person verbinden").click()
                        link_row = page.locator(".item-row").filter(has_text="Atlas prüfen → Mira")
                        expect(link_row).to_contain_text("Personenbeziehung zeitlich nicht gültig")
                        expect(link_row).to_contain_text("Abgelaufen")
                        link_id = link_row.get_attribute("id", timeout=5000).removeprefix("project-person-task-")
                        page.get_by_role("button", name="Projektbeziehungen lokal abfragen").click()
                        connections = page.get_by_label("Ergebnis der lokalen Projektabfrage")
                        expect(connections).to_contain_text("Arbeitsstand-Fassung 1")
                        expect(connections).to_contain_text("lokaler Stichtag")
                        expect(connections).to_contain_text("Mira · Leitung · Dateibeleg unverändert")
                        expect(connections).to_contain_text("M. Weber → Mira")
                        expect(connections).to_contain_text("Bestätigt (eigene Einschätzung)")
                        expect(connections).to_contain_text("Atlas prüfen · open")
                        expect(connections).to_contain_text("Atlas prüfen → Mira · Verantwortlich")
                        expect(connections).to_contain_text("Erinnerungen (1)")
                        expect(connections).to_contain_text("Atlas hat drei Prüfschritte")
                        connections.get_by_role("button", name="Erinnerungsbeleg ansehen").click()
                        assert page.locator(f"#project-memory-{memory_link_id}").count() == 1
                        connections.get_by_role("button", name="Personenbeleg ansehen").first.click()
                        assert row.get_attribute("id", timeout=5000).startswith("project-person-")
                        connections.get_by_role("button", name="Quelldatei ansehen").first.click()
                        assert page.locator(f"#project-document-{document['id']}").count() == 1
                        page.get_by_role("button", name="Projektgraph lokal laden").click()
                        page.get_by_label("Graph-Zielknoten").select_option(f"document:{document['id']}")
                        page.get_by_label("Projekt → Aufgabe").check()
                        page.get_by_label("Aufgabe → Person").check()
                        page.get_by_label("Person → Dateibeleg").check()
                        page.get_by_role("button", name="Pfade abfragen").click()
                        paths = page.get_by_label("Graph-Pfade")
                        expect(paths).to_contain_text("1 gerichtete Pfade")
                        expect(paths).to_contain_text("Atlas prüfen → Mira")
                        expect(paths).to_contain_text("Im lokalen Protokoll genannt")
                        page.get_by_label("Graph-Startknoten").select_option(f"task:{task['id']}")
                        page.get_by_label("Graph-Richtung").select_option("out")
                        page.get_by_role("button", name="Nachbarn abfragen").click()
                        expect(page.get_by_label("Graph-Nachbarn")).to_contain_text("Mira")
                        page.get_by_label("Graph-Startknoten").select_option(f"project:{state['id']}")
                        page.get_by_label("Graph-Zielknoten").select_option(f"memory:{memory['id']}")
                        page.get_by_label("Projekt → Aufgabe").uncheck()
                        page.get_by_label("Aufgabe → Person").uncheck()
                        page.get_by_label("Person → Dateibeleg").uncheck()
                        page.get_by_label("Projekt → Erinnerung").check()
                        page.get_by_role("button", name="Pfade abfragen").click()
                        expect(page.get_by_label("Graph-Pfade")).to_contain_text("Atlas hat drei Prüfschritte")
                        page.get_by_label("Graph-Zielknoten").select_option(f"deadline:{task['id']}")
                        page.get_by_label("Projekt → Erinnerung").uncheck()
                        page.get_by_label("Projekt → Aufgabe").check()
                        page.get_by_label("Aufgabe → Frist").check()
                        page.get_by_role("button", name="Pfade abfragen").click()
                        expect(page.get_by_label("Graph-Pfade")).to_contain_text("2030-10-27T02:30")
                        with db.connect() as connection:
                            assert connection.execute("SELECT count(*) FROM project_people WHERE state_id=?",
                                                      (state["id"],)).fetchone()[0] == 1

                        source.write_text("Atlas: Mira prüft extern.", encoding="utf-8")
                        documents.update_memory(memory["id"], "Atlas hat vier Prüfschritte",
                                                "Neue Besprechung", "cloud_allowed")
                        tasks.update(task["id"], task["title"], "", "2030-10-27T02:30",
                                     "Europe/Paris", fold=1)
                        page.get_by_role("button", name="Status neu prüfen").click()
                        expect(deadline_row).to_contain_text("Frist seit Zuordnung geändert")
                        expect(deadline_row).to_contain_text("UTC+01:00")
                        expect(memory_row).to_contain_text("Erinnerung seit Zuordnung geändert")
                        expect(memory_row).to_contain_text("Nur lokal")
                        memory_row.get_by_role("button", name="Aktuelle Erinnerung bestätigen").click()
                        memory_row = page.locator(f"#project-memory-{memory_link_id}")
                        expect(memory_row).to_contain_text("Atlas hat vier Prüfschritte")
                        expect(memory_row).to_contain_text("Für Cloud freigegeben")
                        expect(row).to_contain_text("Seit dem Sichern geändert")
                        expect(alias_row).to_contain_text("Seit dem Sichern geändert")
                        expect(link_row).to_contain_text("Dateibeleg prüfen")
                        expect(connections).to_have_count(0)
                        page.get_by_role("button", name="Projektbeziehungen lokal abfragen").click()
                        expect(connections).to_contain_text("Dateibeleg prüfen")
                        expect(connections).to_contain_text("Seit dem Sichern geändert")
                        page.get_by_role("button", name="Projektgraph lokal laden").click()
                        page.get_by_label("Graph-Zielknoten").select_option(f"document:{document['id']}")
                        page.get_by_label("Projekt → Erinnerung").uncheck()
                        page.get_by_label("Projekt → Aufgabe").check()
                        page.get_by_label("Aufgabe → Person").check()
                        page.get_by_label("Person → Dateibeleg").check()
                        page.get_by_label("Aufgabe → Frist").uncheck()
                        page.get_by_role("button", name="Pfade abfragen").click()
                        expect(page.get_by_label("Graph-Pfade")).to_contain_text("Seit dem Sichern geändert")
                        row.get_by_role("button", name="Bearbeiten").click()
                        page.get_by_label("Projektbeziehung").select_option("reviewer")
                        page.get_by_label("Herkunftsnotiz").fill("Eigene neue Zuordnung")
                        page.get_by_label("Personen-Quelldatei").select_option("")
                        page.get_by_label("Personen gültig ab").fill("")
                        page.get_by_role("button", name="Änderung speichern").click()
                        expect(connections).to_have_count(0)
                        expect(page.locator(".item-row").filter(has_text="Mira · Prüfung"))\
                            .to_contain_text("Eigene Angabe ohne Dateibeleg")
                        link_row.get_by_role("button", name="Bearbeiten").click()
                        page.get_by_label("Aufgabenbeziehung Rolle").select_option("reviewer")
                        page.get_by_label("Aufgabenbeziehung Herkunft").fill("Eigene neue Aufgabenangabe")
                        page.get_by_label("Aufgabenbeziehung Quelldatei").select_option("")
                        page.get_by_label("Aufgabenbeziehung gültig bis").fill("")
                        page.get_by_role("button", name="Aufgabenbeziehung speichern").click()
                        expect(link_row).to_contain_text("Aktuelle Zuordnung")
                        alias_row.get_by_role("button", name="Bearbeiten").click()
                        page.get_by_label("Aliasname").fill("Mirjam")
                        page.get_by_label("Alias-Herkunft").fill("Eigene bestätigte Abkürzung")
                        page.get_by_label("Alias-Quelldatei").select_option("")
                        page.get_by_label("Alias-Vertrauen").select_option("confirmed")
                        page.get_by_role("button", name="Alias speichern").click()
                        alias_row = page.locator(f"#project-alias-{alias_id}")
                        expect(alias_row).to_contain_text("Eigene Angabe")
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Arbeitsstände", exact=True).click()
                        page.get_by_role("button", name="Öffnen", exact=True).click()
                        row = page.locator(".item-row").filter(has_text="Mira · Prüfung")
                        expect(row).to_contain_text("Eigene Angabe ohne Dateibeleg")
                        link_row = page.locator(f"#project-person-task-{link_id}")
                        expect(link_row).to_contain_text("Prüfend")
                        alias_row = page.locator(f"#project-alias-{alias_id}")
                        expect(alias_row).to_contain_text("Mirjam → Mira")
                        page.get_by_role("button", name="Projektbeziehungen lokal abfragen").click()
                        expect(connections).to_contain_text("Mira · Prüfung · Eigene Angabe")
                        expect(connections).to_contain_text("Mirjam → Mira")
                        connections.get_by_role("button", name="Aliasbeleg ansehen").click()
                        expect(connections).to_contain_text("Atlas prüfen → Mira · Prüfend")
                        connections.get_by_role("button", name="Verbindung ansehen").click()
                        page.get_by_label("Personenname").fill("Miriam")
                        page.get_by_label("Projektbeziehung").select_option("contact")
                        page.get_by_label("Herkunftsnotiz").fill("Eigene Projektkontakt-Angabe")
                        page.get_by_role("button", name="Person zuordnen", exact=True).click()
                        target_row = page.locator(".item-row").filter(has_text="Miriam · Kontakt")
                        target_id = target_row.get_attribute("id", timeout=5000).removeprefix("project-person-")
                        page.get_by_label("Quellperson zusammenführen").select_option(person_id)
                        page.get_by_label("Zielperson zusammenführen").select_option(target_id)
                        page.get_by_role("button", name="Zusammenführen…").click()
                        expect(page.get_by_text("Mira in Miriam zusammenführen?")).to_be_visible()
                        page.get_by_role("button", name="Zusammenführung bestätigen").click()
                        expect(page.locator(f"#project-person-{person_id}")).to_have_count(0)
                        expect(page.locator(f"#project-alias-{alias_id}")).to_contain_text("Mirjam → Miriam")
                        expect(link_row).to_contain_text("Atlas prüfen → Miriam")
                        expect(target_row.get_by_role("button", name="Entfernen…")).to_be_disabled()
                        expect(page.get_by_text("Mira → Miriam", exact=True).first).to_be_visible()
                        page.get_by_role("button", name="Projektbeziehungen lokal abfragen").click()
                        expect(page.get_by_label("Ergebnis der lokalen Projektabfrage"))\
                            .to_contain_text("ursprünglich Mira")
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Arbeitsstände", exact=True).click()
                        page.get_by_role("button", name="Öffnen", exact=True).click()
                        page.get_by_role("button", name="Trennen…").click()
                        page.get_by_role("button", name="Trennen bestätigen").click()
                        row = page.locator(f"#project-person-{person_id}")
                        expect(row).to_contain_text("Mira · Prüfung")
                        link_row = page.locator(f"#project-person-task-{link_id}")
                        expect(link_row).to_contain_text("Atlas prüfen → Mira")
                        alias_row = page.locator(f"#project-alias-{alias_id}")
                        expect(alias_row).to_contain_text("Mirjam → Mira")
                        connections = page.get_by_label("Ergebnis der lokalen Projektabfrage")
                        link_row.get_by_role("button", name="Entfernen…").click()
                        link_row.get_by_role("button", name="Verbindung entfernen").click()
                        expect(connections).to_have_count(0)
                        expect(page.get_by_text("Noch keine direkte Aufgabenbeziehung eingetragen.")).to_be_visible()
                        alias_row.get_by_role("button", name="Entfernen…").click()
                        alias_row.get_by_role("button", name="Alias entfernen").click()
                        expect(page.get_by_text("Noch kein Aliasname eingetragen.")).to_be_visible()
                        row.get_by_role("button", name="Entfernen…").click()
                        row.get_by_role("button", name="Verbindung entfernen").click()
                        expect(connections).to_have_count(0)
                        expect(page.locator(f"#project-person-{person_id}")).to_have_count(0)
                        expect(target_row).to_be_visible()
                        assert tasks.get_task(task["id"]) is not None
                        assert documents.get_document(document["id"]) is not None
                        memory_row = page.locator(f"#project-memory-{memory_link_id}")
                        memory_row.get_by_role("button", name="Verknüpfung entfernen…").click()
                        memory_row.get_by_role("button", name="Projektverknüpfung entfernen").click()
                        expect(memory_row).to_have_count(0)
                        assert documents.get_memory(memory["id"]) is not None
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"source_link": True, "changed_source": True,
                                          "manual_correction": True, "reload": True,
                                          "local_project_query": True,
                                          "task_person_source_link": True,
                                          "person_alias_source": True,
                                          "person_merge_split": True,
                                          "project_graph_paths_neighbors": True,
                                          "project_memory_snapshots": True,
                                          "project_deadlines_dst": True,
                                          "removed_without_domain_deletion": True,
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
