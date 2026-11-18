"""Direct chat file selection keeps the original and its message linkage local."""

from collections.abc import AsyncIterator
from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.provider import Chunk, ProviderAdapter


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
        assert download.headers["x-content-type-options"] == "nosniff"
        assert client.post(f"/chats/{chat}/stream", headers=headers,
                           json={"content": "Noch einmal", "attachment_id": item["id"]}).status_code == 422
        assert client.delete(f"/chats/{chat}/attachments/{item['id']}",
                             headers=headers).status_code == 404
        assert client.post(f"/chats/{chat}/stream", headers=headers,
                           json={"content": "Und die Stadt?"}).status_code == 200
        assert "Kaution für München" in " ".join(msg["content"] for msg in provider.calls[1])

    archive = tmp_path / "backup.zip"
    assert create_backup(data_dir, archive)["schema"] == 53
    restored = tmp_path / "restored"
    assert restore_backup(archive, restored)["schema"] == 53
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=restored,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = token(client)
        assert client.get(f"/chats/{chat}").json()["messages"][0]["attachments"][0]["id"] == item["id"]
        assert client.get(f"/chats/{chat}/attachments/{item['id']}").content == original
        assert client.delete(f"/chats/{chat}", headers=headers).status_code == 200
        assert client.get(f"/chats/{chat}/attachments/{item['id']}").status_code == 404
        assert not list((restored / "chat-attachments").iterdir())


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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 53
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
