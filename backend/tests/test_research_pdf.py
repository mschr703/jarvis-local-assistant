"""Öffentliche Text-PDFs nutzen denselben Netzschutz und begrenzte Extraktion."""

import io
import socket
import subprocess
from email.message import Message

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.desktop import extract_public_pdf, fetch_public_document
from jarvis.provider import Completion
from jarvis.research import ResearchStore, extract_source_claims, read_source, validate_claims


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


def pdf_connection(monkeypatch, payload: bytes, content_type: str = "application/pdf"):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
    ])
    addresses = []

    class FakeConnection:
        def __init__(self, host, port, address):
            addresses.append((host, port, address))

        def request(self, *_args, **_kwargs):
            pass

        def getresponse(self):
            class Response:
                status = 200
                headers = Message()

                @staticmethod
                def getheader(_name, default=None):
                    return default

                @staticmethod
                def read(_limit):
                    return payload

            Response.headers["Content-Type"] = content_type
            return Response()

        def close(self):
            pass

    monkeypatch.setattr("jarvis.desktop.PinnedHTTPSConnection", FakeConnection)
    return addresses


def test_pdf_source_uses_checked_ip_real_worker_and_quote_validation(monkeypatch):
    quote = "Europa Clipper untersucht den Jupitermond Europa und sammelt Daten ueber seine eisige Oberflaeche. " * 3
    payload = text_pdf(quote)
    assert quote.strip() in (PdfReader(io.BytesIO(payload)).pages[0].extract_text() or "")
    addresses = pdf_connection(monkeypatch, payload)
    with pytest.raises(ValueError, match="unterstützten Text"):
        fetch_public_document("https://example.com/report.pdf")
    source = read_source("https://example.com/report.pdf", "Missionsbericht")
    assert addresses == [("example.com", 443, "93.184.215.14")] * 2
    assert source["media_type"] == "pdf"
    assert source["title"] == "Missionsbericht (PDF · Seiten 1–1 von 1)"
    assert "Jupitermond Europa" in source["text"]
    claims = extract_source_claims([{"id": 1, **source}], "Was untersucht Europa Clipper?")
    assert claims and claims[0]["evidence"][0]["quote"] in source["text"]
    accepted, _, _ = validate_claims({"claims": claims}, [{"id": 1, **source}])
    assert len(accepted) == 1


@pytest.mark.parametrize("payload,error", [
    (b"no pdf", "PDF-Signatur"),
    (b"%PDF-1.4\nkaputt", "beschädigt"),
    (b"%PDF-" + b"a" * 2_000_001, "zu groß"),
], ids=["signature", "damaged", "oversized"])
def test_pdf_signature_damage_and_size(monkeypatch, payload, error):
    pdf_connection(monkeypatch, payload)
    with pytest.raises(ValueError, match=error):
        read_source("https://example.com/report.pdf", "Bericht")


def test_pdf_requires_declared_media_type(monkeypatch):
    pdf_connection(monkeypatch, text_pdf("Europa Clipper " * 25), "application/octet-stream")
    with pytest.raises(ValueError, match="unterstützten Text"):
        read_source("https://example.com/report.pdf", "Bericht")


def test_pdf_only_reads_first_twelve_pages():
    writer = PdfWriter()
    writer.append(io.BytesIO(text_pdf("Europa Clipper untersucht den Mond Europa. " * 6)))
    for _ in range(12):
        writer.add_blank_page(612, 792)
    many = io.BytesIO()
    writer.write(many)
    result = extract_public_pdf(many.getvalue())
    assert result["pages_total"] == 13
    assert result["pages_read"] == 12
    assert "Seite 1" in result["text"]
    assert "Seite 13" not in result["text"]


