"""Direct chat file selection keeps the original and its message linkage local."""

import base64
from collections.abc import AsyncIterator
from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.provider import Chunk, OllamaAdapter, ProviderAdapter


class CaptureProvider(ProviderAdapter):
    def __init__(self):
        self.calls: list[list[dict]] = []

    async def stream(self, messages: list[dict]) -> AsyncIterator[Chunk]:
        self.calls.append(messages)
        yield Chunk("text", text="Der Beleg nennt München.")
        yield Chunk("usage", input_tokens=40, output_tokens=8, cost_usd=0,
                    cost_kind="local_no_api_charge")


def token(client: TestClient) -> dict:
    return {"x-jarvis-csrf": client.get("/session").json()["csrf"]}


def upload(client: TestClient, chat_id: str, filename: str, data: bytes,
           headers: dict) -> object:
    return client.post(f"/chats/{chat_id}/attachments", params={"filename": filename},
                       content=data,
                       headers={**headers, "Content-Type": "application/octet-stream"})


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


def image_bytes(size=(256, 256), *, format="PNG", exif=False) -> bytes:
    image = Image.new("RGB", size, "red")
    output = BytesIO()
    options = {}
    if exif:
        metadata = image.getexif()
        metadata[270] = "PRIVATE-IMAGE-METADATA"
        options["exif"] = metadata
    image.save(output, format=format, **options)
    return output.getvalue()


def test_chat_attachment_message_reload_context_backup_and_delete(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    provider = CaptureProvider()
    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: provider)
    original = "Vertrag: Kaution für München beträgt 800 Euro.\n".encode()
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=data_dir,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = token(client)
        chat = client.post("/chats", headers=headers).json()["id"]
        assert upload(client, chat, "Vertrag.txt", original, {}).status_code == 403
        attached = upload(client, chat, "Vertrag.txt", original, headers)
        assert attached.status_code == 200
        item = attached.json()
        assert item["filename"] == "Vertrag.txt" and item["size"] == len(original)
        assert len(item["sha256"]) == 64
        second_chat = client.post("/chats", headers=headers).json()["id"]
        assert client.get(f"/chats/{second_chat}/attachments/{item['id']}").status_code == 404
        assert client.post(f"/chats/{second_chat}/stream", headers=headers,
                           json={"content": "Fremd", "attachment_id": item["id"]}).status_code == 422
        response = client.post(f"/chats/{chat}/stream", headers=headers,
                               json={"content": "Was steht zur Kaution?",
                                     "attachment_id": item["id"]})
        assert response.status_code == 200 and "Der Beleg nennt München" in response.text
        assert "Kaution für München" in provider.calls[0][-1]["content"]
        assert "Vertrag.txt" in provider.calls[0][-1]["content"]
        loaded = client.get(f"/chats/{chat}").json()
        sent = loaded["messages"][0]
        assert sent["content"] == "Was steht zur Kaution?"
        assert sent["attachments"][0]["id"] == item["id"]
        assert sent["attachments"][0]["sha256"] == item["sha256"]
        assert "text" not in sent["attachments"][0]
        download = client.get(f"/chats/{chat}/attachments/{item['id']}")
        assert download.content == original
        assert download.headers["content-disposition"].endswith("Vertrag.txt")
        assert download.headers["cache-control"] == "no-store"
        assert download.headers["x-content-type-options"] == "nosniff"
        assert client.post(f"/chats/{chat}/stream", headers=headers,
                           json={"content": "Noch einmal", "attachment_id": item["id"]}).status_code == 422
        assert client.delete(f"/chats/{chat}/attachments/{item['id']}",
                             headers=headers).status_code == 404
        assert client.post(f"/chats/{chat}/stream", headers=headers,
                           json={"content": "Und die Stadt?"}).status_code == 200
        assert "Kaution für München" in " ".join(msg["content"] for msg in provider.calls[1])

    archive = tmp_path / "backup.zip"
    assert create_backup(data_dir, archive)["schema"] == 54
    restored = tmp_path / "restored"
    assert restore_backup(archive, restored)["schema"] == 54
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=restored,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = token(client)
        assert client.get(f"/chats/{chat}").json()["messages"][0]["attachments"][0]["id"] == item["id"]
        assert client.get(f"/chats/{chat}/attachments/{item['id']}").content == original
        assert client.delete(f"/chats/{chat}", headers=headers).status_code == 200
        assert client.get(f"/chats/{chat}/attachments/{item['id']}").status_code == 404
        assert not list((restored / "chat-attachments").iterdir())


