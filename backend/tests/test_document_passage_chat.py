"""One verified PDF passage can be handed to a local chat without the whole index."""

import io

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from test_document_index_text import text_pdf

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.documents import DocumentStore


def long_pdf(path):
    writer = PdfWriter()
    for number in range(1, 5):
        writer.append(io.BytesIO(text_pdf(f"Orion Seite {number} " + "Alpha " * 600)))
    with path.open("wb") as stream:
        writer.write(stream)


def sample(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    pdf = allowed / "Langer Bericht.pdf"
    long_pdf(pdf)
    note = allowed / "Notiz.md"
    note.write_text("Orion Notiz", encoding="utf-8")
    db = Database(tmp_path / "data" / "jarvis.db")
    documents = DocumentStore(db)
    folder = documents.add_folder(str(allowed), "local_only")
    assert documents.index_folder(folder["id"])["indexed"] == 2
    item = next(row for row in documents.documents() if row["path"] == str(pdf))
    note_item = next(row for row in documents.documents() if row["path"] == str(note))
    return db, documents, item, note_item, pdf


def test_pdf_passage_only_returns_verified_text_and_page(tmp_path):
    _, documents, item, note_item, _ = sample(tmp_path)
    hit = next(row for row in documents.search("Orion") if row["document_id"] == item["id"])
    payload = documents.verified_pdf_passage(item["id"], hit["passage_id"], item["sha256"])
    assert payload["document_id"] == item["id"]
    assert payload["passage_id"] == hit["passage_id"]
    assert payload["filename"] == "Langer Bericht.pdf"
    assert payload["page"] in range(1, 5)
    assert payload["sha256"] == item["sha256"]
    assert payload["text"].startswith("Orion Seite ")
    assert str(tmp_path) not in str(payload)
    _, full_text = documents.indexed_pdf_text(item["id"])
    assert len(full_text) > 8000
    with pytest.raises(LookupError, match="Fundstelle"):
        documents.verified_pdf_passage(item["id"], 999999, item["sha256"])
    with pytest.raises(ValueError, match="nicht mehr aktuell"):
        documents.verified_pdf_passage(item["id"], hit["passage_id"], "0" * 64)
    with pytest.raises(ValueError, match="nur für PDF"):
        documents.verified_pdf_passage(note_item["id"], hit["passage_id"],
                                       note_item["sha256"])


def test_pdf_passage_blocks_changed_excluded_and_revised_index(tmp_path, monkeypatch):
    _, documents, item, _, path = sample(tmp_path)
    hit = next(row for row in documents.search("Orion") if row["document_id"] == item["id"])
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Originaldatei weicht"):
        documents.verified_pdf_passage(item["id"], hit["passage_id"], item["sha256"])
    path.write_bytes(path.read_bytes()[:-1])
    documents.set_excluded(item["id"], True)
    with pytest.raises(LookupError, match="Indexiertes Dokument"):
        documents.verified_pdf_passage(item["id"], hit["passage_id"], item["sha256"])
    documents.set_excluded(item["id"], False)
    documents.index_document(item["id"])
    item = documents.get_document(item["id"])
    hit = next(row for row in documents.search("Orion") if row["document_id"] == item["id"])
    original = documents.original_bytes

    def revise_index(document_id):
        result = original(document_id)
        with documents.db.connect() as db:
            db.execute("UPDATE documents SET indexed_at=? WHERE id=?",
                       ("2099-01-01T00:00:00Z", document_id))
        return result

    monkeypatch.setattr(documents, "original_bytes", revise_index)
    with pytest.raises(ValueError, match="Index hat sich"):
        documents.verified_pdf_passage(item["id"], hit["passage_id"], item["sha256"])


def test_pdf_passage_route_validates_request_and_disables_cache(tmp_path):
    db, documents, item, _, path = sample(tmp_path)
    hit = next(row for row in documents.search("Orion") if row["document_id"] == item["id"])
    url = f"/documents/{item['id']}/passages/{hit['passage_id']}/chat-text"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=db.path.parent,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        assert client.get("/session").status_code == 200
        response = client.get(url, params={"expected_sha256": item["sha256"]})
        assert response.status_code == 200
        assert response.json()["page"] == hit["page"]
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert client.get(url, params={"expected_sha256": "bad"}).status_code == 422
        assert client.get(url, params={"expected_sha256": "0" * 64}).status_code == 409
        assert client.get(url.replace(str(hit["passage_id"]), "999999"),
                          params={"expected_sha256": item["sha256"]}).status_code == 404
        path.write_bytes(path.read_bytes() + b"\n")
        assert client.get(url, params={"expected_sha256": item["sha256"]}).status_code == 409
