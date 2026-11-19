"""Edge/Ollama path for a saved task model and its actual usage receipt."""

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

DEFAULT_MODEL = "qwen3:4b-instruct"
CHAT_MODEL = "qwen3:4b"


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-model-routes-ui-") as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(root / "data"), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL=DEFAULT_MODEL, JARVIS_MAX_OUTPUT_TOKENS="64")
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
                        page.get_by_role("heading", name="Jarvis ist bereit.").wait_for(timeout=30000)
                        expect(page.get_by_label("Modell für Chat")).to_have_value("")
                        page.get_by_label("Modell für Chat").fill(CHAT_MODEL)
                        page.get_by_role("button", name="Einstellungen speichern").click()
                        expect(page.get_by_text("Gespeichert.", exact=True)).to_be_visible(timeout=30000)
                        page.get_by_role("button", name="Chat", exact=True).click()
                        banner = page.get_by_label("Modellwahl für Chat")
                        expect(banner).to_contain_text(f"ollama / {CHAT_MODEL} · Aufgabenregel", timeout=30000)
                        page.get_by_role("button", name="Neues Gespräch").click()
                        page.get_by_label("Nachricht").fill("Antworte mit genau einem kurzen Satz.")
                        page.get_by_role("button", name="Senden", exact=True).click()
                        expect(page.locator(".message.assistant .usage")).to_have_count(1,
                                                                                       timeout=180000)
                        expect(page.locator(".message.assistant .usage")).to_contain_text(CHAT_MODEL)
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        expect(page.get_by_label("Modellwahl für Chat")).to_contain_text(
                            f"ollama / {CHAT_MODEL} · Aufgabenregel", timeout=30000)
                        page.get_by_role("button", name="Übersicht", exact=True).click()
                        expect(page.get_by_label("Modell für Chat")).to_have_value(CHAT_MODEL,
                                                                                  timeout=30000)
                        expect(page.get_by_text(f"Chat · ollama / {CHAT_MODEL}", exact=False)).to_be_visible(timeout=30000)
                        long_model = "local/" + "a" * 100
                        page.get_by_label("Modell für Chat").fill(long_model)
                        page.get_by_role("button", name="Einstellungen speichern").click()
                        expect(page.get_by_text("Gespeichert.", exact=True)).to_be_visible(timeout=30000)
                        page.get_by_role("button", name="Chat", exact=True).click()
                        expect(page.get_by_label("Modellwahl für Chat")).to_contain_text(long_model)
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"route": CHAT_MODEL, "receipt": "matched",
                                          "reload": "persisted", "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