def test_multiple_attachments_keep_upload_order_and_survive_backup(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    provider = CaptureProvider()
    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: provider)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=data_dir,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = token(client)
        chat = client.post("/chats", headers=headers).json()["id"]
        selected = [upload(client, chat, name, raw, headers).json()
                    for name, raw in (("A.txt", b"Alpha one"),
                                      ("B.md", b"Beta two"),
                                      ("C.txt", b"Gamma three"))]
        # The server uses upload order even if API callers present the IDs differently.
        response = client.post(f"/chats/{chat}/stream", headers=headers,
                               json={"content": "Vergleiche die Quellen",
                                     "attachment_ids": [item["id"] for item in reversed(selected)]})
        assert response.status_code == 200, response.text
        content = provider.calls[0][-1]["content"]
        assert content.index("A.txt") < content.index("B.md") < content.index("C.txt")
        saved = client.get(f"/chats/{chat}").json()["messages"][0]
        assert [item["id"] for item in saved["attachments"]] == [item["id"] for item in selected]
        assert client.post(f"/chats/{chat}/stream", headers=headers,
                           json={"content": "Was war in B?"}).status_code == 200
        assert "Beta two" in " ".join(item["content"] for item in provider.calls[1])
    archive = tmp_path / "multi-backup.zip"
    create_backup(data_dir, archive)
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=restored,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = token(client)
        restored_items = client.get(f"/chats/{chat}").json()["messages"][0]["attachments"]
        assert [item["filename"] for item in restored_items] == ["A.txt", "B.md", "C.txt"]
        assert client.delete(f"/chats/{chat}", headers=headers).status_code == 200
        assert not list((restored / "chat-attachments").iterdir())


def test_multiple_attachments_reject_bad_selection_without_partial_link(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    provider = CaptureProvider()
    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: provider)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=data_dir,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = token(client)
        chat = client.post("/chats", headers=headers).json()["id"]
        other = client.post("/chats", headers=headers).json()["id"]
        first = upload(client, chat, "A.txt", b"Alpha", headers).json()["id"]
        second = upload(client, chat, "B.txt", b"Beta", headers).json()["id"]
        foreign = upload(client, other, "F.txt", b"Foreign", headers).json()["id"]
        for ids in ([first, first], [first, foreign], [first, "missing"],
                    [first, second, foreign, "fourth"]):
            assert client.post(f"/chats/{chat}/stream", headers=headers,
                               json={"content": "Prüfen", "attachment_ids": ids}).status_code == 422
        assert client.post(f"/chats/{chat}/stream", headers=headers,
                           json={"content": "Prüfen", "attachment_id": first,
                                 "attachment_ids": [second]}).status_code == 422
        db = Database(data_dir / "jarvis.db")
        with pytest.raises(ValueError, match="Chatanhang nicht gefunden"):
            db.add_message(chat, "user", "Atomar", attachment_ids=[first, foreign])
        assert db.list_messages(chat) == []
        assert client.post(f"/chats/{chat}/stream", headers=headers,
                           json={"content": "Jetzt", "attachment_ids": [first, second]}).status_code == 200
        assert len(client.get(f"/chats/{chat}").json()["messages"][0]["attachments"]) == 2


def test_image_and_text_share_one_local_vision_call(tmp_path, monkeypatch):
    provider = CaptureProvider()
    models = []

    def make(settings):
        models.append(settings.ollama_model)
        return provider

    monkeypatch.setattr("jarvis.app.make_provider", make)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path / "data",
                                        JARVIS_OLLAMA_MODEL="text-fixture",
                                        JARVIS_OLLAMA_VISION_MODEL="vision-fixture"))) as client:
        headers = token(client)
        chat = client.post("/chats", headers=headers).json()["id"]
        text_item = upload(client, chat, "Notiz.txt", b"Das Schild ist rot.", headers).json()
        image_item = upload(client, chat, "Rot.png", image_bytes(), headers).json()
        response = client.post(f"/chats/{chat}/stream", headers=headers,
                               json={"content": "Was zeigen Bild und Notiz?",
                                     "attachment_ids": [text_item["id"], image_item["id"]]})
        assert response.status_code == 200, response.text
        assert models == ["vision-fixture"]
        assert "Schild ist rot" in provider.calls[0][-1]["content"]
        assert len(provider.calls[0][-1]["images"]) == 1
        assert [item["filename"] for item in client.get(f"/chats/{chat}").json()["messages"][0]["attachments"]] == ["Notiz.txt", "Rot.png"]
        another = upload(client, chat, "Blau.png", image_bytes(), headers).json()
        third = upload(client, chat, "Gruen.png", image_bytes(), headers).json()
        assert client.post(f"/chats/{chat}/stream", headers=headers,
                           json={"content": "Zwei Bilder", "attachment_ids": [another["id"],
                                 third["id"]]}).status_code == 422


