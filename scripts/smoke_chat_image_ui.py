"""Isolated Edge/Ollama vision path for a user-selected image."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image
from playwright.sync_api import expect, sync_playwright
from smoke_code_project_ui import wait_ui
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, stop_test_server

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-chat-image-ui-") as temporary:
        root = Path(temporary)
        file = root / "Rot.png"
        Image.new("RGB", (256, 256), "red").save(file)
        original = file.read_bytes()
        data = root / "data"
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct",
                           JARVIS_OLLAMA_VISION_MODEL="moondream:1.8b",
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
                        expect(page.get_by_alt_text("Vorschau des gewählten Bildes")).to_be_visible()
                        page.get_by_label("Nachricht").fill("Welche Farbe hat das Bild?")
                        page.get_by_role("button", name="Senden", exact=True).click()
                        expect(page.locator(".message.assistant .usage")).to_contain_text(
                            "moondream:1.8b", timeout=180000)
                        attachments = page.get_by_role("region", name="Dateianhänge im Gespräch")
                        image = attachments.get_by_alt_text("Bildanhang Rot.png")
                        expect(image).to_be_visible()
                        expect(image).to_have_js_property("naturalWidth", 256, timeout=30000)
                        assert not overflow_nodes(page), overflow_nodes(page)
                        with page.expect_download() as download_event:
                            attachments.get_by_role("link", name="Unveränderte Kopie herunterladen").click()
                        assert Path(download_event.value.path()).read_bytes() == original
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        page.get_by_role("button", name="Welche Farbe hat das Bild?").click()
                        expect(image).to_be_visible(timeout=30000)
                        expect(image).to_have_js_property("naturalWidth", 256, timeout=30000)
                        db = Database(data / "jarvis.db")
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM chat_attachments").fetchone()[0] == 1
                            row = connection.execute("""SELECT provider,model,status FROM model_usage
                                WHERE purpose='chat'""").fetchone()
                            assert tuple(row) == ("ollama", "moondream:1.8b", "complete")
                        print(json.dumps({"vision": "answered", "image": "previewed",
                                          "download": "matches", "reload": "persists",
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
