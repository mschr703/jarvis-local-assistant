"""Real Edge/Ollama path for explicitly linked project files and memories."""

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
from jarvis.project_memories import ProjectMemoryStore
from jarvis.tasks import TaskStore
from jarvis.work_states import WorkStateStore


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-semantic-project-files-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed"
        allowed.mkdir()
        file = allowed / "Orion_Besprechung.md"
        file.write_text("Internes Protokoll ohne Suchwörter.", encoding="utf-8")
        data = root / "data"
        db = Database(data / "jarvis.db")
        docs = DocumentStore(db)
        folder = docs.add_folder(str(allowed), "local_only")
        assert docs.index_folder(folder["id"])["indexed"] == 1
        document = docs.documents()[0]
        memory = docs.add_memory("Die Betonlieferung erfolgt am Freitagmorgen.",
                                 "Abstimmung vom Dienstag", "local_only")
        states = WorkStateStore(db, docs, TaskStore(db))
        project = states.create("Mosaik", "Projekt angelegt", "", "Unterlagen prüfen",
                                [document["id"]], [], [])
        memory_links = ProjectMemoryStore(db, states)
        memory_link = memory_links.add(project["id"], project["version"],
                                       memory["id"], "Notiz aus der Baustellenbesprechung")
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
                        page.get_by_label("Gemeinsame Suche").fill("Orion Besprechung")
                        page.get_by_role("button", name="Sinngemäß lokal suchen").click()
                        results = page.get_by_role("region", name="Sinngemäße Suchergebnisse")
                        hit = results.locator("article").filter(has_text="Mosaik")
                        expect(hit).to_contain_text("Projektdatei: Orion_Besprechung.md",
                                                    timeout=120000)
                        expect(hit).to_contain_text("Original und Freigabe im Arbeitsstand prüfen")
                        assert str(allowed) not in hit.inner_text()
                        hit.get_by_role("button", name="Quelle öffnen").click()
                        expect(page.locator(f"#search-source-project-{project['id']}")).to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)

                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Gemeinsame Suche").fill("Betonlieferung Freitagmorgen")
                        page.get_by_role("button", name="Sinngemäß lokal suchen").click()
                        memory_hit = results.locator("article").filter(has_text="Mosaik")
                        expect(memory_hit).to_contain_text("Projekterinnerung (gespeichert)",
                                                           timeout=120000)
                        expect(memory_hit).to_contain_text("Notiz aus der Baustellenbesprechung")
                        memory_hit.get_by_role("button", name="Quelle öffnen").click()
                        expect(page.locator(f"#project-memory-{memory_link['id']}")).to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)

                        memory_links.delete(project["id"], memory_link["id"],
                                            project["version"], memory_link["version"])
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Quelltyp der gemeinsamen Suche").select_option("project")
                        page.get_by_label("Gemeinsame Suche").fill("Mosaik")
                        page.get_by_role("button", name="Sinngemäß lokal suchen").click()
                        results = page.get_by_role("region", name="Sinngemäße Suchergebnisse")
                        expect(results.locator("article").filter(has_text="Mosaik")).to_be_visible(
                            timeout=120000)
                        assert "Betonlieferung" not in results.inner_text()

                        states.update(project["id"], project["version"], project["project"],
                                      project["confirmed"], project["open_questions"],
                                      project["next_step"], [], [], [])
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Quelltyp der gemeinsamen Suche").select_option("project")
                        page.get_by_label("Gemeinsame Suche").fill("Orion Besprechung")
                        page.get_by_role("button", name="Sinngemäß lokal suchen").click()
                        results = page.get_by_role("region", name="Sinngemäße Suchergebnisse")
                        expect(results.locator("article").filter(has_text="Mosaik")).to_be_visible(
                            timeout=120000)
                        assert "Orion_Besprechung.md" not in results.inner_text()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"local_semantic_file": "found_without_path",
                                          "local_semantic_memory": "found_and_opened",
                                          "removed_file_and_memory_links": "refreshed",
                                          "mobile_width": "fits",
                                          "cloud_calls": 0}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