def test_attachment_rejects_bad_inputs_and_tampering(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    provider = CaptureProvider()
    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: provider)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=data_dir,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = token(client)
        chat = client.post("/chats", headers=headers).json()["id"]
        for name, content in (("bild.png", b"x"), ("../pfad.txt", b"x"),
                              ("daten.txt", b"\x00abc"), ("leer.md", b"")):
            assert upload(client, chat, name, content, headers).status_code == 422
        assert upload(client, chat, "gross.txt", b"a" * (512 * 1024 + 1),
                      headers).status_code == 413
        attached = upload(client, chat, "Notiz.md", b"Erstfassung", headers).json()
        path = data_dir / "chat-attachments" / f"{attached['id']}.md"
        path.write_bytes(b"Manipuliert")
        assert client.post(f"/chats/{chat}/stream", headers=headers,
                           json={"content": "Prüfen", "attachment_id": attached["id"]}).status_code == 409
        assert client.get(f"/chats/{chat}/attachments/{attached['id']}").status_code == 409
        assert Database(data_dir / "jarvis.db").list_messages(chat) == []
        assert provider.calls == []
        assert client.delete(f"/chats/{chat}/attachments/{attached['id']}",
                             headers=headers).status_code == 200
        assert not path.exists()


def test_docx_attachment_extracts_text_without_sending_archive(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    provider = CaptureProvider()
    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: provider)
    raw = BytesIO()
    with ZipFile(raw, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.'
                         'openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                         '<w:p><w:r><w:t>Wohnung in Zürich</w:t></w:r></w:p>'
                         '</w:body></w:document>')
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=data_dir,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = token(client)
        chat = client.post("/chats", headers=headers).json()["id"]
        attached = upload(client, chat, "Wohnung.docx", raw.getvalue(), headers)
        assert attached.status_code == 200
        assert client.post(f"/chats/{chat}/stream", headers=headers,
                           json={"content": "Was steht drin?",
                                 "attachment_id": attached.json()["id"]}).status_code == 200
        assert "Wohnung in Zürich" in provider.calls[0][-1]["content"]
        assert client.get(f"/chats/{chat}/attachments/{attached.json()['id']}").content == raw.getvalue()


def test_text_pdf_attachment_keeps_original_and_page_context(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    provider = CaptureProvider()
    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: provider)
    original = text_pdf("Die Monatsmiete betraegt 900 Euro.")
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=data_dir,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = token(client)
        chat = client.post("/chats", headers=headers).json()["id"]
        attached = upload(client, chat, "Mietbeleg.pdf", original, headers)
        assert attached.status_code == 200, attached.text
        attachment_id = attached.json()["id"]
        answer = client.post(f"/chats/{chat}/stream", headers=headers,
                             json={"content": "Was ist die Monatsmiete?",
                                   "attachment_id": attachment_id})
        assert answer.status_code == 200
        assert "Seite 1" in provider.calls[0][-1]["content"]
        assert "900 Euro" in provider.calls[0][-1]["content"]
        assert client.get(f"/chats/{chat}").json()["messages"][0]["attachments"][0]["filename"] == "Mietbeleg.pdf"
        assert client.get(f"/chats/{chat}/attachments/{attachment_id}").content == original
    archive = tmp_path / "pdf-backup.zip"
    assert create_backup(data_dir, archive)["schema"] == 54
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=restored,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = token(client)
        assert client.get(f"/chats/{chat}/attachments/{attachment_id}").content == original
        assert client.delete(f"/chats/{chat}", headers=headers).status_code == 200
        assert not list((restored / "chat-attachments").iterdir())


