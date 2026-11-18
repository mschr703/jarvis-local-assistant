"""Real Edge/Ollama flow: multiple verified search sources in one local chat."""

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


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-search-multi-chat-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed"
        allowed.mkdir()
        originals = []
        for number in range(4):
            path = allowed / f"Orion_{number}.txt"
            path.write_text(f"Projekt Orion. Beleg {number}: Code OR-{number}.\n", encoding="utf-8")
            originals.append(path)
        db = Database(root / "data" / "jarvis.db")
        documents = DocumentStore(db)
        folder = documents.add_folder(str(allowed), "local_only")
        assert documents.index_folder(folder["id"])["indexed"] == 4
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(db.path.parent), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct",
                           JARVIS_MAX_OUTPUT_TOKENS="96")
        with (root / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                                       cwd=ROOT, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT)
            try:
                wait_ui(process)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge", headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 390, "height": 844},
                                                accept_downloads=True)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Gemeinsame Suche").fill("Orion")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        results = page.get_by_role("region", name="Suchergebnisse")
                        expect(results).to_contain_text("4 Dokumente")
                        choices = results.get_by_role("checkbox", name=re.compile("für lokalen Chat auswählen"))
                        expect(choices).to_have_count(4)
                        for index in range(3):
                            choices.nth(index).check()
                        expect(choices.nth(3)).to_be_disabled()
                        choices.nth(0).uncheck()
                        expect(choices.nth(3)).to_be_enabled()
                        choices.nth(3).check()
                        expect(page.get_by_role("region", name="Dokumente für lokalen Chat"))\
                            .to_contain_text("3 von höchstens drei")
                        originals[2].write_text("Projekt Orion. Beleg 2 wurde geändert.\n",
                                                encoding="utf-8")
                        page.get_by_role("button", name="Mit 3 Dokumenten lokalen Chat vorbereiten").click()
                        expect(page.get_by_role("alert")).to_contain_text("Originaldatei weicht vom Index ab")
                        expect(page.get_by_role("heading", name="Gemeinsame Suche")).to_be_visible()
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                            assert connection.execute("SELECT COUNT(*) FROM chat_attachments").fetchone()[0] == 0
                            assert connection.execute("SELECT COUNT(*) FROM chats").fetchone()[0] == 0
                        assert documents.index_folder(folder["id"])["indexed"] >= 1
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        expect(choices).to_have_count(4)
                        expect(page.get_by_role("region", name="Dokumente für lokalen Chat"))\
                            .to_have_count(0)
                        for index in range(3):
                            choices.nth(index).check()
                        page.get_by_role("button", name="Mit 3 Dokumenten lokalen Chat vorbereiten").click()
                        expect(page.get_by_role("heading", name="Chat", exact=True)).to_be_visible()
                        for index in range(3):
                            expect(page.get_by_text(f"{index + 1}. Orion_{index}.txt", exact=False))\
                                .to_be_visible()
                        expect(page.get_by_label("Nachricht")).to_have_value("")
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                            assert connection.execute("SELECT COUNT(*) FROM chat_attachments").fetchone()[0] == 0
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.get_by_label("Nachricht").fill("Nenne die Codes aus den drei Dateien.")
                        page.get_by_role("button", name="Senden", exact=True).click()
                        expect(page.locator(".message.assistant .usage")).to_contain_text(
                            "keine API-Gebühr", timeout=180000)
                        attachments = page.get_by_role("region", name="Dateianhänge im Gespräch")
                        for index in range(3):
                            expect(attachments).to_contain_text(f"Orion_{index}.txt")
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        page.get_by_role("button", name="Nenne die Codes aus den drei Dateien.").click()
                        for index in range(3):
                            expect(attachments).to_contain_text(f"Orion_{index}.txt")
                        assert not overflow_nodes(page), overflow_nodes(page)
                        originals[0].write_text("Projekt Orion. " * 120 + "Beleg 0: Code OR-0.\n",
                                                encoding="utf-8")
                        assert documents.index_folder(folder["id"])["indexed"] >= 1
                        document_id = next(item["id"] for item in documents.documents()
                                           if item["path"] == str(originals[0]))
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Gemeinsame Suche").fill("Orion")
                        page.get_by_label("Einzelnes Dokument tief durchsuchen").select_option(document_id)
                        page.get_by_role("button", name="Sinngemäß lokal suchen").click()
                        semantic = page.get_by_role("region", name="Sinngemäße Suchergebnisse")
                        expect(semantic.locator("article")).to_have_count(2, timeout=120000)
                        expect(semantic.get_by_role("checkbox", name=re.compile("für lokalen Chat auswählen")))\
                            .to_have_count(1)
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM chat_attachments").fetchone()[0] == 3
                            assert connection.execute(
                                "SELECT COUNT(*) FROM model_usage WHERE provider='ollama'").fetchone()[0] == 1
                        cloud_page = browser.new_page(viewport={"width": 390, "height": 844})
                        cloud_page.route("**/api/health", lambda route: route.fulfill(
                            status=200, content_type="application/json",
                            body=json.dumps({"status": "ok", "provider": "openrouter",
                                             "model": "fixture", "configured": True,
                                             "message": "Teststatus"})))
                        cloud_page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        cloud_page.get_by_role("button", name="Suche", exact=True).click()
                        cloud_page.get_by_label("Gemeinsame Suche").fill("Orion")
                        cloud_page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        cloud_results = cloud_page.get_by_role("region", name="Suchergebnisse")
                        expect(cloud_results.get_by_role("button", name="Im lokalen Chat verwenden")
                               .first).to_be_disabled()
                        expect(cloud_results.get_by_role("checkbox", name=re.compile(
                            "für lokalen Chat auswählen")).first).to_be_disabled()
                        cloud_page.close()
                        print(json.dumps({"selection_limit": 3, "stale_batch": "rejected_atomically",
                                          "deep_search_duplicate": "one_selection",
                                          "attachments": 3, "reload": "persists",
                                          "mobile_width": "fits", "provider": "ollama",
                                          "openrouter_action": "disabled"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
