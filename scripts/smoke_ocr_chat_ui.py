"""Real Edge/Tesseract/Ollama path from indexed scan PDF to local chat."""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from smoke_code_project_ui import wait_ui
from smoke_document_ocr_ui import write_scan
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, stop_test_server

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database
from jarvis.documents import DocumentStore


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-ocr-chat-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed"
        allowed.mkdir()
        pdf = allowed / "Scan.pdf"
        write_scan(pdf, "Orion")
        original = pdf.read_bytes()
        for number in (1, 2):
            (allowed / f"Notiz_{number}.md").write_text(
                f"Projekt Orion: lokaler Zusatzbeleg {number}.\n", encoding="utf-8")
        db = Database(root / "data" / "jarvis.db")
        documents = DocumentStore(db)
        folder = documents.add_folder(str(allowed), "local_only")
        counts = documents.index_folder(folder["id"])
        assert counts["ocr_pending"] == 1 and counts["indexed"] == 2
        scan_id = next(item["id"] for item in documents.documents()
                       if Path(item["path"]).name == "Scan.pdf")
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(db.path.parent), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct",
                           JARVIS_MAX_OUTPUT_TOKENS="96")
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
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        row = page.locator(".document-row").filter(has_text="Scan.pdf")
                        row.get_by_role("button", name="Scan-PDF lokal per OCR lesen").click()
                        expect(page.get_by_role("status").filter(
                            has_text="Scan-PDF lokal erkannt")).to_be_visible(timeout=120000)
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Gemeinsame Suche").fill("Orion")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        results = page.get_by_role("region", name="Suchergebnisse")
                        expect(results).to_contain_text("3 Dokumente")
                        results.get_by_role(
                            "button", name="PDF-Indextext im lokalen Chat verwenden").click()
                        expect(page.get_by_role("heading", name="Chat", exact=True)).to_be_visible()
                        selected_files = page.get_by_label("Datei für Chat").locator(
                            "xpath=ancestor::section[1]")
                        expect(selected_files).to_contain_text("1. Scan-indextext.txt")
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                            assert connection.execute("SELECT COUNT(*) FROM chat_attachments").fetchone()[0] == 0
                        page.get_by_label("Nachricht").fill(
                            "Welches Projekt steht in der Scan-Datei? Nenne die Seite.")
                        page.get_by_role("button", name="Senden", exact=True).click()
                        expect(page.locator(".message.assistant .usage")).to_contain_text(
                            "keine API-Gebühr", timeout=180000)
                        attachments = page.get_by_role("region", name="Dateianhänge im Gespräch")
                        expect(attachments).to_contain_text("Scan-indextext.txt")
                        with db.connect() as connection:
                            saved = connection.execute(
                                "SELECT text FROM chat_attachments WHERE filename='Scan-indextext.txt'"
                            ).fetchone()[0]
                        assert "=== Seite 1 ===" in saved
                        assert f"Dokument-ID: {scan_id}" in saved
                        assert "Projekt Orion Vertrag 2026" in saved
                        assert hashlib.sha256(original).hexdigest() in saved
                        assert str(allowed) not in saved
                        assert not overflow_nodes(page), overflow_nodes(page)

                        page.get_by_role("button", name="Suche", exact=True).click()
                        choices = results.get_by_role(
                            "checkbox", name=re.compile("für lokalen Chat auswählen"))
                        expect(choices).to_have_count(3)
                        for index in range(3):
                            choices.nth(index).check()
                        write_scan(pdf, "Neptun")
                        page.get_by_role("button", name="Mit 3 Dokumenten lokalen Chat vorbereiten")\
                            .click()
                        expect(page.get_by_role("alert")).to_contain_text(
                            "Originaldatei weicht vom Index ab")
                        expect(page.get_by_role("heading", name="Gemeinsame Suche"))\
                            .to_be_visible()
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM chats").fetchone()[0] == 1
                            assert connection.execute("SELECT COUNT(*) FROM chat_attachments").fetchone()[0] == 1
                        pdf.write_bytes(original)
                        page.get_by_role("button", name="Mit 3 Dokumenten lokalen Chat vorbereiten")\
                            .click()
                        expect(page.get_by_role("heading", name="Chat", exact=True)).to_be_visible()
                        expect(selected_files).to_contain_text("Scan-indextext.txt")
                        expect(selected_files).to_contain_text("Notiz_1.md")
                        expect(selected_files).to_contain_text("Notiz_2.md")
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 1
                        page.get_by_label("Nachricht").fill(
                            "Nenne den Projektnamen und die Seitenzahl aus dem Scan.")
                        page.get_by_role("button", name="Senden", exact=True).click()
                        expect(page.locator(".message.assistant .usage")).to_contain_text(
                            "keine API-Gebühr", timeout=180000)
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM chats").fetchone()[0] == 2
                            assert connection.execute("SELECT COUNT(*) FROM chat_attachments").fetchone()[0] == 4
                            assert connection.execute("SELECT COUNT(*) FROM model_usage WHERE provider='openrouter'").fetchone()[0] == 0
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"scan_pdf_chat": "verified_index_text",
                                          "three_sources": "atomic_stale_then_sent",
                                          "pre_send_model_calls": 0, "page_evidence": 1,
                                          "provider": "ollama", "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
