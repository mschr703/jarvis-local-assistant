"""Real Edge/Ollama flow: verified search document to deliberate local chat."""

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


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-search-chat-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed"
        allowed.mkdir()
        original = allowed / "Aurora_Notiz.md"
        original_text = "# Projekt Aurora\nDer Projektcode lautet AZ-42.\n"
        original.write_text(original_text, encoding="utf-8")
        db = Database(root / "data" / "jarvis.db")
        documents = DocumentStore(db)
        folder = documents.add_folder(str(allowed), "local_only")
        assert documents.index_folder(folder["id"])["indexed"] == 1
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
                        page.get_by_label("Gemeinsame Suche").fill("Aurora")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        results = page.get_by_role("region", name="Suchergebnisse")
                        expect(results).to_contain_text("Aurora_Notiz.md")
                        use_in_chat = results.get_by_role("button", name="Im lokalen Chat verwenden")
                        expect(use_in_chat).to_be_enabled(timeout=120000)
                        use_in_chat.click()
                        expect(page.get_by_role("heading", name="Chat", exact=True)).to_be_visible()
                        expect(page.get_by_text("1. Aurora_Notiz.md", exact=False)).to_be_visible()
                        expect(page.get_by_label("Nachricht")).to_have_value("")
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                            assert connection.execute("SELECT COUNT(*) FROM chat_attachments").fetchone()[0] == 0
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_role("button", name="Sinngemäß lokal suchen").click()
                        semantic = page.get_by_role("region", name="Sinngemäße Suchergebnisse")
                        expect(semantic).to_contain_text("Aurora_Notiz.md", timeout=120000)
                        semantic.get_by_role("button", name="Im lokalen Chat verwenden").click()
                        expect(page.get_by_text("1. Aurora_Notiz.md", exact=False)).to_be_visible()
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                        page.get_by_label("Nachricht").fill("Welcher Projektcode steht in der Datei?")
                        page.get_by_role("button", name="Senden", exact=True).click()
                        expect(page.locator(".message.assistant .usage")).to_contain_text(
                            "keine API-Gebühr", timeout=180000)
                        attachments = page.get_by_role("region", name="Dateianhänge im Gespräch")
                        expect(attachments).to_contain_text("Aurora_Notiz.md")
                        original.write_text("# Projekt Aurora\nDer Projektcode wurde geändert.\n",
                                            encoding="utf-8")
                        page.get_by_role("button", name="Suche", exact=True).click()
                        expect(results).to_contain_text("Aurora_Notiz.md")
                        use_in_chat.click()
                        expect(page.get_by_role("alert")).to_contain_text(
                            "Originaldatei weicht vom Index ab")
                        expect(page.get_by_role("heading", name="Gemeinsame Suche")).to_be_visible()
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        page.get_by_role("button", name="Welcher Projektcode steht in der Datei?").click()
                        expect(attachments).to_contain_text("Aurora_Notiz.md")
                        with page.expect_download() as event:
                            attachments.get_by_role("link", name="Unveränderte Kopie herunterladen").click()
                        assert Path(event.value.path()).read_text(encoding="utf-8") == original_text
                        assert not overflow_nodes(page), overflow_nodes(page)
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM chat_attachments").fetchone()[0] == 1
                            assert connection.execute(
                                "SELECT COUNT(*) FROM model_usage WHERE provider='ollama'").fetchone()[0] == 1
                            assert connection.execute(
                                "SELECT COUNT(*) FROM model_usage WHERE provider='openrouter'").fetchone()[0] == 0
                        print(json.dumps({"search_to_chat": "lexical_and_semantic_verified", "click_model_calls": 0,
                                          "stale_original": "rejected", "reload": "copy_matches",
                                          "mobile_width": "fits", "provider": "ollama"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
