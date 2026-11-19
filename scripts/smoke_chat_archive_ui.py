"""Echter Edge/Ollama-Klicktest für zwei lokale Gespräche und ihr Archiv."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, stop_test_server, wait_ready

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Chat-Archiv-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-chat-archive-ui-") as temporary:
        root = Path(temporary)
        data = root / "data"
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct",
                           JARVIS_TIMEZONE="Europe/Paris")
        with (root / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                                       cwd=ROOT, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT)
            try:
                wait_ready(process, timeout=120)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge", headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 390, "height": 844})
                        page.set_default_timeout(240_000)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        db = Database(data / "jarvis.db")

                        page.get_by_label("Nachricht").fill(
                            "Projekt Alpha: Antworte mit einem kurzen Satz.")
                        page.get_by_role("button", name="Senden", exact=True).click()
                        expect(page.locator(".message.assistant .usage").last).to_be_visible(
                            timeout=240_000)
                        first = db.list_chats()[0]["id"]
                        assert len(db.list_messages(first)) == 2
                        assert db.list_messages(first)[-1]["content"].strip()

                        page.get_by_role("button", name="Neues Gespräch").click()
                        page.get_by_label("Nachricht").fill(
                            "Projekt Beta: Antworte mit einem kurzen Satz.")
                        page.get_by_role("button", name="Senden", exact=True).click()
                        expect(page.locator(".message.assistant .usage").last).to_be_visible(
                            timeout=240_000)
                        second = next(item["id"] for item in db.list_chats() if item["id"] != first)
                        assert len(db.list_messages(second)) == 2
                        db.add_message(first, "user", "Die Überraschung steht nur im späteren Verlauf.")

                        page.get_by_label("Gespräche suchen").fill("ÜBERRASCHUNG")
                        page.get_by_role("button", name="Suchen", exact=True).click()
                        alpha = page.locator(".chat-list-item").filter(has_text="Projekt Alpha")
                        expect(alpha).to_be_visible()
                        expect(page.locator(".chat-list-item")).to_have_count(1)
                        expect(alpha.get_by_text("Treffer: Die Überraschung", exact=False)).to_be_visible()
                        alpha.get_by_role("button", name="Archivieren").click()
                        expect(page.locator(".chat-list-item")).to_have_count(0)
                        page.get_by_label("Archiv anzeigen").check()
                        alpha = page.locator(".chat-list-item").filter(has_text="Projekt Alpha")
                        expect(alpha).to_be_visible()
                        alpha.get_by_role("button", name="Wiederherstellen").click()
                        expect(page.locator(".chat-list-item")).to_have_count(0)
                        page.get_by_label("Archiv anzeigen").uncheck()
                        expect(alpha).to_be_visible()
                        page.get_by_role("button", name="Alle", exact=True).click()
                        beta = page.locator(".chat-list-item").filter(has_text="Projekt Beta")
                        expect(beta).to_be_visible()
                        beta.get_by_role("button", name="Löschen", exact=True).click()
                        expect(page.get_by_role("alertdialog", name="Gespräch löschen")).to_be_visible()
                        assert db.get_chat(second) is not None
                        page.get_by_role("button", name="Dauerhaft löschen").click()
                        expect(beta).to_have_count(0)
                        assert db.get_chat(second) is None
                        assert len(db.list_messages(first)) == 3
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        alpha = page.locator(".chat-list-item").filter(has_text="Projekt Alpha")
                        expect(alpha).to_be_visible()
                        alpha.get_by_role("button", name="Projekt Alpha", exact=False).click()
                        expect(page.get_by_text("Die Überraschung steht nur im späteren Verlauf.",
                                                exact=True)).to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"real_ollama_chats": 2, "content_search": True,
                                          "archive_restore": True, "confirmed_delete": True,
                                          "other_chat_preserved": True, "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
