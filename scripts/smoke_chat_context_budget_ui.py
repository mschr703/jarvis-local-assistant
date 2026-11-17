"""Echter Edge/Ollama-Klickpfad für sichtbare lokale Kontextauslassungen."""

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
        raise RuntimeError("Jarvis vor dem isolierten A08c-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-chat-context-budget-ui-") as temporary:
        root = Path(temporary)
        data = root / "data"
        db = Database(data / "jarvis.db")
        chat_id = db.create_chat()["id"]
        for index in range(20):
            db.add_message(chat_id, "user" if index % 2 == 0 else "assistant",
                           f"Atlas Statusnotiz {index}: " + "Der Plan bleibt geordnet. " * 25)
        original = db.list_messages(chat_id)
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct",
                           JARVIS_OLLAMA_CONTEXT_TOKENS="4096",
                           JARVIS_MAX_OUTPUT_TOKENS="256",
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
                            name="Atlas Statusnotiz", exact=False).click()
                        page.get_by_label("Nachricht").fill("Antworte nur mit OK.")
                        page.get_by_role("button", name="Senden", exact=True).click()
                        expect(page.locator(".messages .message.assistant .usage")).to_have_count(
                            11, timeout=300_000)
                        answer = db.list_messages(chat_id)[-1]
                        assert answer["context_omitted_count"] > 0
                        assert answer["context_token_budget"] == 2816
                        assert answer["context_estimated_tokens"] <= 2816
                        assert answer["context_latest_omitted_id"] in {
                            item["id"] for item in original}
                        usage = page.locator(".messages .message.assistant .usage").last
                        expect(usage).to_contain_text("Kontext geschätzt")
                        expect(usage).to_contain_text("ältere Rohbeiträge ausgelassen")
                        usage.get_by_role("button", name="Letzten ausgelassenen Beitrag zeigen").click()
                        expect(page.locator(
                            f"#chat-message-{answer['context_latest_omitted_id']}"))\
                            .to_be_in_viewport()
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        page.locator(".chat-list-item").first.get_by_role("button",
                            name="Atlas Statusnotiz", exact=False).click()
                        expect(page.locator(".messages .message.assistant .usage").last)\
                            .to_contain_text(f"{answer['context_omitted_count']} ältere Rohbeiträge")
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"real_ollama": True,
                                          "estimated_tokens": answer["context_estimated_tokens"],
                                          "budget": answer["context_token_budget"],
                                          "omitted": answer["context_omitted_count"],
                                          "jump_reload_mobile": True}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
