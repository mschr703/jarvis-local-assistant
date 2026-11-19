"""Echter Edge/Ollama-Klickpfad für belegten lokalen Gesprächszustand."""

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
        raise RuntimeError("Jarvis vor dem isolierten A08a-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-chat-compaction-ui-") as temporary:
        root = Path(temporary)
        data = root / "data"
        db = Database(data / "jarvis.db")
        chat_id = db.create_chat()["id"]
        for index in range(20):
            if index == 0:
                content = ("Für Projekt Atlas ist verbindlich entschieden: Das Material heißt "
                           "Kupferfenster. Bitte halte diese Entscheidung im Gespräch fest.")
            elif index == 1:
                content = ("Entscheidung im Projekt Atlas bestätigt: Das Material ist "
                           "Kupferfenster.")
            else:
                content = (f"Statusnotiz {index}: Die Projektorganisation läuft weiter. "
                           "Es gibt keine neue Materialentscheidung.")
            db.add_message(chat_id, "user" if index % 2 == 0 else "assistant", content)
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
                        page.set_default_timeout(300_000)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        page.locator(".chat-list-item").first.get_by_role("button",
                            name="Für Projekt Atlas", exact=False).click()
                        expect(page.locator(".message")).to_have_count(20)
                        with page.expect_response(lambda response: response.request.method == "POST"
                                and response.url.endswith("/compactions"),
                                timeout=300_000) as response_info:
                            page.get_by_role("button", name="Ältere Beiträge zusammenfassen").click()
                        response = response_info.value
                        assert response.status == 200, response.text()
                        expect(page.locator(".chat-compactions .compaction-state")).to_be_visible(
                            timeout=15_000)
                        snapshot = db.latest_chat_compaction(chat_id)
                        assert snapshot is not None and snapshot["version"] == 1
                        assert len(snapshot["covered_message_ids"]) == 12
                        decisions = [entry for entry in snapshot["entries"]
                                     if "Kupferfenster" in entry["text"]]
                        assert decisions, snapshot["entries"]
                        assert all(set(entry["message_ids"]).issubset(
                            set(snapshot["covered_message_ids"])) for entry in snapshot["entries"])
                        message_box = page.locator(".messages")
                        bottom = message_box.evaluate("el => { el.scrollTop = el.scrollHeight; return el.scrollTop; }")
                        assert bottom > 0
                        page.locator(".chat-compactions .item-row").filter(
                            has_text="Kupferfenster").first.get_by_role(
                            "button", name="Quelle", exact=False).first.click()
                        page.wait_for_function("before => document.querySelector('.messages').scrollTop < before", arg=bottom)
                        assert len(db.list_messages(chat_id)) == 20

                        page.get_by_label("Nachricht").fill(
                            "Welches Material wurde für Projekt Atlas verbindlich beschlossen? "
                            "Nenne es kurz.")
                        page.get_by_role("button", name="Senden", exact=True).click()
                        expect(page.locator(".messages .message.assistant .usage")).to_have_count(11,
                            timeout=300_000)
                        answer = db.list_messages(chat_id)[-1]["content"]
                        assert "Kupferfenster" in answer, answer
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        page.locator(".chat-list-item").first.get_by_role("button",
                            name="Für Projekt Atlas", exact=False).click()
                        expect(page.locator(".chat-compactions .compaction-state")).to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"seeded_raw_messages": 20,
                                          "real_ollama_compaction": True,
                                          "version": snapshot["version"],
                                          "covered_messages": 12,
                                          "source_navigation": True,
                                          "decision_recalled": True,
                                          "reload": True, "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
