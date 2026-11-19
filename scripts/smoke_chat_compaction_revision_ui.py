"""Edge/Ollama-Klickpfad für korrigierbaren Chat-Zustand und Antwortbeleg."""

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
        raise RuntimeError("Jarvis vor dem isolierten A08b-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-chat-compaction-revision-ui-") as temporary:
        root = Path(temporary)
        data = root / "data"
        db = Database(data / "jarvis.db")
        chat_id = db.create_chat()["id"]
        for index in range(20):
            content = ("Für Projekt Atlas lautet die exakte interne Materialbezeichnung Kupferfenster. Nicht zu Kupfer abkürzen."
                       if index < 2 else f"Statusnotiz {index}: Keine Änderung am Projektablauf.")
            db.add_message(chat_id, "user" if index % 2 == 0 else "assistant", content)
        history = db.list_messages(chat_id)
        db.save_chat_compaction(chat_id, 0, history[-1]["id"],
                                [item["id"] for item in history[:12]], [
                                    {"kind": "decision", "text": "Exakte interne Materialbezeichnung: Kupferfenster (nicht Kupfer abkürzen)",
                                     "message_ids": [history[0]["id"]]},
                                    {"kind": "goal", "text": "Falscher Punkt über Lieferfrist",
                                     "message_ids": [history[3]["id"]]}], "qwen3:4b-instruct")
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
                        latest = page.locator(".chat-compactions .compaction-state").first
                        expect(latest.locator(".item-row")).to_have_count(2)
                        latest.locator(".item-row").filter(has_text="Falscher Punkt").get_by_role(
                            "button", name="Diesen Punkt entfernen").click()
                        expect(latest).to_contain_text("Fassung 2")
                        expect(latest).to_contain_text("von dir korrigiert")
                        expect(latest.locator(".item-row")).to_have_count(1)
                        assert "Falscher Punkt" not in latest.inner_text()
                        latest.get_by_role("button", name=f"Quelle {history[0]['id'][:8]} anzeigen").click()
                        expect(page.locator(f"#chat-message-{history[0]['id']}")).to_be_in_viewport()
                        assert db.list_chat_compactions(chat_id)[1]["entries"][1]["text"].startswith(
                            "Falscher Punkt")
                        assert len(db.list_messages(chat_id)) == 20

                        page.get_by_label("Nachricht").fill(
                            "Wie lautet die exakte interne Materialbezeichnung für Projekt Atlas? "
                            "Übernimm sie vollständig und ohne Abkürzung aus dem älteren Beschluss.")
                        page.get_by_role("button", name="Senden", exact=True).click()
                        expect(page.locator(".messages .message.assistant .usage")).to_have_count(
                            11, timeout=300_000)
                        answer = db.list_messages(chat_id)[-1]
                        assert "Kupferfenster" in answer["content"], answer["content"]
                        assert answer["compaction_version"] == 2
                        expect(page.locator(".messages .message.assistant .usage").last).to_contain_text(
                            "Gesprächszustand v2")

                        page.get_by_role("button", name="Aktuellen Zustand zurücksetzen").click()
                        dialog = page.get_by_role("alertdialog", name="Gesprächszustand zurücksetzen")
                        expect(dialog).to_be_visible()
                        assert db.latest_chat_compaction(chat_id)["version"] == 2
                        dialog.get_by_role("button", name="Zustand wirklich zurücksetzen").click()
                        expect(latest).to_contain_text("Fassung 3")
                        expect(latest).to_contain_text("von dir zurückgesetzt")
                        assert db.latest_chat_compaction(chat_id)["entries"] == []
                        assert db.latest_chat_compaction(chat_id)["covered_message_ids"] == []
                        assert db.latest_chat_compaction(chat_id)["created_by"] == "user_reset"
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        page.locator(".chat-list-item").first.get_by_role("button",
                            name="Für Projekt Atlas", exact=False).click()
                        expect(page.locator(".chat-compactions .compaction-state").first).to_contain_text(
                            "Fassung 3")
                        expect(page.get_by_text("Frühere Fassungen (2)")).to_be_visible()
                        assert len(db.list_messages(chat_id)) == 22
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"removed_point": True, "old_version_preserved": True,
                                          "real_ollama_answer_version": 2,
                                          "reset_confirmed": True, "reload": True,
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