def test_text_pdf_attachment_rejects_scan_encryption_and_corruption(tmp_path):
    data_dir = tmp_path / "data"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=data_dir,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = token(client)
        chat = client.post("/chats", headers=headers).json()["id"]
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        scanned = BytesIO()
        writer.write(scanned)
        result = upload(client, chat, "Scan.pdf", scanned.getvalue(), headers)
        assert result.status_code == 422 and "OCR" in result.text
        writer.encrypt("secret")
        encrypted = BytesIO()
        writer.write(encrypted)
        assert upload(client, chat, "Secret.pdf", encrypted.getvalue(), headers).status_code == 422
        assert upload(client, chat, "Kaputt.pdf", b"%PDF-falsch", headers).status_code == 422
        assert upload(client, chat, "Gross.pdf", b"%PDF-" + b"x" * (512 * 1024),
                      headers).status_code == 413
        assert not list((data_dir / "chat-attachments").glob("*"))


def test_image_attachment_uses_local_vision_model_and_keeps_original(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    provider = CaptureProvider()
    models = []

    def make(settings):
        models.append(settings.ollama_model)
        return provider

    monkeypatch.setattr("jarvis.app.make_provider", make)
    original = image_bytes(format="JPEG", exif=True)
    assert b"PRIVATE-IMAGE-METADATA" in original
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=data_dir,
                                        JARVIS_OLLAMA_MODEL="text-fixture",
                                        JARVIS_OLLAMA_VISION_MODEL="vision-fixture"))) as client:
        headers = token(client)
        chat = client.post("/chats", headers=headers).json()["id"]
        attached = upload(client, chat, "Rot.jpg", original, headers)
        assert attached.status_code == 200, attached.text
        item = attached.json()
        assert client.get(f"/chats/{chat}/attachments/{item['id']}/preview").status_code == 404
        answer = client.post(f"/chats/{chat}/stream", headers=headers,
                             json={"content": "Welche Farbe?", "attachment_id": item["id"]})
        assert answer.status_code == 200, answer.text
        assert models == ["vision-fixture"]
        sent = provider.calls[0][-1]
        assert sent["role"] == "user" and len(sent["images"]) == 1
        model_bytes = base64.b64decode(sent["images"][0])
        with Image.open(BytesIO(model_bytes)) as model_image:
            assert model_image.format == "JPEG" and model_image.size == (256, 256)
            assert not model_image.getexif()
        assert b"PRIVATE-IMAGE-METADATA" not in model_bytes
        loaded = client.get(f"/chats/{chat}").json()
        assert loaded["messages"][0]["attachments"][0]["sha256"] == item["sha256"]
        assert loaded["messages"][1]["model"] == "vision-fixture"
        assert client.get(f"/chats/{chat}/attachments/{item['id']}").content == original
        preview = client.get(f"/chats/{chat}/attachments/{item['id']}/preview")
        assert preview.status_code == 200 and preview.headers["content-type"].startswith("image/jpeg")
        assert preview.content == model_bytes
        assert client.post(f"/chats/{chat}/stream", headers=headers,
                           json={"content": "Noch eine Frage"}).status_code == 200
        assert models == ["vision-fixture", "text-fixture"]
        assert all("images" not in message for message in provider.calls[1])
    archive = tmp_path / "image-backup.zip"
    assert create_backup(data_dir, archive)["schema"] == 54
    restored = tmp_path / "restored"
    restore_backup(archive, restored)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=restored,
                                        JARVIS_OLLAMA_MODEL="text-fixture"))) as client:
        headers = token(client)
        assert client.get(f"/chats/{chat}/attachments/{item['id']}").content == original
        assert client.delete(f"/chats/{chat}", headers=headers).status_code == 200
        assert not list((restored / "chat-attachments").iterdir())


