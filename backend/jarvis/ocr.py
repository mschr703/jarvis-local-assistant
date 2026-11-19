"""Bounded, explicitly requested local OCR for scanned PDF files."""

import io
import math
import shutil
import subprocess
from pathlib import Path

import pypdfium2 as pdfium

MAX_OCR_PAGES = 10
MAX_OCR_PIXELS_PER_PAGE = 4_000_000
MAX_OCR_CHARS = 200_000
OCR_SCALE = 2  # 144 dpi for ordinary 72-point PDF pages.
OCR_PAGE_TIMEOUT_SECONDS = 20


class OcrUnavailable(ValueError):
    """Local OCR engine or required language data are not ready."""


def tesseract_binary() -> str:
    found = shutil.which("tesseract")
    if found:
        return found
    common = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
    if common.is_file():
        return str(common)
    raise OcrUnavailable("Lokales Tesseract fehlt. OCR-Installation in README.md prüfen.")


def scan_pdf(data: bytes, *, binary: str | None = None) -> list[tuple[int, str]]:
    """Return page-numbered OCR text; never send a source path to the OCR process."""
    executable = binary or tesseract_binary()
    try:
        document = pdfium.PdfDocument(data)
    except Exception as exc:
        raise ValueError("PDF kann für OCR nicht geöffnet werden") from exc
    try:
        if len(document) == 0 or len(document) > MAX_OCR_PAGES:
            raise ValueError(f"OCR unterstützt höchstens {MAX_OCR_PAGES} PDF-Seiten")
        page_sizes = []
        for number in range(len(document)):
            page = document[number]
            try:
                width, height = page.get_size()
                if not math.isfinite(width) or not math.isfinite(height):
                    raise ValueError("PDF-Seite ist für lokale OCR zu groß")
                pixels = math.ceil(width * OCR_SCALE) * math.ceil(height * OCR_SCALE)
                if pixels <= 0 or pixels > MAX_OCR_PIXELS_PER_PAGE:
                    raise ValueError("PDF-Seite ist für lokale OCR zu groß")
                page_sizes.append((width, height))
            finally:
                page.close()
        recognized: list[tuple[int, str]] = []
        total = 0
        for number in range(len(page_sizes)):
            page = document[number]
            try:
                bitmap = page.render(scale=OCR_SCALE)
                try:
                    png = io.BytesIO()
                    bitmap.to_pil().save(png, format="PNG")
                finally:
                    bitmap.close()
            finally:
                page.close()
            try:
                result = subprocess.run(
                    [executable, "stdin", "stdout", "-l", "deu+eng"],
                    input=png.getvalue(), capture_output=True,
                    timeout=OCR_PAGE_TIMEOUT_SECONDS, check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ValueError("OCR-Zeitlimit für eine PDF-Seite erreicht") from exc
            except OSError as exc:
                raise OcrUnavailable("Lokales Tesseract konnte nicht gestartet werden") from exc
            if result.returncode != 0:
                raise OcrUnavailable("OCR fehlgeschlagen. Tesseract und Sprachdaten deu/eng prüfen.")
            text = result.stdout.decode("utf-8", "replace").strip()
            total += len(text)
            if total > MAX_OCR_CHARS:
                raise ValueError("OCR-Text überschreitet die Längengrenze")
            recognized.append((number + 1, text))
        return recognized
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("PDF-Seite konnte für OCR nicht gelesen werden") from exc
    finally:
        document.close()
