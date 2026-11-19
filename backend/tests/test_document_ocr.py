"""Explicit local OCR must preserve grants, source identity and index integrity."""

import hashlib
import io
import subprocess

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from pypdf import PdfWriter

from jarvis import documents as document_module
from jarvis import ocr as ocr_module
from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore, SourceChanged
from jarvis.ocr import MAX_OCR_PAGES, OcrUnavailable, scan_pdf


def scanned_pdf(path, text="Orion Beleg"):
    image = Image.new("RGB", (900, 220), "white")
    ImageDraw.Draw(image).text((40, 80), text, fill="black")
    image.save(path, format="PDF")


def sample(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    path = allowed / "Scan.pdf"
    scanned_pdf(path)
    db = Database(tmp_path / "data" / "jarvis.db")
    documents = DocumentStore(db)
    folder = documents.add_folder(str(allowed), "local_only")
    assert documents.index_folder(folder["id"])["ocr_pending"] == 1
    item = documents.documents()[0]
    assert item["status"] == "ocr_pending"
    return db, documents, item, path


def test_explicit_ocr_indexes_page_and_preserves_original(tmp_path, monkeypatch):
    _, documents, item, path = sample(tmp_path)
    original = path.read_bytes()
    calls = []

    def recognize(data):
        calls.append(data)
        return [(1, "Orion Vertrag unterschrieben")]

    monkeypatch.setattr(document_module, "scan_pdf", recognize)
    result = documents.ocr_document(item["id"])
    assert result["outcome"] == "indexed"
    assert result["document"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert calls == [original]
    assert path.read_bytes() == original
    hits = documents.search("unterschrieben")
    assert len(hits) == 1
    assert hits[0]["page"] == 1
    assert hits[0]["document_id"] == item["id"]
    assert hits[0]["source_status"] == "current"
    assert documents.index_folder(item["folder_id"])["unchanged"] == 1
    assert documents.search("unterschrieben")[0]["page"] == 1
    with pytest.raises(ValueError, match="ausstehender"):
        documents.ocr_document(item["id"])


def test_ocr_rejects_changed_and_excluded_original(tmp_path, monkeypatch):
    _, documents, item, path = sample(tmp_path)
    scanned_pdf(path, "andere Fassung")
    monkeypatch.setattr(document_module, "scan_pdf", lambda _data: [(1, "falsch")])
    with pytest.raises(SourceChanged, match="zuerst neu indexieren"):
        documents.ocr_document(item["id"])
    assert documents.get_document(item["id"]) == item
    assert documents.search("falsch") == []
    documents.set_excluded(item["id"], True)
    with pytest.raises(ValueError, match="Ausgeschlossenes"):
        documents.ocr_document(item["id"])


def test_ocr_rejects_mutation_during_recognition_without_partial_index(tmp_path, monkeypatch):
    _, documents, item, path = sample(tmp_path)

    def change_original(_data):
        scanned_pdf(path, "neuer Scan")
        return [(1, "Alte OCR darf nicht erscheinen")]

    monkeypatch.setattr(document_module, "scan_pdf", change_original)
    with pytest.raises(SourceChanged, match="während der OCR geändert"):
        documents.ocr_document(item["id"])
    assert documents.get_document(item["id"]) == item
    assert documents.search("Alte") == []


def test_ocr_page_limit_and_failed_recognition_leave_no_passages(tmp_path, monkeypatch):
    _, documents, item, _ = sample(tmp_path)
    monkeypatch.setattr(document_module, "scan_pdf", lambda _data: (_ for _ in ())
                        .throw(ValueError("OCR-Zeitlimit für eine PDF-Seite erreicht")))
    result = documents.ocr_document(item["id"])
    assert result["outcome"] == "error"
    assert result["document"]["error"] == "OCR-Zeitlimit für eine PDF-Seite erreicht"
    assert documents.search("Orion") == []

    image = Image.new("RGB", (100, 100), "white")
    output = io.BytesIO()
    image.save(output, format="PDF", save_all=True,
               append_images=[image] * MAX_OCR_PAGES)
    with pytest.raises(ValueError, match="höchstens 10 PDF-Seiten"):
        scan_pdf(output.getvalue(), binary="unused")

    large = PdfWriter()
    large.add_blank_page(width=5000, height=5000)
    oversized = io.BytesIO()
    large.write(oversized)
    with pytest.raises(ValueError, match="PDF-Seite ist für lokale OCR zu groß"):
        scan_pdf(oversized.getvalue(), binary="unused")


def test_ocr_helper_enforces_process_timeout(tmp_path, monkeypatch):
    path = tmp_path / "Scan.pdf"
    scanned_pdf(path)

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("tesseract", 20)

    monkeypatch.setattr(ocr_module.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="OCR-Zeitlimit"):
        scan_pdf(path.read_bytes(), binary="unused")


def test_missing_local_ocr_tool_keeps_pending_document_retryable(tmp_path, monkeypatch):
    _, documents, item, _ = sample(tmp_path)
    monkeypatch.setattr(document_module, "scan_pdf", lambda _data: (_ for _ in ())
                        .throw(OcrUnavailable("Lokales Tesseract fehlt")))
    with pytest.raises(OcrUnavailable, match="Tesseract fehlt"):
        documents.ocr_document(item["id"])
    assert documents.get_document(item["id"]) == item


def test_ocr_route_requires_csrf_and_returns_page_status(tmp_path, monkeypatch):
    db, _, item, _ = sample(tmp_path)
    monkeypatch.setattr(document_module, "scan_pdf", lambda _data: [(1, "OCR Beleg")])
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=db.path.parent,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        endpoint = f"/documents/{item['id']}/ocr"
        token = client.get("/session").json()["csrf"]
        assert client.post(endpoint).status_code == 403
        response = client.post(endpoint, headers={"x-jarvis-csrf": token})
        assert response.status_code == 200
        assert response.json()["outcome"] == "indexed"
        assert client.post(endpoint, headers={"x-jarvis-csrf": token}).status_code == 409
