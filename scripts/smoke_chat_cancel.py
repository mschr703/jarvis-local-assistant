"""Prüft mit einem langsamen Fixture-Provider den Abbruch eines echten HTTP-SSE-Streams."""

import asyncio
import importlib
import json
import socket
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import uvicorn
from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.provider import Chunk, ProviderAdapter


class SlowProvider(ProviderAdapter):
    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[Chunk]:
        if messages[-1]["content"] == "Früher Abbruch":
            await asyncio.sleep(10)
        yield Chunk("text", text="Teilantwort")
        await asyncio.sleep(10)
        yield Chunk("text", text=" nach Wartezeit")
        yield Chunk("usage", input_tokens=10, output_tokens=4,
                    cost_usd=0.0, cost_kind="local_no_api_charge")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def main() -> None:
    app_module = importlib.import_module("jarvis.app")
    original_provider = app_module.make_provider
    app_module.make_provider = lambda _settings: SlowProvider()
    with TemporaryDirectory(prefix="jarvis-chat-cancel-") as temporary:
        settings = Settings(JARVIS_DATA_DIR=Path(temporary), JARVIS_OLLAMA_MODEL="fixture")
        port = free_port()
        server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1",
                                               port=port, log_level="error"))
        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.05)
            if not server.started:
                raise RuntimeError("Fixture-Server wurde nicht bereit")
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5) as client:
                csrf = client.get("/session").json()["csrf"]
                headers = {"X-Jarvis-CSRF": csrf}
                chat_id = client.post("/chats", headers=headers).json()["id"]
                with client.stream("POST", f"/chats/{chat_id}/stream", headers=headers,
                                   json={"content": "Hallo"}) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if line.startswith("data: ") and "Teilantwort" in line:
                            break
                    else:
                        raise AssertionError("SSE-Teilantwort fehlte")
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    messages = client.get(f"/chats/{chat_id}").json()["messages"]
                    if len(messages) == 2:
                        break
                    time.sleep(0.05)
                assert len(messages) == 2, messages
                assert messages[-1]["status"] == "cancelled", messages
                assert messages[-1]["content"] == "Teilantwort", messages
                early_chat = client.post("/chats", headers=headers).json()["id"]
                with client.stream("POST", f"/chats/{early_chat}/stream", headers=headers,
                                   json={"content": "Früher Abbruch"}) as response:
                    response.raise_for_status()
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    early_messages = client.get(f"/chats/{early_chat}").json()["messages"]
                    if len(early_messages) == 2:
                        break
                    time.sleep(0.05)
                assert len(early_messages) == 2, early_messages
                assert early_messages[-1]["status"] == "cancelled", early_messages
                assert early_messages[-1]["content"] == "", early_messages
                usage = client.get("/usage").json()
                assert all(item["status"] != "running" for item in usage), usage
                print(json.dumps({"status": messages[-1]["status"],
                                  "partial_text": messages[-1]["content"],
                                  "early_status": early_messages[-1]["status"]}, ensure_ascii=False))
        finally:
            server.should_exit = True
            worker.join(timeout=15)
            app_module.make_provider = original_provider
            if worker.is_alive():
                raise RuntimeError("Fixture-Server konnte nicht gestoppt werden")


if __name__ == "__main__":
    main()
