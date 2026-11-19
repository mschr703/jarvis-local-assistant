"""Real Edge/VoiceStudio CPU path for one locally synthesized chat answer."""

import io
import json
import os
import subprocess
import sys
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright
from smoke_research_ui import overflow_nodes
from smoke_speech_ui import wait_http_ready
from smoke_ui import ROOT, port_free, stop_test_server

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database


def main() -> None:
    if not all(port_free(port) for port in (8765, 5173)):
        raise RuntimeError("Jarvis vor dem isolierten Test stoppen")
    with urlopen("http://127.0.0.1:3900/health", timeout=10) as response:
        if response.status != 200:
            raise RuntimeError("VoiceStudio ist lokal nicht bereit")
    with TemporaryDirectory(prefix="jarvis-real-speech-") as temporary:
        root = Path(temporary)
        data = root / "data"
        db = Database(data / "jarvis.db")
        chat = db.create_chat()
        db.add_message(chat["id"], "user", "Sag einen kurzen deutschen Satz.")
        answer_text = "Guten Morgen, Maxim. Ich kann diese Antwort lokal vorlesen."
        answer = db.add_message(chat["id"], "assistant", answer_text)
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct",
                           JARVIS_VOICESTUDIO_URL="http://127.0.0.1:3900",
                           JARVIS_VOICESTUDIO_API_KEY="",
                           JARVIS_VOICESTUDIO_MODEL="tts-1",
                           JARVIS_VOICESTUDIO_VOICE="default")
        with (root / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                                       cwd=ROOT, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT)
            try:
                wait_http_ready(process, timeout=240)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge", headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 390, "height": 844})
                        page.set_default_timeout(600_000)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        page.get_by_role("button", name="Sag einen kurzen deutschen Satz.").click()
                        expect(page.get_by_text(answer_text, exact=True)).to_be_visible()
                        with page.expect_response(lambda response: response.url.endswith(
                                f"/chats/{chat['id']}/messages/{answer['id']}/speech")
                                and response.request.method == "POST", timeout=600_000) as spoken:
                            page.get_by_role("button", name="Vorlesen").click()
                        result = spoken.value
                        if result.status != 200:
                            raise AssertionError(f"Jarvis speech HTTP {result.status}: {result.text()[:500]}")
                        expect(page.get_by_label("Jarvis-Antwort als Audio")).to_be_visible()
                        audio_response = page.request.get(
                            f"http://127.0.0.1:8765/chats/{chat['id']}/messages/{answer['id']}/speech")
                        assert audio_response.status == 200
                        with wave.open(io.BytesIO(audio_response.body()), "rb") as audio:
                            frames = audio.readframes(audio.getnframes())
                            duration = round(audio.getnframes() / audio.getframerate(), 2)
                            assert duration > 0.5, duration
                            assert max(frames) != min(frames), "Audio ist stumm"
                        assert db.list_messages(chat["id"])[-1]["content"] == answer_text
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.get_by_role("button", name="Stoppen").click()
                        assert page.locator("audio").evaluate("(element) => element.paused")
                        print(json.dumps({"real_voicestudio": True, "german_wav_seconds": duration,
                                          "audio_non_silent": True, "text_unchanged": True,
                                          "edge_390px": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
