"""Echter Edge/Ollama-Vorschlag und reproduzierbare Formularübergaben."""

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, stop_test_server, wait_ready

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database


def count(db_path: Path, table: str) -> int:
    assert table in {"tasks", "research_reports"}
    with closing(sqlite3.connect(db_path)) as connection:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Vorschlagstest mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-chat-suggestions-ui-") as temporary:
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
                        page.get_by_label("Nachricht").fill(
                            "Ich organisiere Projekt Atlas. Bitte antworte kurz: "
                            "Welche Unterlagen soll ich zusammenstellen und welche "
                            "öffentlichen Quellen sollte ich für die Behauptungen prüfen?")
                        page.get_by_role("button", name="Senden", exact=True).click()
                        expect(page.locator(".message.assistant .usage").last).to_be_visible(
                            timeout=240_000)
                        db_path = data / "jarvis.db"
                        db = Database(db_path)
                        chat = db.list_chats()[0]
                        message_id = db.list_messages(chat["id"])[-1]["id"]
                        assert count(db_path, "tasks") == 0
                        assert count(db_path, "research_reports") == 0

                        button = page.locator(".chat-suggestions>button")
                        with page.expect_response(lambda response: response.url.endswith(
                                "/suggestions"), timeout=180_000) as response_info:
                            button.click()
                        response = response_info.value
                        assert response.status == 200, response.text()
                        expect(page.locator(".chat-suggestions .item-row").first).to_be_visible()
                        real_suggestions = page.locator(".chat-suggestions .item-row").count()
                        assert 1 <= real_suggestions <= 2, page.locator(
                            ".chat-suggestions").inner_text()
                        assert count(db_path, "tasks") == 0
                        assert count(db_path, "research_reports") == 0

                        def fixture(route):
                            route.fulfill(status=200, content_type="application/json",
                                          body=json.dumps({"message_id": message_id,
                                                           "suggestions": [
                                                               {"kind": "task", "text":
                                                                "Unterlagen für Projekt Atlas sammeln"},
                                                               {"kind": "research", "text":
                                                                "Welche öffentlichen Quellen belegen Atlas?"}]}))

                        page.route("**/api/chats/*/suggestions", fixture)
                        button.click()
                        expect(page.locator(".chat-suggestions .item-row")).to_have_count(2)
                        page.get_by_role("button", name="Aufgabe vorbereiten").click()
                        expect(page.get_by_label("Aufgabe", exact=True)).to_have_value(
                            "Unterlagen für Projekt Atlas sammeln")
                        assert count(db_path, "tasks") == 0
                        page.get_by_label("Aufgabe", exact=True).fill(
                            "Unterlagen für Atlas bewusst sammeln")
                        page.get_by_role("button", name="Aufgabe speichern").click()
                        expect(page.get_by_text("Aufgabe gespeichert.", exact=False)).to_be_visible()
                        assert count(db_path, "tasks") == 1
                        assert not overflow_nodes(page), overflow_nodes(page)

                        page.get_by_role("button", name="Chat", exact=True).click()
                        page.locator(".chat-list-item").filter(has_text="Ich organisiere").get_by_role(
                            "button", name="Ich organisiere", exact=False).click()
                        button = page.locator(".chat-suggestions>button")
                        button.click()
                        expect(page.locator(".chat-suggestions .item-row")).to_have_count(2)
                        page.locator(".chat-suggestions .item-row").first.get_by_role(
                            "button", name="Ausblenden").click()
                        expect(page.locator(".chat-suggestions .item-row")).to_have_count(1)
                        page.get_by_role("button", name="Recherche vorbereiten").click()
                        expect(page.get_by_role("textbox", name="Frage", exact=True)).to_have_value(
                            "Welche öffentlichen Quellen belegen Atlas?")
                        assert count(db_path, "research_reports") == 0
                        page.get_by_role("textbox", name="Frage", exact=True).fill(
                            "Welche offiziellen Quellen belegen die Atlas-Aussage?")
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"real_ollama_suggestions": real_suggestions,
                                          "no_automatic_action": True,
                                          "task_form_saved_after_edit": True,
                                          "research_form_editable": True,
                                          "dismiss": True, "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
