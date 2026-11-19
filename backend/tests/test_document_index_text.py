"""The user can inspect only current, page-numbered PDF search text."""

import hashlib

from fastapi.testclient import TestClient
from PIL import Image

from jarvis import documents as document_module
from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore


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


def test_ocr_index_export_reassembles_overlap_without_local_path(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    path = root / "Scan.pdf"
    Image.new("RGB", (300, 100), "white").save(path, format="PDF")
    db = Database(tmp_path / "data" / "jarvis.db")
    documents = DocumentStore(db)
    folder = documents.add_folder(str(root), "local_only")
    assert documents.index_folder(folder["id"])["ocr_pending"] == 1
    item = documents.documents()[0]
    recognized = "Orion" * 300
    monkeypatch.setattr(document_module, "scan_pdf", lambda _data: [(1, recognized)])
    assert documents.ocr_document(item["id"])["outcome"] == "indexed"
    exported, text = documents.indexed_pdf_text(item["id"])
    assert exported["id"] == item["id"]
    assert "Originaldatei: Scan.pdf" in text
    assert f"Dokument-ID: {item['id']}" in text
    assert f"Original SHA-256: {hashlib.sha256(path.read_bytes()).hexdigest()}" in text
    assert "=== Seite 1 ===\n" + recognized in text
    assert str(root) not in text
    assert text.count("Orion") == 300


def test_text_pdf_download_and_stale_or_excluded_source_are_rejected(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    path = root / "Vertrag.pdf"
    path.write_bytes(text_pdf("Projekt Saturn unterschrieben"))
    (root / "Notiz.txt").write_text("Textdatei", encoding="utf-8")
    db = Database(tmp_path / "data" / "jarvis.db")
    documents = DocumentStore(db)
    folder = documents.add_folder(str(root), "local_only")
    assert documents.index_folder(folder["id"])["indexed"] == 2
    indexed = {item["path"]: item for item in documents.documents()}
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=db.path.parent,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        client.get("/session")
        url = f"/documents/{indexed[str(path)]['id']}/index-text"
        response = client.get(url)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert "attachment" in response.headers["content-disposition"]
        assert "no-store" == response.headers["cache-control"]
        assert "=== Seite 1 ===" in response.text
        assert "Projekt Saturn unterschrieben" in response.text
        assert str(root) not in response.text
        assert client.get(f"/documents/{indexed[str(root / 'Notiz.txt')]['id']}/index-text")\
            .status_code == 409
        path.write_bytes(text_pdf("Projekt Saturn geaendert"))
        assert client.get(url).status_code == 409
        path.write_bytes(text_pdf("Projekt Saturn unterschrieben"))
        documents.set_excluded(indexed[str(path)]["id"], True)
        assert client.get(url).status_code == 404
        assert client.get("/documents/missing/index-text").status_code == 404
