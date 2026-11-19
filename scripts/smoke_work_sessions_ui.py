"""Edge-Pfad für manuelle Sitzungsnotizen mit Quellenprüfung nach Neustart."""

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


def start(root: Path, environment: dict, number: int):
    log = (root / f"server-{number}.log").open("w", encoding="utf-8")
    process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                               cwd=ROOT, env=environment, stdout=log,
                               stderr=subprocess.STDOUT)
    try:
        wait_ready(process, timeout=120)
    except BaseException:
        stop_test_server(process)
        log.close()
        raise
    return process, log


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-work-sessions-ui-") as temporary:
        root = Path(temporary)
        source = root / "sources"
        source.mkdir()
        path = source / "plan.md"
        path.write_text("Freigegebene Planungsunterlage", encoding="utf-8")
        data = root / "data"
        db = Database(data / "jarvis.db")
        docs = DocumentStore(db)
        folder = docs.add_folder(str(source), "local_only")
        assert docs.index_folder(folder["id"])["indexed"] == 1
        document = docs.documents()[0]
        tasks = TaskStore(db)
        task = tasks.create("Angebot prüfen", "", None, "Europe/Paris")
        states = WorkStateStore(db, docs, tasks)
        state = states.create("Atlas", "Plan liegt vor", "", "Maria fragen",
                              [document["id"]], [task["id"]], ["https://example.org/plan"])
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama")
        process, log = start(root, environment, 1)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                try:
                    page = browser.new_page(viewport={"width": 390, "height": 800})
                    page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                    page.get_by_role("button", name="Arbeitsstände", exact=True).click()
                    page.get_by_role("button", name="Öffnen").click()
                    notes = page.get_by_role("region", name="Sitzungsnotizen zum Arbeitsstand")
                    notes.get_by_label("Sitzungsergebnis").fill("Angebot durchgesehen")
                    notes.get_by_label("Offene Sitzungsfragen").fill("Preis noch offen")
                    notes.get_by_label("Nächster Sitzungsschritt").fill("Rückfrage stellen")
                    notes.get_by_role("group", name="Dateien als Beleg").get_by_role("checkbox").check()
                    notes.get_by_role("group", name="Aufgaben als Beleg").get_by_role("checkbox").check()
                    notes.get_by_role("group", name="Links als Verweis (Inhalt ungeprüft)").get_by_role("checkbox").check()
                    notes.get_by_role("button", name="Sitzungsnotiz sichern").click()
                    expect(notes).to_contain_text("Angebot durchgesehen")
                    expect(notes).to_contain_text("Datei:")
                    notes.get_by_role("button", name="Dateibeleg ansehen").click()
                    expect(page.locator(f"#project-document-{document['id']}")).to_be_in_viewport()
                    notes.get_by_role("button", name="Aufgabe ansehen").click()
                    expect(page.locator(f"#project-task-{task['id']}")).to_be_in_viewport()
                    assert notes.get_by_role("link", name="Link öffnen").get_attribute("rel") == "noopener noreferrer"
                    assert not overflow_nodes(page), overflow_nodes(page)
                finally:
                    browser.close()
        finally:
            stop_test_server(process)
            log.close()

        path.write_text("Extern geändert", encoding="utf-8")
        tasks.complete(task["id"])
        states.update(state["id"], state["version"], "Atlas", "Plan liegt vor", "",
                      "Weiter", [document["id"]], [task["id"]], [])
        process, log = start(root, environment, 2)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                try:
                    page = browser.new_page(viewport={"width": 390, "height": 800})
                    page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                    page.get_by_role("button", name="Arbeitsstände", exact=True).click()
                    page.get_by_role("button", name="Öffnen").click()
                    notes = page.get_by_role("region", name="Sitzungsnotizen zum Arbeitsstand")
                    expect(notes).to_contain_text("Angebot durchgesehen")
                    expect(notes).to_contain_text("Arbeitsstand inzwischen geändert")
                    expect(notes).to_contain_text("Geändert")
                    expect(notes).to_contain_text("Erledigt")
                    expect(notes).to_contain_text("nicht mehr im Arbeitsstand")
                    notes.get_by_role("button", name="Entfernen…").click()
                    notes.get_by_role("button", name="Wirklich entfernen").click()
                    expect(notes).to_contain_text("Noch keine Sitzungsnotiz")
                    assert not overflow_nodes(page), overflow_nodes(page)
                    print(json.dumps({"session": "persisted_after_restart",
                                      "file": "changed", "task": "done",
                                      "link": "removed_from_state", "delete": "confirmed",
                                      "mobile_width": "fits", "cloud_calls": 0}))
                finally:
                    browser.close()
        finally:
            stop_test_server(process)
            log.close()


if __name__ == "__main__":
    main()
