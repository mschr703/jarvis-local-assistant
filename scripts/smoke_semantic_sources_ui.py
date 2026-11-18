"""Exercise real Ollama retrieval across document, memory and work state in Edge."""

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
from jarvis.work_states import WorkStateStore


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-semantic-sources-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed"
        allowed.mkdir()
        (allowed / "Kuchen.md").write_text("Das Rezept für Apfelkuchen enthält Zimt.",
                                          encoding="utf-8")
        data = root / "data"
        db = Database(data / "jarvis.db")
        docs = DocumentStore(db)
        folder = docs.add_folder(str(allowed), "local_only")
        docs.index_folder(folder["id"])
        memory = docs.add_memory("Die Schlüsselübergabe für die Wohnung ist am Freitag.",
                                 "Gespräch vom Dienstag", "local_only")
        states = WorkStateStore(db, docs, TaskStore(db))
        project = states.create("Umzug", "Mietvertrag unterschrieben", "",
                                "Schlüssel am Freitag abholen", [], [], [])
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
                        page.get_by_label("Gemeinsame Suche").fill(
                            "Wann bekomme ich den Zugang zur neuen Wohnung?")
                        button = page.get_by_role("button", name="Sinngemäß lokal suchen")
                        button.click()
                        results = page.get_by_role("region", name="Sinngemäße Suchergebnisse")
                        expect(results.get_by_text("1 Dokumente · 1 Erinnerungen · 1 Arbeitsstände")).to_be_visible(timeout=120000)
                        expect(results.locator("article").filter(has_text="Gespräch vom Dienstag")).to_be_visible()
                        expect(results.locator("article").filter(has_text="Umzug")).to_contain_text("Arbeitsstand v1")
                        expect(results.get_by_text("Ähnlichkeit", exact=False).first).to_be_visible()
                        assert "Kuchen.md" not in results.locator("article").first.inner_text()
                        assert not overflow_nodes(page), overflow_nodes(page)

                        docs.update_memory(memory["id"],
                                           "Die Schlüsselübergabe für die Wohnung ist am Samstag.",
                                           "Gespräch vom Dienstag", "local_only")
                        states.update(project["id"], project["version"], "Umzug",
                                      "Mietvertrag unterschrieben", "",
                                      "Schlüssel am Samstag abholen", [], [], [])
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Gemeinsame Suche").fill(
                            "Wann bekomme ich den Zugang zur neuen Wohnung?")
                        page.get_by_role("button", name="Sinngemäß lokal suchen").click()
                        results = page.get_by_role("region", name="Sinngemäße Suchergebnisse")
                        expect(results.get_by_text("1 Dokumente · 1 Erinnerungen · 1 Arbeitsstände")).to_be_visible(timeout=120000)
                        assert "Samstag" in results.inner_text()
                        assert "Freitag" not in results.inner_text()
                        expect(results.locator("article").filter(has_text="Umzug")).to_contain_text("Arbeitsstand v2")
                        assert not overflow_nodes(page), overflow_nodes(page)
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"source_kinds": 3, "updates": "current",
                                          "mobile_width": "fits", "cloud_calls": 0}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