def test_compressed_pdf_stream_is_bounded_before_text_extraction():
    writer = PdfWriter()
    writer.append(io.BytesIO(text_pdf("Europa Clipper " * 20)))
    page = writer.pages[0]
    stream = DecodedStreamObject()
    stream.set_data(b" " * 8_100_000)
    page[NameObject("/Contents")] = writer._add_object(stream.flate_encode())
    output = io.BytesIO()
    writer.write(output)
    assert len(output.getvalue()) < 2_000_000
    assert len(PdfReader(io.BytesIO(output.getvalue())).pages[0].get_contents().get_data()) == 8_100_000
    with pytest.raises(ValueError, match="Entpackungs- oder Parserlimit"):
        extract_public_pdf(output.getvalue())


def test_pdf_encryption_page_limit_and_extraction_timeout(monkeypatch):
    writer = PdfWriter()
    writer.add_blank_page(612, 792)
    writer.encrypt("secret")
    encrypted = io.BytesIO()
    writer.write(encrypted)
    with pytest.raises(ValueError, match="Verschlüsselte"):
        extract_public_pdf(encrypted.getvalue())
    many = PdfWriter()
    for _ in range(51):
        many.add_blank_page(612, 792)
    too_many = io.BytesIO()
    many.write(too_many)
    with pytest.raises(ValueError, match="50 Seiten"):
        extract_public_pdf(too_many.getvalue())
    blank = PdfWriter()
    blank.add_blank_page(612, 792)
    no_text = io.BytesIO()
    blank.write(no_text)
    with pytest.raises(ValueError, match="OCR ausstehend"):
        extract_public_pdf(no_text.getvalue())

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("pdf", 8)

    monkeypatch.setattr("jarvis.desktop.subprocess.run", timeout)
    with pytest.raises(ValueError, match="8 Sekunden"):
        extract_public_pdf(text_pdf("Ein Text " * 30))


def test_pdf_research_api_persists_pages_quotes_and_backup(tmp_path, monkeypatch):
    pdf_connection(monkeypatch, text_pdf(
        "Europa Clipper untersucht den Jupitermond Europa und seine eisige Oberflaeche. " * 4))
    monkeypatch.setattr("jarvis.app.search_public_web", lambda _query: (
        [{"title": "Missionsbericht", "url": "https://example.com/report.pdf"}],
        "DuckDuckGo HTML"))

    async def generate(_db, _config, _purpose, _messages):
        return Completion("Die Mission untersucht Europa.", 40, 15, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    root = tmp_path / "data"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        response = client.post("/research", headers=headers,
                               json={"question": "Was untersucht Europa Clipper?"})
        assert response.status_code == 200, response.text
        report = response.json()
        source = report["sources"][0]
        assert source["media_type"] == "pdf"
        assert "PDF · Seiten 1–1 von 1" in source["title"]
        assert report["claims"][0]["evidence"][0]["quote"] in source["text"]
        assert client.get(f"/research/{report['id']}").json() == report
    archive = tmp_path / "backup.zip"
    create_backup(root, archive)
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    assert ResearchStore(Database(restored / "jarvis.db")).get(report["id"]) == report


def test_pdf_result_redirect_to_private_address_is_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
    ])
    connections = []

    class RedirectConnection:
        def __init__(self, *_args):
            connections.append(1)

        def request(self, *_args, **_kwargs):
            pass

        def getresponse(self):
            class Response:
                status = 302

                @staticmethod
                def getheader(_name, _default=None):
                    return "http://127.0.0.1/secret.pdf"

            return Response()

        def close(self):
            pass

    monkeypatch.setattr("jarvis.desktop.PinnedHTTPSConnection", RedirectConnection)
    with pytest.raises(ValueError, match="Private"):
        read_source("https://example.com/report.pdf", "Bericht")
    assert len(connections) == 1


