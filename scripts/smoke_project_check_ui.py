"""Isolierter Edge-Klicktest für J5a mit Quellenwechsel und einmaliger Aufgabe."""

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
from jarvis.documents import DocumentStore
from jarvis.tasks import TaskStore
from jarvis.work_states import WorkStateStore


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Projekttest mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-project-ui-") as temporary:
        root = Path(temporary)
        data = root / "data"
        source = root / "sources"
        source.mkdir()
        file = source / "plan.md"
        file.write_text("# Atlas\nDer Entwurf ist geprüft.\n", encoding="utf-8")
        db = Database(data / "jarvis.db")
        documents = DocumentStore(db)
        folder = documents.add_folder(str(source), "local_only")
        documents.index_folder(folder["id"])
        document = documents.documents()[0]
        tasks = TaskStore(db)
        previous = tasks.create("Vorarbeit", "", None, "Europe/Paris")
        states = WorkStateStore(db, documents, tasks)
        state = states.create("Atlas", "Entwurf geprüft", "Budget offen", "Maria fragen",
                              [document["id"]], [previous["id"]], [])
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
                        page.set_default_timeout(30_000)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.locator("nav button").filter(has_text="Arbeitsstände").click()
                        page.get_by_role("button", name="Öffnen").click()
                        expect(page.get_by_role("heading", name="Atlas", exact=True)).to_be_visible()
                        expect(page.get_by_role("button", name="Nächsten Schritt als Aufgabe anlegen")).to_be_enabled()
                        file.write_text("# Atlas\nDer Entwurf wurde geändert.\n", encoding="utf-8")
                        page.get_by_role("button", name="Status neu prüfen").click()
                        expect(page.get_by_text("Vorbereitung blockiert", exact=False)).to_be_visible()
                        expect(page.get_by_role("button", name="Nächsten Schritt als Aufgabe anlegen")).to_be_disabled()
                        documents.index_folder(folder["id"])
                        page.get_by_role("button", name="Neu indexierte Datei bestätigen").click()
                        expect(page.get_by_role("button", name="Nächsten Schritt als Aufgabe anlegen")).to_be_enabled()
                        page.get_by_role("button", name="Nächsten Schritt als Aufgabe anlegen").dblclick()
                        expect(page.get_by_text("einmalig als Aufgabe angelegt", exact=False)).to_be_visible()
                        assert len(tasks.list_tasks()) == 2
                        prepared = next(item for item in tasks.list_tasks() if item["title"] == "Maria fragen")
                        stop_test_server(process)
                        process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                                                   cwd=ROOT, env=environment, stdout=log,
                                                   stderr=subprocess.STDOUT)
                        wait_ready(process, timeout=120)
                        page.reload()
                        page.locator("nav button").filter(has_text="Arbeitsstände").click()
                        page.get_by_role("button", name="Öffnen").click()
                        expect(page.get_by_text("Dieser nächste Schritt ist bereits als Aufgabe erfasst", exact=False)).to_be_visible()
                        tasks.complete(prepared["id"])
                        page.get_by_role("button", name="Status neu prüfen").click()
                        expect(page.get_by_text("erledigt", exact=False).first).to_be_visible()
                        assert not page.get_by_role("button", name="Nächsten Schritt als Aufgabe anlegen").count()
                        page.get_by_role("button", name="Bearbeiten").click()
                        page.get_by_label("Nächster Schritt").fill("Angebot schreiben")
                        page.get_by_role("button", name="Arbeitsstand speichern").click()
                        expect(page.get_by_role("button", name="Nächsten Schritt als Aufgabe anlegen")).to_be_enabled()
                        page.get_by_role("button", name="Nächsten Schritt als Aufgabe anlegen").click()
                        expect(page.get_by_text("einmalig als Aufgabe angelegt", exact=False)).to_be_visible()
                        assert len(tasks.list_tasks()) == 3
                        assert states.get(state["id"])["next_step"] == "Angebot schreiben"
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                        print(json.dumps({"first_task": prepared["id"], "tasks": 3,
                                          "changed_source_blocked": True,
                                          "duplicate_after_reload": False,
                                          "backend_restart": "passed",
                                          "new_step_created": True,
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
