"""Real Edge/Ollama path for explicitly reviewed chat memory proposals."""

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
from jarvis.memory_review import MemoryReviewStore


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-memory-review-") as temporary:
        root = Path(temporary)
        data = root / "data"
        db = Database(data / "jarvis.db")
        chat = db.create_chat()
        for statement in (
            "Mein Lieblingsfach ist Biologie und ich lerne es gerade intensiv.",
            "Ich wohne in Bremen und gehe dort zur Schule.",
            "Ich mag kurze Antworten mit konkreten Beispielen.",
        ):
            db.add_message(chat["id"], "user", statement)
        db.add_message(chat["id"], "assistant", "Verstanden.")
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
                        page.get_by_role("button", name="Gedächtnis", exact=True).click()
                        expect(page.get_by_label("Gespräch für Erinnerungen")).to_have_value(
                            chat["id"], timeout=30_000)
                        page.get_by_role("button", name="Erinnerungen vorschlagen").click()
                        expect(page.get_by_text("Vorschlag/Vorschläge zur Prüfung bereit.",
                                                exact=False)).to_be_visible(timeout=240_000)
                        store = MemoryReviewStore(Database(data / "jarvis.db"))
                        items = store.list(chat["id"])
                        assert 1 <= len(items) <= 3
                        assert all(item["source_available"] for item in items)
                        assert all(item["source_quote"] in next(
                            message["content"] for message in db.list_messages(chat["id"])
                            if message["id"] == item["message_id"]) for item in items)
                        first = items[0]
                        page.get_by_label(f"Erinnerungstext {first['id']}").fill(
                            "Ich bevorzuge in Jarvis kurze konkrete Antworten.")
                        page.get_by_role("button", name="Bestätigen", exact=True).first.click()
                        expect(page.get_by_text("Erinnerung bewusst gespeichert.")).to_be_visible()
                        assert store.get(first["id"])["status"] == "accepted"
                        assert len(Database(data / "jarvis.db").list_messages(chat["id"])) == 4
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.get_by_role("button", name="Original im Chat öffnen").first.click()
                        expect(page.locator(f"#chat-message-{first['message_id']}")).to_be_visible(
                            timeout=30_000)
                        expect(page.locator(".chat-memory-choice input[type=checkbox]")).to_have_count(
                            1, timeout=30_000)
                        page.locator(".chat-memory-choice input[type=checkbox]").check()
                        page.get_by_label("Nachricht", exact=True).fill(
                            "Bitte antworte in einem kurzen Satz auf diese Nachricht.")
                        page.get_by_role("button", name="Senden", exact=True).click()
                        expect(page.get_by_text("Bewusst verwendete Erinnerungen (1)"
                                                )).to_be_visible(timeout=240_000)
                        latest = db.list_messages(chat["id"])[-1]
                        assert latest["memory_context"][0]["text_snapshot"] == (
                            "Ich bevorzuge in Jarvis kurze konkrete Antworten.")
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Gedächtnis", exact=True).click()
                        expect(page.get_by_label("Gespräch für Erinnerungen")).to_have_value(
                            chat["id"], timeout=30_000)
                        expect(page.get_by_text("Ich bevorzuge in Jarvis kurze konkrete Antworten.",
                                                exact=True).first).to_be_visible(timeout=30_000)
                        expect(page.get_by_text("Zuletzt für eine Chatantwort verwendet:",
                                                exact=False)).to_be_visible(timeout=30_000)
                        if len(items) >= 2:
                            second = items[1]
                            page.get_by_label(f"Erinnerungstext {second['id']}").fill(
                                second["candidate_text"])
                            page.get_by_role("button", name="Bestätigen", exact=True).first.click()
                            expect(page.get_by_text("Erinnerung bewusst gespeichert."))\
                                .to_be_visible(timeout=30_000)
                            before_scan = len(Database(data / "jarvis.db").list_messages(chat["id"]))
                            with page.expect_response(lambda response: (
                                    response.url.endswith("/memory-proposals/conflicts")
                                    and response.request.method == "POST"), timeout=240_000) as scan:
                                page.get_by_role("button", name="Widersprüche prüfen").click()
                            assert scan.value.status == 200
                            assert isinstance(scan.value.json(), list)
                            assert len(Database(data / "jarvis.db").list_messages(chat["id"])) == before_scan
                        page.get_by_label(f"Gültig bis {first['id']}").fill("2000-01-01")
                        first_row = page.get_by_label(f"Gültig bis {first['id']}").locator(
                            "xpath=ancestor::div[contains(@class,'item-row')]")
                        first_row.get_by_role("button", name="Korrektur speichern").click()
                        expect(page.get_by_text("Diese Erinnerung ist seit 2000-01-01 abgelaufen",
                                                exact=False)).to_be_visible(timeout=30_000)
                        assert store.get(first["id"])["validity_state"] == "expired"
                        first_row.get_by_role("button", name="Original im Chat öffnen").click()
                        expect(page.locator(".chat-memory-choice input[type=checkbox]")).to_have_count(
                            1 if len(items) >= 2 else 0, timeout=30_000)
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"ollama": True, "proposals": len(items),
                                          "literal_source": True, "explicit_accept": True,
                                          "selected_memory_used": True, "reload": True,
                                          "validity_blocks_expired": True,
                                          "conflict_scan": len(items) >= 2,
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
