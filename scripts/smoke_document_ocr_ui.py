"""Read a real scanned PDF locally through Edge and Tesseract."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import expect, sync_playwright
from smoke_code_project_ui import wait_ui
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, stop_test_server

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database
from jarvis.documents import DocumentStore


def write_scan(path: Path, word: str) -> None:
    image = Image.new("RGB", (1400, 300), "white")
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 56)
    ImageDraw.Draw(image).text((70, 95), f"Projekt {word} Vertrag 2026", font=font,
                               fill="black")
    image.save(path, format="PDF")


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-ocr-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed"
        allowed.mkdir()
        pdf = allowed / "Vertrag.pdf"
        write_scan(pdf, "Orion")
        db = Database(root / "data" / "jarvis.db")
        documents = DocumentStore(db)
        folder = documents.add_folder(str(allowed), "local_only")
        assert documents.index_folder(folder["id"])["ocr_pending"] == 1
        item = documents.documents()[0]
        original_id = item["id"]
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
                        page = browser.new_page(viewport={"width": 390, "height": 844})
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        row = page.locator(".document-row").filter(has_text="Vertrag.pdf")
                        expect(row).to_contain_text("OCR ausstehend")
                        expect(row.get_by_role("button", name="Scan-PDF lokal per OCR lesen"))\
                            .to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)

                        write_scan(pdf, "Saturn")
                        row.get_by_role("button", name="Scan-PDF lokal per OCR lesen").click()
                        expect(page.get_by_role("status").filter(
                            has_text="Original geändert. Datei zuerst neu indexieren"))\
                            .to_be_visible()
                        assert documents.get_document(original_id) == item
                        row.get_by_role("button", name="Diese Datei neu indexieren").click()
                        expect(page.get_by_role("status").filter(has_text="OCR steht aus"))\
                            .to_be_visible()
                        row.get_by_role("button", name="Scan-PDF lokal per OCR lesen").click()
                        expect(page.get_by_role("status").filter(
                            has_text="Scan-PDF lokal erkannt"))\
                            .to_be_visible(timeout=120000)
                        expect(row).to_contain_text("Indexstand:")
                        page.get_by_label("Dokumente durchsuchen").fill("Saturn")
                        page.get_by_role("button", name="Suchen", exact=True).click()
                        expect(page.get_by_text("Projekt Saturn Vertrag 2026", exact=False))\
                            .to_be_visible()
                        expect(page.get_by_text("Vertrag.pdf · Seite 1", exact=False))\
                            .to_be_visible()
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Quelltyp der gemeinsamen Suche").select_option("document")
                        page.get_by_label("Gemeinsame Suche").fill(
                            "Was steht im Vertrag für das Projekt Saturn?")
                        page.get_by_role("button", name="Sinngemäß lokal suchen").click()
                        semantic = page.get_by_role("region", name="Sinngemäße Suchergebnisse")
                        expect(semantic.locator("article").filter(has_text="Vertrag.pdf"))\
                            .to_contain_text("Seite 1", timeout=120000)
                        assert documents.get_document(original_id)["status"] == "indexed"
                        assert not overflow_nodes(page), overflow_nodes(page)
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"ocr": "real_tesseract", "page": 1,
                                          "semantic_search": "local_ollama",
                                          "stale_original": "refused", "source_id": "preserved",
                                          "model_usage": 0, "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
