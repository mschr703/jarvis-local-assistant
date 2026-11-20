"""Real Edge/Ollama path from chat intent to denied write, at mobile width."""

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

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test stoppen")
    with TemporaryDirectory(prefix="jarvis-chat-action-ui-") as temporary:
        root = Path(temporary)
        db = Database(root / "data" / "jarvis.db")
        chat = db.create_chat("Einkauf Aktion")
        db.add_message(chat["id"], "user",
                       "Speichere eine Notiz mit dem Titel Einkauf und dem Inhalt Milch kaufen.")
        db.add_message(chat["id"], "assistant", "Ich kann die Notiz vorbereiten.")
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(db.path.parent), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct")
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
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        page.locator(".chat-list-item").filter(has_text="Einkauf Aktion")\
                            .get_by_role("button", name="Einkauf Aktion").click()
                        section = page.get_by_role("region", name="Aktion aus Gespräch vorbereiten")
                        section.get_by_role("button", name="Aktion vorschlagen").click()
                        expect(section.get_by_text("save_note", exact=False)).to_be_visible(
                            timeout=180_000)
                        expect(section.locator("pre")).to_contain_text('"Milch kaufen"')
                        assert db.list_runs() == []
                        assert not list((db.path.parent / "notes").glob("*.txt"))
                        section.get_by_role("button", name="Zur Freigabe vorbereiten").click()
                        expect(section.get_by_text("Auftrag ist vorbereitet", exact=False))\
                            .to_be_visible(timeout=30_000)
                        runs = db.list_runs()
                        assert len(runs) == 1 and runs[0]["status"] == "waiting_approval"
                        section.get_by_role("button", name="Auftrag unter Aktivität ansehen").click()
                        page.get_by_role("button", name="Ablehnen").first.click()
                        expect(page.get_by_text("save_note · cancelled", exact=False))\
                            .to_be_visible(timeout=30_000)
                        assert db.get_run(runs[0]["id"])["status"] == "cancelled"
                        assert not list((db.path.parent / "notes").glob("*.txt"))
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"chat_proposal": "save_note", "preview": "no_side_effect",
                                          "write": "denied", "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
