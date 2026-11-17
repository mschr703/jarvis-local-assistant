"""DOCX ingestion stays bounded and tied to the approved original file."""

from pathlib import Path
from zipfile import ZipFile

import pytest

from jarvis import documents
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.unified_search import search as unified_search

NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def write_docx(path, body: str) -> None:
    xml = f'<w:document xmlns:w="{NS}"><w:body>{body}</w:body></w:document>'
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", xml)


def test_docx_paragraphs_tables_breaks_and_updates(tmp_path):
    root = tmp_path / "Freigabe"
    root.mkdir()
    file = root / "Mietübersicht.docx"
    write_docx(file, """
        <w:p><w:r><w:t>Mietvertrag für Zürich</w:t></w:r></w:p>
        <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Kaution</w:t></w:r>
        <w:r><w:tab/><w:t>800 Euro</w:t><w:br/><w:t>fällig</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Originalbeleg</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
    """)
    db = Database(tmp_path / "data" / "jarvis.db")
    docs = DocumentStore(db)
    folder = docs.add_folder(str(root), "local_only")
    assert docs.index_folder(folder["id"])["indexed"] == 1
    document = docs.documents()[0]
    assert document["status"] == "indexed"
    assert docs.search("Zürich")[0]["path"] == str(file.resolve())
    with db.connect() as connection:
        content = connection.execute("SELECT text FROM passages WHERE document_id=?",
                                     (document["id"],)).fetchone()[0]
    assert "Mietvertrag für Zürich\nKaution\t800 Euro\nfällig\nOriginalbeleg" in content
    hit = unified_search(db, "Mietübersicht")["results"][0]
    assert hit["source"] == str(file.resolve())
    assert hit["sha256"] == document["sha256"]
    assert hit["passage_id"]

    write_docx(file, "<w:p><w:r><w:t>Neue Wohnung in Basel</w:t></w:r></w:p>")
    assert docs.index_folder(folder["id"])["indexed"] == 1
    assert docs.search("Zürich") == []
    assert len(docs.search("Basel")) == 1
    assert docs.documents()[0]["sha256"] != document["sha256"]
    file.unlink()
    assert docs.index_folder(folder["id"])["removed"] == 1
    assert docs.search("Basel") == []


def test_bad_or_oversized_docx_is_visible_without_stopping_other_files(tmp_path):
    root = tmp_path / "Freigabe"
    root.mkdir()
    (root / "kaputt.docx").write_bytes(b"not a ZIP or an Office document")
    huge = root / "riesig.docx"
    with ZipFile(huge, "w") as archive:
        archive.writestr("word/document.xml", b"X" * (8 * 1024 * 1024 + 1))
    (root / "gute_notiz.txt").write_text("Prüfbarer Referenztext", encoding="utf-8")
    db = Database(tmp_path / "data" / "jarvis.db")
    docs = DocumentStore(db)
    folder = docs.add_folder(str(root), "local_only")
    counts = docs.index_folder(folder["id"])
    assert counts["indexed"] == 1 and counts["errors"] == 2
    assert len(docs.search("Referenztext")) == 1
    assert {item["status"] for item in docs.documents()} == {"indexed", "error"}
    failures = {Path(item["path"]).name: item["error"] for item in docs.documents()
                if item["status"] == "error"}
    assert failures["kaputt.docx"] == "Ungültiges oder verschlüsseltes DOCX"
    assert failures["riesig.docx"] == "DOCX-XML ist zu groß"
    assert unified_search(db, "kaputt")["results"] == []


def test_docx_rejects_dtd_even_with_utf16_encoding(tmp_path):
    root = tmp_path / "Freigabe"
    root.mkdir()
    xml = (f'<?xml version="1.0" encoding="utf-16"?>'
           f'<!DOCTYPE w:document [<!ENTITY probe "unerwünscht">]>'
           f'<w:document xmlns:w="{NS}"><w:body><w:p><w:r>'
           f'<w:t>&probe;</w:t></w:r></w:p></w:body></w:document>')
    with ZipFile(root / "entity.docx", "w") as archive:
        archive.writestr("word/document.xml", xml.encode("utf-16"))
    db = Database(tmp_path / "data" / "jarvis.db")
    docs = DocumentStore(db)
    folder = docs.add_folder(str(root), "local_only")
    assert docs.index_folder(folder["id"])["errors"] == 1
    assert docs.documents()[0]["status"] == "error"
    assert docs.search("unerwünscht") == []


def test_docx_entry_count_is_bounded(tmp_path, monkeypatch):
    file = tmp_path / "viele.docx"
    with ZipFile(file, "w") as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{NS}"><w:body/></w:document>')
        archive.writestr("extra-1.txt", "a")
        archive.writestr("extra-2.txt", "b")
    monkeypatch.setattr(documents, "MAX_DOCX_ENTRIES", 2)
    with pytest.raises(ValueError, match="zu viele ZIP-Einträge"):
        documents.extract_docx(file.read_bytes())
