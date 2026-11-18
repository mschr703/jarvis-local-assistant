"""Real Edge/Ollama path for choosing and reopening a local chat attachment."""

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


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-chat-attachment-ui-") as temporary:
        root = Path(temporary)
        data = root / "data"
        file = root / "Wohnungsnotiz.txt"
        content = "Die Kaution für Projekt Elbe beträgt genau 800 Euro."
        file.write_text(content, encoding="utf-8")
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama",
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
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded",
                                  timeout=120000)
                        page.get_by_role("button", name="Chat", exact=True).click()
                        page.get_by_label("Datei für Chat").set_input_files(str(file))
                        expect(page.get_by_text("Gewählt: Wohnungsnotiz.txt", exact=False)).to_be_visible()
                        page.get_by_label("Nachricht").fill("Wie hoch ist die Kaution für Projekt Elbe?")
                        page.get_by_role("button", name="Senden", exact=True).click()
                        expect(page.locator(".message.assistant .usage")).to_contain_text(
                            "keine API-Gebühr", timeout=180000)
                        attachments = page.get_by_role("region", name="Dateianhänge im Gespräch")
                        expect(attachments.get_by_text("Wohnungsnotiz.txt", exact=False)).to_be_visible()
                        expect(attachments.get_by_text("SHA-256", exact=False)).to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        page.get_by_role("button", name="Wie hoch ist die Kaution für Projekt Elbe?").click()
                        expect(attachments.get_by_text("Wohnungsnotiz.txt", exact=False)).to_be_visible(
                            timeout=30000)
                        with page.expect_download() as download_event:
                            attachments.get_by_role("link", name="Unveränderte Kopie herunterladen").click()
                        downloaded = download_event.value
                        assert downloaded.suggested_filename == file.name
                        assert Path(downloaded.path()).read_text(encoding="utf-8") == content
                        db = Database(data / "jarvis.db")
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM chat_attachments").fetchone()[0] == 1
                            assert connection.execute("SELECT COUNT(*) FROM model_usage WHERE provider='ollama'").fetchone()[0] == 1
                        print(json.dumps({"attachment": "linked", "download": "matches",
                                          "reload": "persists", "mobile_width": "fits",
                                          "provider": "ollama"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
