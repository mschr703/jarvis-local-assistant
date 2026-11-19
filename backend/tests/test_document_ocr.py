"""Explicit local OCR must preserve grants, source identity and index integrity."""

import hashlib
import io
import subprocess

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from pypdf import PdfWriter
from test_document_index_text import text_pdf

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
    assert documents.ocr_document(item["id"])["outcome"] == "unchanged"


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
        repeated = client.post(endpoint, headers={"x-jarvis-csrf": token})
        assert repeated.status_code == 200
        assert repeated.json()["outcome"] == "unchanged"


def mixed_pdf(path, *, second_image=False):
    image = Image.new("RGB", (900, 220), "white")
    ImageDraw.Draw(image).text((40, 80), "Saturn Bildbeleg", fill="black")
    scan = io.BytesIO()
    image.save(scan, format="PDF")
    writer = PdfWriter()
    writer.append(io.BytesIO(text_pdf("Orion Textbeleg")))
    writer.append(io.BytesIO(scan.getvalue()))
    if second_image:
        writer.append(io.BytesIO(scan.getvalue()))
    with path.open("wb") as stream:
        writer.write(stream)


def test_mixed_pdf_ocr_adds_only_image_page_and_keeps_text_page(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    path = root / "Gemischt.pdf"
    mixed_pdf(path)
    db = Database(tmp_path / "data" / "jarvis.db")
    documents = DocumentStore(db)
    folder = documents.add_folder(str(root), "local_only")
    assert documents.index_folder(folder["id"])["indexed"] == 1
    item = documents.documents()[0]
    assert documents.search("Orion")[0]["page"] == 1
    assert documents.search("Saturn") == []
    selected = []

    def recognize(_data, *, page_numbers):
        selected.append(page_numbers)
        return [(2, "Saturn Bildbeleg")]

    monkeypatch.setattr(document_module, "scan_pdf", recognize)
    assert documents.ocr_document(item["id"])["outcome"] == "indexed"
    assert selected == [[2]]
    assert documents.search("Orion")[0]["page"] == 1
    assert documents.search("Saturn")[0]["page"] == 2
    _, exported = documents.indexed_pdf_text(item["id"])
    assert "=== Seite 1 ===\nOrion Textbeleg" in exported
    assert "=== Seite 2 ===\nSaturn Bildbeleg" in exported
    assert documents.ocr_document(item["id"])["outcome"] == "unchanged"


def test_mixed_pdf_ocr_error_and_source_change_preserve_text_index(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    path = root / "Gemischt.pdf"
    mixed_pdf(path)
    db = Database(tmp_path / "data" / "jarvis.db")
    documents = DocumentStore(db)
    folder = documents.add_folder(str(root), "local_only")
    documents.index_folder(folder["id"])
    item = documents.documents()[0]
    monkeypatch.setattr(document_module, "scan_pdf", lambda *_args, **_kwargs: (_ for _ in ())
                        .throw(ValueError("OCR-Zeitlimit für eine PDF-Seite erreicht")))
    with pytest.raises(ValueError, match="OCR-Zeitlimit"):
        documents.ocr_document(item["id"])
    assert documents.get_document(item["id"]) == item
    assert documents.search("Orion")[0]["page"] == 1
    monkeypatch.setattr(document_module, "scan_pdf", lambda *_args, **_kwargs: (_ for _ in ())
                        .throw(OcrUnavailable("Tesseract fehlt")))
    with pytest.raises(OcrUnavailable, match="Tesseract fehlt"):
        documents.ocr_document(item["id"])
    assert documents.get_document(item["id"]) == item

    def change_during_ocr(_data, *, page_numbers):
        assert page_numbers == [2]
        path.write_bytes(path.read_bytes() + b"\n")
        return [(2, "Saturn Bildbeleg")]

    monkeypatch.setattr(document_module, "scan_pdf", change_during_ocr)
    with pytest.raises(SourceChanged, match="während der OCR geändert"):
        documents.ocr_document(item["id"])
    assert documents.get_document(item["id"]) == item
    assert documents.search("Orion")[0]["source_status"] == "needs_reindex"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(SourceChanged, match="zuerst neu indexieren"):
        documents.ocr_document(item["id"])
    assert documents.search("Saturn") == []


def test_text_pdf_ocr_is_noop_and_selected_page_limit_is_bounded(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    path = root / "Text.pdf"
    path.write_bytes(text_pdf("Orion Textbeleg"))
    documents = DocumentStore(Database(tmp_path / "data" / "jarvis.db"))
    folder = documents.add_folder(str(root), "local_only")
    documents.index_folder(folder["id"])
    item = documents.documents()[0]
    assert documents.ocr_document(item["id"])["outcome"] == "unchanged"
    assert documents.get_document(item["id"]) == item

    writer = PdfWriter()
    for _ in range(MAX_OCR_PAGES + 2):
        writer.add_blank_page(612, 792)
    output = io.BytesIO()
    writer.write(output)
    with pytest.raises(ValueError, match="höchstens 10 PDF-Seiten"):
        scan_pdf(output.getvalue(), binary="unused", page_numbers=list(range(1, 12)))
    with pytest.raises(ValueError, match="Ungültige PDF-Seitenauswahl"):
        scan_pdf(output.getvalue(), binary="unused", page_numbers=[1, 1])


def test_selected_ocr_pages_keep_previous_image_page_and_reject_text_page(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    path = root / "Gemischt.pdf"
    mixed_pdf(path, second_image=True)
    documents = DocumentStore(Database(tmp_path / "data" / "jarvis.db"))
    folder = documents.add_folder(str(root), "local_only")
    documents.index_folder(folder["id"])
    item = documents.documents()[0]
    preview = documents.ocr_page_preview(item["id"])
    assert preview["sha256"] == item["sha256"]
    assert preview["pages_total"] == 3
    assert preview["pages"] == [
        {"page": 2, "already_indexed": False},
        {"page": 3, "already_indexed": False},
    ]
    selected = []

    def recognize(_data, *, page_numbers):
        selected.append(page_numbers)
        return [(page_numbers[0], f"Bildseite {page_numbers[0]}")]

    monkeypatch.setattr(document_module, "scan_pdf", recognize)
    result = documents.ocr_document(item["id"], page_numbers=[2],
                                    expected_sha256=item["sha256"])
    assert result["outcome"] == "indexed"
    assert selected == [[2]]
    assert documents.ocr_page_preview(item["id"])["pages"] == [
        {"page": 2, "already_indexed": True},
        {"page": 3, "already_indexed": False},
    ]
    for invalid in ([1], [2, 2], [4]):
        with pytest.raises(ValueError, match="Seiten ohne Textschicht"):
            documents.ocr_document(item["id"], page_numbers=invalid,
                                   expected_sha256=item["sha256"])
    assert documents.search("Bildseite")[0]["page"] == 2
    monkeypatch.setattr(document_module, "scan_pdf", lambda *_args, **_kwargs: (_ for _ in ())
                        .throw(ValueError("OCR-Zeitlimit für eine PDF-Seite erreicht")))
    with pytest.raises(ValueError, match="OCR-Zeitlimit"):
        documents.ocr_document(item["id"], page_numbers=[3],
                               expected_sha256=item["sha256"])
    assert documents.search("Bildseite")[0]["page"] == 2
    monkeypatch.setattr(document_module, "scan_pdf", recognize)
    assert documents.ocr_document(item["id"], page_numbers=[3],
                                  expected_sha256=item["sha256"])["outcome"] == "indexed"
    assert selected == [[2], [3]]
    assert {hit["page"] for hit in documents.search("Bildseite")} == {2, 3}
    assert documents.search("Orion")[0]["page"] == 1
    _, exported = documents.indexed_pdf_text(item["id"])
    assert "=== Seite 2 ===\nBildseite 2" in exported
    assert "=== Seite 3 ===\nBildseite 3" in exported


def test_ocr_preview_route_and_selection_hash_block_stale_pdf(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    path = root / "Gemischt.pdf"
    mixed_pdf(path)
    note = root / "Notiz.md"
    note.write_text("Nur Text", encoding="utf-8")
    db = Database(tmp_path / "data" / "jarvis.db")
    documents = DocumentStore(db)
    folder = documents.add_folder(str(root), "local_only")
    documents.index_folder(folder["id"])
    item = next(entry for entry in documents.documents()
                if entry["path"] == str(path))
    note_id = next(entry["id"] for entry in documents.documents()
                   if entry["path"] == str(note))
    monkeypatch.setattr(document_module, "scan_pdf", lambda _data, *, page_numbers:
                        [(page_numbers[0], "Saturn Bildbeleg")])
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=db.path.parent,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        endpoint = f"/documents/{item['id']}/ocr"
        preview_url = f"/documents/{item['id']}/ocr-pages"
        token = client.get("/session").json()["csrf"]
        preview = client.get(preview_url)
        assert preview.status_code == 200
        assert preview.json()["pages"] == [{"page": 2, "already_indexed": False}]
        payload = {"pages": [2], "expected_sha256": item["sha256"]}
        assert client.post(endpoint, json=payload).status_code == 403
        assert client.post(endpoint, json={**payload, "pages": list(range(11))},
                           headers={"x-jarvis-csrf": token}).status_code == 422
        assert client.post(endpoint, json={**payload, "expected_sha256": "0" * 64},
                           headers={"x-jarvis-csrf": token}).status_code == 409
        assert client.post(endpoint, json=payload,
                           headers={"x-jarvis-csrf": token}).json()["outcome"] == "indexed"
        path.write_bytes(path.read_bytes() + b"\n")
        assert client.get(preview_url).status_code == 409
        assert client.post(endpoint, json=payload,
                           headers={"x-jarvis-csrf": token}).status_code == 409
        assert client.get(f"/documents/{note_id}/ocr-pages").status_code == 409
        assert documents.index_document(item["id"])["outcome"] == "indexed"
        assert client.post(endpoint, json=payload,
                           headers={"x-jarvis-csrf": token}).status_code == 409
        documents.set_excluded(item["id"], True)
        assert client.get(preview_url).status_code == 404
        assert client.get("/documents/missing/ocr-pages").status_code == 404
