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
from jarvis.project_aliases import ProjectAliasStore
from jarvis.project_people import ProjectPeopleStore
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
        people = ProjectPeopleStore(db, states)
        person = people.add(project["id"], project["version"], "Mira Weber",
                            "reviewer", "Prüft die Nebenkosten", None)
        ProjectAliasStore(db, states, people).add(
            project["id"], project["version"], person["id"], person["version"],
            "Mimi", "Rufname im Umzugsteam", None)
        document = docs.documents()[0]
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
                        expect(results.get_by_text("1 Dokumente · 1 Erinnerungen · 1 Arbeitsstände · 0 Lebensereignisse")).to_be_visible(timeout=120000)
                        expect(results.locator("article").filter(has_text="Gespräch vom Dienstag")).to_be_visible()
                        expect(results.locator("article").filter(has_text="Umzug")).to_contain_text("Arbeitsstand v1")
                        expect(results.locator("article").filter(has_text="Umzug")).to_contain_text(
                            "Projektperson: Mira Weber")
                        expect(results.locator("article").filter(has_text="Umzug")).to_contain_text(
                            "Personenalias: Mimi → Mira Weber")
                        expect(results.get_by_text("Ähnlichkeit", exact=False).first).to_be_visible()
                        assert "Kuchen.md" not in results.locator("article").first.inner_text()
                        assert not overflow_nodes(page), overflow_nodes(page)

                        for label, kind, source_id in (
                            ("Kuchen.md", "document", document["id"]),
                            ("Gespräch vom Dienstag", "memory", memory["id"]),
                            ("Umzug", "project", project["id"]),
                        ):
                            results.locator("article").filter(has_text=label).get_by_role(
                                "button", name="Quelle öffnen").click()
                            expect(page.locator(f"#search-source-{kind}-{source_id}")).to_be_visible()
                            page.get_by_role("button", name="Suche", exact=True).click()
                            expect(results.get_by_text("1 Dokumente · 1 Erinnerungen · 1 Arbeitsstände · 0 Lebensereignisse")).to_be_visible()

                        kind_select = page.get_by_label("Quelltyp der gemeinsamen Suche")
                        for kind, label in (("memory", "0 Dokumente · 1 Erinnerungen · 0 Arbeitsstände · 0 Lebensereignisse"),
                                            ("project", "0 Dokumente · 0 Erinnerungen · 1 Arbeitsstände · 0 Lebensereignisse")):
                            kind_select.select_option(kind)
                            assert page.get_by_label("Dokumentordner für sinngemäße Suche").is_disabled()
                            page.get_by_label("Gemeinsame Suche").fill("Schlüssel am Freitag")
                            page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                            lexical = page.get_by_role("region", name="Suchergebnisse")
                            expect(lexical).to_contain_text("1 " + ("Erinnerungen" if kind == "memory" else "Arbeitsstände"))
                            button.click()
                            results = page.get_by_role("region", name="Sinngemäße Suchergebnisse")
                            expect(results.get_by_text(label)).to_be_visible(timeout=120000)
                            expect(results.get_by_text("Geprüft: 0 Dokumentfundstellen",
                                                       exact=False)).to_be_visible()
                        kind_select.select_option("document")
                        page.get_by_label("Gemeinsame Suche").fill("Wie backe ich Apfelkuchen?")
                        button.click()
                        results = page.get_by_role("region", name="Sinngemäße Suchergebnisse")
                        expect(results.get_by_text("1 Dokumente · 0 Erinnerungen · 0 Arbeitsstände · 0 Lebensereignisse")).to_be_visible(
                            timeout=120000)
                        expect(results.locator("article")).to_contain_text("Kuchen.md")
                        results.locator("article").get_by_role(
                            "button", name="Mit dieser Fundstelle Entwurf starten").click()
                        expect(page.get_by_role("heading", name="Entwürfe", exact=True)).to_be_visible()
                        expect(page.get_by_text("1 Fundstelle(n) ausgewählt.", exact=False)).to_be_visible()
                        expect(page.get_by_role("list", name="Ausgewählte Fundstellen")).to_contain_text(
                            "Kuchen.md")
                        page.get_by_role("button", name="Suche", exact=True).click()

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
                        expect(results.get_by_text("1 Dokumente · 1 Erinnerungen · 1 Arbeitsstände · 0 Lebensereignisse")).to_be_visible(timeout=120000)
                        assert "Samstag" in results.inner_text()
                        assert "Freitag" not in results.inner_text()
                        expect(results.locator("article").filter(has_text="Umzug")).to_contain_text("Arbeitsstand v2")
                        assert not overflow_nodes(page), overflow_nodes(page)
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"source_kinds": 3, "filters": "lexical_and_semantic",
                                          "source_navigation": "all_three_semantic",
                                          "draft_source": "selected_without_model",
                                          "updates": "current",
                                          "mobile_width": "fits", "cloud_calls": 0}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