def test_image_attachment_rejects_wrong_type_dimensions_and_tampering(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    provider = CaptureProvider()
    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: provider)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=data_dir,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = token(client)
        chat = client.post("/chats", headers=headers).json()["id"]
        for filename, raw in (("Klein.png", image_bytes((16, 16))),
                              ("Falsch.jpg", image_bytes()),
                              ("Kaputt.png", b"\x89PNG\r\n\x1a\nkaputt"),
                              ("Riesig.png", image_bytes((2100, 2100)))):
            assert upload(client, chat, filename, raw, headers).status_code == 422
        attached = upload(client, chat, "Bild.webp", image_bytes(format="WEBP"), headers)
        assert attached.status_code == 200
        item = attached.json()
        path = data_dir / "chat-attachments" / f"{item['id']}.webp"
        path.write_bytes(image_bytes(format="PNG"))
        assert client.post(f"/chats/{chat}/stream", headers=headers,
                           json={"content": "Prüfen", "attachment_id": item["id"]}).status_code == 409
        assert provider.calls == []


def test_image_attachment_missing_local_model_is_reported_before_send(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(OllamaAdapter, "available", lambda _self: async_false())
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=data_dir,
                                        JARVIS_OLLAMA_MODEL="fixture",
                                        JARVIS_OLLAMA_VISION_MODEL="missing-vision"))) as client:
        headers = token(client)
        chat = client.post("/chats", headers=headers).json()["id"]
        item = upload(client, chat, "Rot.png", image_bytes(), headers).json()
        answer = client.post(f"/chats/{chat}/stream", headers=headers,
                             json={"content": "Welche Farbe?", "attachment_id": item["id"]})
        assert answer.status_code == 503 and "ollama pull missing-vision" in answer.text
        assert Database(data_dir / "jarvis.db").list_messages(chat) == []


async def async_false():
    return False


def test_attachment_backup_rejects_changed_original_and_cloud_send(tmp_path):
    data_dir = tmp_path / "data"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=data_dir,
                                        JARVIS_PROVIDER="openrouter",
                                        JARVIS_OPENROUTER_MODEL="openai/gpt-4o-mini"))) as client:
        headers = token(client)
        chat = client.post("/chats", headers=headers).json()["id"]
        attached = upload(client, chat, "Privat.txt", b"Privater Testinhalt", headers).json()
        assert client.post(f"/chats/{chat}/stream", headers=headers,
                           json={"content": "Prüfen", "allow_cloud": True,
                                 "attachment_id": attached["id"]}).status_code == 422
        assert Database(data_dir / "jarvis.db").list_messages(chat) == []
    original = data_dir / "chat-attachments" / f"{attached['id']}.txt"
    original.write_bytes(b"Anderer Inhalt")
    with pytest.raises(ValueError, match="Chatanhang fehlt oder weicht"):
        create_backup(data_dir, tmp_path / "backup.zip")


def test_schema52_migrates_to_chat_attachments_without_losing_messages(tmp_path):
    path = tmp_path / "jarvis.db"
    db = Database(path)
    chat = db.create_chat()
    db.add_message(chat["id"], "user", "Bestehende Nachricht")
    with db.connect() as connection:
        connection.execute("DROP TABLE chat_attachments")
        connection.execute("PRAGMA user_version=52")
    migrated = Database(path)
    with migrated.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 54
        assert connection.execute("SELECT COUNT(*) FROM chat_attachments").fetchone()[0] == 0
    assert migrated.list_messages(chat["id"])[0]["content"] == "Bestehende Nachricht"


def test_prior_local_attachment_is_not_sent_after_switch_to_openrouter(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    provider = CaptureProvider()
    monkeypatch.setattr("jarvis.app.make_provider", lambda _settings: provider)
    secret_text = "Privater lokaler Vertragswert 937 Euro"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=data_dir,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = token(client)
        chat = client.post("/chats", headers=headers).json()["id"]
        attached = upload(client, chat, "Vertrag.txt", secret_text.encode(), headers).json()
        assert client.post(f"/chats/{chat}/stream", headers=headers,
                           json={"content": "Lokal prüfen",
                                 "attachment_id": attached["id"]}).status_code == 200
        assert secret_text in provider.calls[-1][-1]["content"]

    with TestClient(create_app(Settings(JARVIS_DATA_DIR=data_dir,
                                        JARVIS_PROVIDER="openrouter",
                                        JARVIS_OPENROUTER_MODEL="openai/gpt-4o-mini"))) as client:
        headers = token(client)
        assert client.post(f"/chats/{chat}/stream", headers=headers,
                           json={"content": "Allgemeine Frage", "allow_cloud": True}).status_code == 422
        assert len(provider.calls) == 1
        assert client.get(f"/chats/{chat}").json()["messages"][0]["attachments"][0]["id"] == attached["id"]
