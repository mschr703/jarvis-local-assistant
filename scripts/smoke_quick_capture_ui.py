"""Isolierte Edge-Probe für die einmalige Hotkey-Übergabe an die Eingabe."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from hotkey import handoff
from playwright.sync_api import expect, sync_playwright
from smoke_ui import ROOT, port_free, stop_test_server, wait_ready


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-quick-capture-ui-") as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(root / "data"), JARVIS_PROVIDER="ollama",
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
                        page = browser.new_page(viewport={"width": 390, "height": 800})
                        page.goto(handoff("Ausgewählter Text für Jarvis", [], ""),
                                  wait_until="domcontentloaded")
                        box = page.get_by_label("Nachricht")
                        expect(box).to_have_value("Ausgewählter Text für Jarvis")
                        expect(box).to_be_focused()
                        expect(page.get_by_text("Erst „Senden“ startet eine Modellanfrage.", exact=False)).to_be_visible()
                        assert "capture=" not in page.url
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                        second = page.evaluate("""async () => {
                          const r = await fetch('/api/chats'); return (await r.json()).length;
                        }""")
                        assert second == 0, second

                        file_path = str(root / "quelle.txt")
                        page.goto(handoff("", [file_path], ""), wait_until="domcontentloaded")
                        expect(page.get_by_text(file_path, exact=True)).to_be_visible()
                        expect(page.get_by_text("Dateien wurden nicht gelesen.", exact=False)).to_be_visible()
                        expect(page.get_by_label("Nachricht")).to_have_value("")
                        page.get_by_role("button", name="Dokumente öffnen").click()
                        expect(page.get_by_label("Absoluter Ordnerpfad")).to_be_visible()
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                        print(json.dumps({"text_prefilled": True, "focused": True,
                                          "model_calls": 0, "file_paths_only": True,
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
