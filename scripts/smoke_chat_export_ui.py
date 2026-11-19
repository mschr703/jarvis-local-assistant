"""Real Edge download of one chat and its verified sent attachments."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from playwright.sync_api import expect, sync_playwright
from smoke_code_project_ui import wait_ui
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, stop_test_server

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.chat_attachments import ChatAttachmentStore
from jarvis.db import Database


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Chat-Export-Test stoppen")
    with TemporaryDirectory(prefix="jarvis-chat-export-ui-") as temporary:
        root = Path(temporary)
        db = Database(root / "data" / "jarvis.db")
        store = ChatAttachmentStore(db)
        chat = db.create_chat("Projekt Orion")
        first = store.save(chat["id"], "Beleg.txt", b"Projekt Orion Vertrag 2026")
        second = store.save(chat["id"], "Beleg.txt", b"Projekt Saturn Vertrag 2026")
        store.save(chat["id"], "Entwurf.txt", b"Ungesendet")
        db.add_message(chat["id"], "user", "Vergleiche beide Belege.",
                       attachment_ids=[first["id"], second["id"]])
        db.add_message(chat["id"], "assistant", "Orion und Saturn sind verschieden.")
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(db.path.parent), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="fixture")
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
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        page.locator(".chat-list-item").filter(has_text="Projekt Orion")\
                            .get_by_role("button", name="Projekt Orion").click()
                        expect(page.get_by_text("Vergleiche beide Belege.", exact=True))\
                            .to_be_visible()
                        with page.expect_download() as event:
                            page.get_by_role("button", name="Gespräch mit Anhängen als ZIP herunterladen")\
                                .click()
                        download = event.value
                        assert download.suggested_filename.endswith(".zip")
                        with ZipFile(download.path()) as archive:
                            names = archive.namelist()
                            assert names == ["transcript.md", "manifest.json",
                                             "attachments/001-Beleg.txt",
                                             "attachments/002-Beleg.txt"]
                            assert archive.read(names[2]) == b"Projekt Orion Vertrag 2026"
                            assert archive.read(names[3]) == b"Projekt Saturn Vertrag 2026"
                            assert "Vergleiche beide Belege." in archive.read(
                                "transcript.md").decode("utf-8")
                            manifest = json.loads(archive.read("manifest.json"))
                            assert len(manifest["attachments"]) == 2
                            assert "Entwurf.txt" not in str(manifest)
                            assert str(root) not in str(manifest)
                        path = store._path(first["id"], ".txt")
                        path.write_bytes(b"andere Fassung")
                        page.get_by_role("button", name="Gespräch mit Anhängen als ZIP herunterladen")\
                            .click()
                        expect(page.get_by_role("alert")).to_contain_text(
                            "Chatanhang weicht vom gespeicherten Original ab")
                        assert not overflow_nodes(page), overflow_nodes(page)
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"chat_zip": "verified_originals",
                                          "tamper": "refused_without_download",
                                          "pending_attachment": "excluded",
                                          "model_usage": 0, "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
