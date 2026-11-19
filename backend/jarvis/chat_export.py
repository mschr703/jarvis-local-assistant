"""Bounded, verified local ZIP export of one chat and its sent attachments."""

import io
import json
import re
from zipfile import ZIP_DEFLATED, ZipFile

from jarvis.chat_attachments import ChatAttachmentStore
from jarvis.db import Database

MAX_EXPORT_MESSAGES = 1000
MAX_EXPORT_ATTACHMENTS = 100
MAX_TRANSCRIPT_BYTES = 2 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 32 * 1024 * 1024


def _inline(value: str) -> str:
    clean = " ".join(value.split())
    return re.sub(r"([\\`*_{}\[\]()#+.!|<>-])", r"\\\1", clean)


def _fence(value: str) -> str:
    runs = re.findall(r"`+", value)
    return "`" * max([3, *(len(run) + 1 for run in runs)])


def _transcript(chat: dict, messages: list[dict]) -> bytes:
    if sum(len((message["content"] or "").encode("utf-8")) for message in messages) > MAX_TRANSCRIPT_BYTES:
        raise ValueError("Gesprächstranskript überschreitet 2 MiB")
    lines = ["# Gespräch", "", f"Titel: {_inline(chat['title'])}  ",
             f"Erstellt: {_inline(chat['created_at'])}  ",
             f"Status: {'archiviert' if chat['archived_at'] else 'aktiv'}", "",
             "Dieser Export enthält Nachrichtentext und die gesendeten Originalanhänge.", ""]
    for index, message in enumerate(messages, start=1):
        content = message["content"] or "(keine Ausgabe)"
        fence = _fence(content)
        lines.extend((f"## {index}. {'Du' if message['role'] == 'user' else 'Jarvis'}",
                      "", f"Zeit: {_inline(message['created_at'])}  ",
                      f"Status: {_inline(message['status'])}", "", fence,
                      content, fence, ""))
        for attachment in message["attachments"]:
            lines.append(f"Anhang: {_inline(attachment['filename'])} · "
                         f"SHA-256: {_inline(attachment['sha256'])}")
        if message["attachments"]:
            lines.append("")
    result = ("\n".join(lines).rstrip() + "\n").encode("utf-8")
    if len(result) > MAX_TRANSCRIPT_BYTES:
        raise ValueError("Gesprächstranskript überschreitet 2 MiB")
    return result


def _safe_filename(filename: str) -> bool:
    return (bool(filename) and len(filename) <= 120 and filename not in {".", ".."}
            and not any(char in filename for char in '/\\<>:"|?*')
            and not any(ord(char) < 32 or ord(char) == 127 for char in filename)
            and not filename.endswith((" ", ".")))


def _snapshot(messages: list[dict]) -> list[tuple]:
    return [(item["id"], item["role"], item["status"], item["content"],
             tuple((attachment["id"], attachment["filename"], attachment["sha256"],
                    attachment["size"]) for attachment in item["attachments"]))
            for item in messages]


def build_chat_export(db: Database, attachments: ChatAttachmentStore,
                      chat_id: str) -> bytes:
    chat = db.get_chat(chat_id)
    if chat is None:
        raise LookupError("Gespräch nicht gefunden")
    messages = db.list_messages(chat_id)
    if len(messages) > MAX_EXPORT_MESSAGES:
        raise ValueError("Gespräch hat zu viele Nachrichten für einen ZIP-Export")
    sent = [(message, item) for message in messages
            for item in message["attachments"]]
    if len(sent) > MAX_EXPORT_ATTACHMENTS:
        raise ValueError("Gespräch hat zu viele Anhänge für einen ZIP-Export")
    transcript = _transcript(chat, messages)
    collected = []
    total = 0
    for index, (message, expected) in enumerate(sent, start=1):
        item, original = attachments.read_original(chat_id, expected["id"])
        if (item["message_id"] != message["id"]
                or any(item[key] != expected[key] for key in
                       ("filename", "sha256", "size"))
                or not _safe_filename(item["filename"])):
            raise ValueError("Chatanhang oder Zuordnung hat sich während des Exports geändert")
        total += len(original)
        if total > MAX_TOTAL_ATTACHMENT_BYTES:
            raise ValueError("Chatanhänge überschreiten 32 MiB für einen ZIP-Export")
        archive_name = f"attachments/{index:03d}-{item['filename']}"
        collected.append((archive_name, item, original))
    if db.get_chat(chat_id) != chat or _snapshot(db.list_messages(chat_id)) != _snapshot(messages):
        raise ValueError("Gespräch hat sich während des Exports geändert; erneut versuchen")
    manifest = {"format": "jarvis-chat-export-v1", "chat_id": chat_id,
                "title": chat["title"], "created_at": chat["created_at"],
                "archived": bool(chat["archived_at"]),
                "message_count": len(messages),
                "attachments": [{"id": item["id"], "message_id": item["message_id"],
                                 "filename": item["filename"], "archive_name": name,
                                 "sha256": item["sha256"], "size": item["size"]}
                                for name, item, _ in collected]}
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED, allowZip64=False) as archive:
        archive.writestr("transcript.md", transcript)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False,
                                                       indent=2).encode("utf-8"))
        for name, _, original in collected:
            archive.writestr(name, original)
    db.log("chat_exported", f"Gespräch {chat_id}; {len(collected)} gesendete Anhänge")
    return output.getvalue()
