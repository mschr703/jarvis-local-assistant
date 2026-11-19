"""Read a real scanned PDF locally through Edge and Tesseract."""

import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import expect, sync_playwright
from pypdf import PdfWriter
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


def text_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
         b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"),
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
                  f"startxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


def write_mixed(path: Path, scanned_page: Path) -> None:
    writer = PdfWriter()
    writer.append(io.BytesIO(text_pdf("Projekt Orion Textbeleg 2026")))
    writer.append(str(scanned_page))
    with path.open("wb") as stream:
        writer.write(stream)


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-ocr-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed"
        allowed.mkdir()
        pdf = allowed / "Vertrag.pdf"
        write_scan(pdf, "Orion")
        scanned_page = root / "image-page.pdf"
        write_scan(scanned_page, "Saturn")
        mixed = allowed / "Gemischt.pdf"
        write_mixed(mixed, scanned_page)
        db = Database(root / "data" / "jarvis.db")
        documents = DocumentStore(db)
        folder = documents.add_folder(str(allowed), "local_only")
        counts = documents.index_folder(folder["id"])
        assert counts["ocr_pending"] == 1 and counts["indexed"] == 1
        item = next(document for document in documents.documents()
                    if Path(document["path"]).name == "Vertrag.pdf")
        original_id = item["id"]
        mixed_item = next(document for document in documents.documents()
                          if Path(document["path"]).name == "Gemischt.pdf")
        mixed_id = mixed_item["id"]
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

                        mixed_row = page.locator(".document-row").filter(has_text="Gemischt.pdf")
                        expect(mixed_row).to_contain_text("Indexstand:")
                        mixed_row.get_by_role("button", name="PDF-Bildseiten lokal per OCR ergänzen")\
                            .click()
                        expect(page.get_by_role("status").filter(
                            has_text="PDF-Bildseiten lokal ergänzt"))\
                            .to_be_visible(timeout=120000)
                        with page.expect_download() as mixed_download:
                            mixed_row.get_by_role(
                                "button", name="Geprüften PDF-Indextext herunterladen").click()
                        mixed_text = Path(mixed_download.value.path()).read_text(encoding="utf-8")
                        assert "=== Seite 1 ===\nProjekt Orion Textbeleg 2026" in mixed_text
                        assert "=== Seite 2 ===" in mixed_text
                        assert "Projekt Saturn Vertrag 2026" in mixed_text
                        assert str(allowed) not in mixed_text
                        assert documents.search("Saturn")[0]["document_id"] == mixed_id
                        mixed_row.get_by_role("button", name="PDF-Bildseiten lokal per OCR ergänzen")\
                            .click()
                        expect(page.get_by_role("status").filter(
                            has_text="Index bleibt unverändert"))\
                            .to_be_visible(timeout=120000)
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
                        with page.expect_download() as file_download:
                            row.get_by_role("button", name="Geprüften PDF-Indextext herunterladen")\
                                .click()
                        downloaded = file_download.value
                        assert downloaded.suggested_filename == "Vertrag-indextext.txt"
                        exported = Path(downloaded.path()).read_text(encoding="utf-8")
                        assert "Projekt Saturn Vertrag 2026" in exported
                        assert "=== Seite 1 ===" in exported
                        assert hashlib.sha256(pdf.read_bytes()).hexdigest() in exported
                        assert str(allowed) not in exported
                        page.get_by_label("Dokumente durchsuchen").fill("Saturn")
                        page.get_by_role("button", name="Suchen", exact=True).click()
                        expect(page.locator(".source-result").filter(has_text=str(pdf))
                               .get_by_text("Projekt Saturn Vertrag 2026", exact=False))\
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
                        write_scan(pdf, "Neptun")
                        blocked = page.request.get(
                            f"http://127.0.0.1:5173/api/documents/{original_id}/index-text")
                        assert blocked.status == 409
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        row.get_by_role("button", name="Geprüften PDF-Indextext herunterladen")\
                            .click()
                        expect(page.get_by_role("status").filter(
                            has_text="Originaldatei weicht vom Index ab"))\
                            .to_be_visible()
                        assert documents.get_document(original_id)["status"] == "indexed"
                        assert not overflow_nodes(page), overflow_nodes(page)
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"ocr": "real_tesseract", "page": 1,
                                          "mixed_pdf": "text_and_image_pages",
                                          "semantic_search": "local_ollama",
                                          "stale_original": "refused", "index_text": "verified_download",
                                          "source_id": "preserved",
                                          "model_usage": 0, "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
