"""Edge click path for local VoiceStudio speech controls using a loopback WAV fixture."""

import io
import json
import os
import subprocess
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, stop_test_server

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * 3200)
    return output.getvalue()


def wait_http_ready(process, timeout=240):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Jarvis-Testserver wurde vor dem Start beendet")
        try:
            with urlopen("http://127.0.0.1:8765/session", timeout=5):
                pass
            with urlopen("http://127.0.0.1:5173/", timeout=5):
                return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError("Jarvis-Testserver wurde nicht bereit")


class VoiceFixture(BaseHTTPRequestHandler):
    calls: ClassVar[list[dict]] = []

    def do_GET(self):
        assert self.path == "/api/tags"
        body = json.dumps({"models": [{"name": "qwen3:4b-instruct"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        assert self.path == "/v1/audio/speech"
        assert self.headers.get("Authorization") == "Bearer test-local"
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert body["input"] == "Guten Morgen Maxim. Das ist eine deutsche Jarvis-Antwort."
        assert body["response_format"] == "wav"
        self.calls.append(body)
        audio = wav_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio)))
        self.end_headers()
        self.wfile.write(audio)

    def log_message(self, *_args):
        pass


def main() -> None:
    if not all(port_free(port) for port in (3900, 8765, 5173)):
        raise RuntimeError("VoiceStudio und Jarvis vor dem isolierten Test stoppen")
    VoiceFixture.calls = []
    service = ThreadingHTTPServer(("127.0.0.1", 3900), VoiceFixture)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        with TemporaryDirectory(prefix="jarvis-speech-ui-") as temporary:
            root = Path(temporary)
            data = root / "data"
            db = Database(data / "jarvis.db")
            chat = db.create_chat()
            db.add_message(chat["id"], "user", "Sag guten Morgen.")
            text = "Guten Morgen Maxim. Das ist eine deutsche Jarvis-Antwort."
            answer = db.add_message(chat["id"], "assistant", text)
            environment = os.environ.copy()
            environment.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama",
                               JARVIS_OLLAMA_MODEL="qwen3:4b-instruct",
                               JARVIS_OLLAMA_URL="http://127.0.0.1:3900",
                               JARVIS_VOICESTUDIO_URL="http://127.0.0.1:3900",
                               JARVIS_VOICESTUDIO_API_KEY="test-local")
            with (root / "server.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                                           cwd=ROOT, env=environment, stdout=log,
                                           stderr=subprocess.STDOUT)
                try:
                    try:
                        wait_http_ready(process)
                    except RuntimeError:
                        log.flush()
                        message = (root / "server.log").read_text(encoding="utf-8")[-4000:]
                        print(message.encode("ascii", "backslashreplace").decode("ascii"))
                        raise
                    with sync_playwright() as playwright:
                        browser = playwright.chromium.launch(channel="msedge", headless=True)
                        try:
                            page = browser.new_page(viewport={"width": 390, "height": 844})
                            page.set_default_timeout(30_000)
                            page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded",
                                      timeout=120_000)
                            page.get_by_role("button", name="Chat", exact=True).click()
                            page.get_by_role("button", name="Sag guten Morgen.").click()
                            expect(page.get_by_text(text, exact=True)).to_be_visible(
                                timeout=30_000)
                            with page.expect_response(lambda response: response.url.endswith(
                                    f"/chats/{chat['id']}/messages/{answer['id']}/speech")
                                    and response.request.method == "POST") as spoken:
                                page.get_by_role("button", name="Vorlesen").click()
                            assert spoken.value.status == 200, spoken.value.text()
                            expect(page.get_by_label("Jarvis-Antwort als Audio")).to_be_visible()
                            expect(page.get_by_text("Stimme: default · tts-1")).to_be_visible()
                            page.get_by_role("button", name="Stoppen").click()
                            assert page.locator("audio").evaluate("(audio) => audio.paused")
                            assert len(VoiceFixture.calls) == 1
                            page.reload(wait_until="domcontentloaded", timeout=120_000)
                            page.get_by_role("button", name="Chat", exact=True).click()
                            page.get_by_role("button", name="Sag guten Morgen.").click()
                            with page.expect_response(lambda response: response.url.endswith(
                                    f"/chats/{chat['id']}/messages/{answer['id']}/speech")
                                    and response.request.method == "POST") as cached:
                                page.get_by_role("button", name="Vorlesen").click()
                            assert cached.value.json()["cached"] is True
                            assert len(VoiceFixture.calls) == 1
                            assert db.list_messages(chat["id"])[-1]["content"] == text
                            assert not overflow_nodes(page), overflow_nodes(page)
                            print(json.dumps({"loopback": True, "wav": True,
                                              "stop": True, "cache_after_reload": True,
                                              "text_unchanged": True, "mobile_width": "fits"}))
                        finally:
                            browser.close()
                finally:
                    stop_test_server(process)
    finally:
        service.shutdown()
        service.server_close()


if __name__ == "__main__":
    main()
