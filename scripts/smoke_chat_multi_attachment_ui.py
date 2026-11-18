"""Real Edge/Ollama path for multiple files in one chat message."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image
from playwright.sync_api import expect, sync_playwright
from smoke_chat_attachment_ui import text_pdf
from smoke_code_project_ui import wait_ui
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, stop_test_server

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-chat-multi-ui-") as temporary:
        root = Path(temporary)
        first = root / "Erste.txt"
        second = root / "Zweite.md"
        third = root / "Dritte.pdf"
        image = root / "Rot.png"
        first.write_text("Projekt Elbe: Miete 900 Euro.", encoding="utf-8")
        second.write_text("Projekt Elbe: Kaution 800 Euro.", encoding="utf-8")
        third.write_bytes(text_pdf("Projekt Elbe: Einzug im Oktober."))
        Image.new("RGB", (256, 256), "red").save(image)
        originals = [first, second, third, image]
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
                        page.get_by_label("Datei für Chat").set_input_files(
                            [str(first), str(second), str(third)])
                        expect(page.get_by_text("1. Erste.txt", exact=False)).to_be_visible()
                        expect(page.get_by_text("3. Dritte.pdf", exact=False)).to_be_visible()
                        page.get_by_label("Nachricht").fill("Vergleiche die drei Quellen")
                        page.get_by_role("button", name="Senden", exact=True).click()
                        expect(page.locator(".message.assistant .usage")).to_have_count(
                            1, timeout=180000)
                        attachments = page.get_by_role("region", name="Dateianhänge im Gespräch")
                        for file in (first, second, third):
                            expect(attachments.get_by_text(file.name, exact=False)).to_be_visible()
                        page.get_by_label("Datei für Chat").set_input_files(
                            [str(image), str(first)])
                        page.get_by_label("Nachricht").fill("Welche Farbe hat das Bild?")
                        page.get_by_role("button", name="Senden", exact=True).click()
                        try:
                            expect(page.locator(".message.assistant .usage")).to_have_count(
                                2, timeout=180000)
                        except AssertionError:
                            print(json.dumps({"alerts": page.locator("[role=alert]").all_text_contents(),
                                              "usage_count": page.locator(".message.assistant .usage").count(),
                                              "server_log": (root / "server.log").read_text(
                                                  encoding="utf-8", errors="replace")[-1500:]}))
                            raise
                        expect(page.locator(".message.assistant .usage").last).to_contain_text(
                            "moondream:1.8b")
                        preview = attachments.get_by_alt_text("Bildanhang Rot.png")
                        expect(preview).to_have_js_property("naturalWidth", 256, timeout=30000)
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        page.get_by_role("button", name="Vergleiche die drei Quellen").click()
                        expect(preview).to_have_js_property("naturalWidth", 256, timeout=30000)
                        for file in originals:
                            row = attachments.locator(".item-row").filter(has_text=file.name).first
                            with page.expect_download() as download_event:
                                row.get_by_role("link", name="Unveränderte Kopie herunterladen").click()
                            assert Path(download_event.value.path()).read_bytes() == file.read_bytes()
                        db = Database(data / "jarvis.db")
                        with db.connect() as connection:
                            rows = connection.execute("""SELECT message_id,COUNT(*) FROM chat_attachments
                                GROUP BY message_id ORDER BY COUNT(*) DESC""").fetchall()
                            assert [row[1] for row in rows] == [3, 2]
                        print(json.dumps({"attachments_per_message": [3, 2],
                                          "vision": "answered", "preview": "loaded",
                                          "downloads": "match", "reload": "persists",
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