def test_direct_urls_skip_search_and_preserve_html_pdf_evidence(tmp_path, monkeypatch):
    pdf = text_pdf("Europa Clipper untersucht die eisige Oberflaeche von Europa im Jahr 2024. " * 5)
    html = ('<meta property="article:published_time" content="2025-06-14">'
            "<title>Forschungsseite</title><main><p>" +
            "Europa Clipper erforscht den Jupitermond Europa und untersucht seine Oberflaeche im Jahr 2025. " * 5 +
            "</p></main>").encode()
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
    ])
    requested = []

    class FakeConnection:
        def __init__(self, host, port, address):
            assert (host, port, address) == ("example.com", 443, "93.184.215.14")

        def request(self, _method, path, **_kwargs):
            self.path = path
            requested.append(path)

        def getresponse(self):
            payload = pdf if self.path == "/report.pdf" else html
            media_type = "application/pdf" if self.path == "/report.pdf" else "text/html"
            redirected = self.path == "/start"

            class Response:
                status = 302 if redirected else 200
                headers = Message()

                @staticmethod
                def getheader(_name, default=None):
                    return "/overview" if redirected else default

                @staticmethod
                def read(_limit):
                    return payload

            Response.headers["Content-Type"] = media_type
            return Response()

        def close(self):
            pass

    monkeypatch.setattr("jarvis.desktop.PinnedHTTPSConnection", FakeConnection)
    monkeypatch.setattr("jarvis.app.search_public_web", lambda _query: pytest.fail("Websuche aufgerufen"))

    async def generate(_db, _config, _purpose, _messages):
        return Completion("Beide Quellen behandeln Europa.", 40, 15, 0, "local_no_api_charge")

    monkeypatch.setattr("jarvis.app.generate_text", generate)
    root = tmp_path / "data"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=root, JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        payload = {"question": "Was untersucht Europa Clipper?", "search_query": "wird ignoriert",
                   "source_urls": ["https://example.com/report.pdf", "https://example.com/overview"]}
        response = client.post("/research", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["search_provider"] == "Direkte URLs" and report["search_query"] == ""
        assert [source["media_type"] for source in report["sources"]] == ["pdf", "web"]
        assert [source["published_at"] for source in report["sources"]] == [None, "2025-06-14"]
        assert [source["url"] for source in report["sources"]] == payload["source_urls"]
        assert len(report["claims"]) == 2
        assert report["comparison"] is not None
        assert [entry["media_type"] for entry in report["comparison"]["entries"]] == ["pdf", "web"]
        assert [entry["published_at"] for entry in report["comparison"]["entries"]] == [None, "2025-06-14"]
        assert report["comparison"]["first_only_numbers"] == ["2024"]
        assert report["comparison"]["second_only_numbers"] == ["2025"]
        assert all(entry["quote"] in report["sources"][index]["text"]
                   for index, entry in enumerate(report["comparison"]["entries"]))
        assert all(claim["evidence"][0]["quote"] in report["sources"][index]["text"]
                   for index, claim in enumerate(report["claims"]))
        assert client.get(f"/research/{report['id']}").json() == report
        assert client.post("/research", headers=headers,
                           json={**payload, "source_urls": payload["source_urls"] * 2}).status_code == 422
        for invalid in ("file:///secret", "http://127.0.0.1/private", "https://example.com/#frag"):
            failed = client.post("/research", headers=headers,
                                 json={**payload, "source_urls": [invalid]})
            assert failed.status_code == 502 and "Direkte Quelle 1" in failed.json()["detail"]
        partial = client.post("/research", headers=headers,
                              json={**payload, "source_urls": ["http://127.0.0.1/private",
                                                            "https://example.com/overview"]})
        assert partial.status_code == 200
        assert len(partial.json()["sources"]) == 1 and partial.json()["errors"]
        redirected = client.post("/research", headers=headers,
                                 json={**payload, "source_urls": ["https://example.com/start"]})
        assert redirected.status_code == 200
        assert redirected.json()["sources"][0]["url"] == "https://example.com/overview"
    assert requested == ["/report.pdf", "/overview", "/overview", "/start", "/overview"]
    archive = tmp_path / "backup.zip"
    create_backup(root, archive)
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    assert ResearchStore(Database(restored / "jarvis.db")).get(report["id"]) == report
