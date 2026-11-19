"""Real Edge microphone, local STT/Ollama/VoiceStudio, and interruption check."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from smoke_research_ui import overflow_nodes
from smoke_speech_ui import wait_http_ready
from smoke_ui import ROOT, port_free, stop_test_server

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.speech import SpeechStore


def main() -> None:
    if len(sys.argv) not in (3, 4) or (len(sys.argv) == 4 and sys.argv[3] != "--wait-audio"):
        raise SystemExit("Aufruf: python scripts/smoke_voice_ui.py <16-bit-PCM-WAV> <Whisper-Modellordner> [--wait-audio]")
    wait_audio = len(sys.argv) == 4
    sample = Path(sys.argv[1]).resolve()
    model_root = Path(sys.argv[2]).resolve()
    if not sample.is_file() or not model_root.is_dir():
        raise RuntimeError("WAV-Testdatei oder Whisper-Modellordner fehlt")
    if not all(port_free(port) for port in (8765, 5173)):
        raise RuntimeError("Jarvis vor diesem isolierten Test stoppen")
    with TemporaryDirectory(prefix="jarvis-voice-ui-") as temporary:
        root = Path(temporary)
        data = root / "data"
        settings = Settings(JARVIS_DATA_DIR=data, JARVIS_VOICESTUDIO_MODEL="tts-1",
                            JARVIS_VOICESTUDIO_VOICE="default")
        db = Database(data / "jarvis.db")
        sample_chat = db.create_chat()
        db.add_message(sample_chat["id"], "user", "Sprachtest")
        sample_answer = db.add_message(sample_chat["id"], "assistant",
                                       "Guten Morgen, Maxim. Ich kann diese Antwort lokal vorlesen.")
        cached = SpeechStore(settings).path(sample_chat["id"], sample_answer["id"],
                                            sample_answer["content"])
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(sample.read_bytes())
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct",
                           JARVIS_OLLAMA_CONTEXT_TOKENS="4096",
                           JARVIS_MAX_OUTPUT_TOKENS="32" if wait_audio else "64",
                           JARVIS_VOICE_MODEL_DIR=str(model_root),
                           JARVIS_VOICESTUDIO_URL="http://127.0.0.1:3900",
                           JARVIS_VOICESTUDIO_API_KEY="",
                           JARVIS_VOICESTUDIO_MODEL="tts-1",
                           JARVIS_VOICESTUDIO_VOICE="default")
        with (root / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(ROOT / "scripts/dev.py")],
                                       cwd=ROOT, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT)
            try:
                wait_http_ready(process, timeout=240)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge", headless=True, args=[
                        "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                        f"--use-file-for-fake-audio-capture={sample}",
                    ])
                    try:
                        page = browser.new_page(viewport={"width": 390, "height": 844},
                                                permissions=["microphone"])
                        page.set_default_timeout(600_000)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        page.get_by_role("button", name="Sprachtest", exact=True).click()
                        page.get_by_role("button", name="Sprachmodus starten").click()
                        expect(page.get_by_text("Bereit. Mikrofon", exact=False)).to_be_visible()
                        page.get_by_role("button", name="Vorlesen").click()
                        player = page.get_by_label("Jarvis-Antwort als Audio")
                        expect(player).to_be_visible()
                        page.get_by_role("button", name="Wiedergeben").click()
                        page.wait_for_function("document.querySelector('audio')?.currentTime > 0.1")
                        old_audio = player.element_handle()
                        started = time.monotonic()
                        page.get_by_role("button", name="Mikrofon starten / Antwort unterbrechen").click()
                        assert old_audio.evaluate("element => element.paused")
                        interruption_ms = round((time.monotonic() - started) * 1000)
                        expect(page.get_by_text("Mikrofon aktiv", exact=False)).to_be_visible()
                        page.wait_for_timeout(6000)
                        with page.expect_response(lambda response: response.url.endswith(
                                "/voice/transcribe") and response.request.method == "POST",
                                timeout=120_000) as transcription:
                            page.get_by_role("button", name="Aufnahme beenden").click()
                        assert transcription.value.status == 200, transcription.value.text()
                        result = transcription.value.json()
                        expect(page.get_by_text("Erkannt:", exact=False)).to_be_visible()
                        assert page.get_by_label("Nachricht").input_value().strip() == result["text"]
                        print(json.dumps({"stt_text": result["text"], "stt_seconds": result["stt_seconds"],
                                          "record_to_transcript_seconds": round(time.monotonic() - started, 2),
                                          "interruption_observed_ms": interruption_ms}),
                              flush=True)
                        if wait_audio:
                            with page.expect_response(lambda response: response.url.endswith("/speech")
                                    and response.request.method == "POST", timeout=600_000) as spoken:
                                page.get_by_role("button", name="Transkript senden und vorlesen").click()
                            assert spoken.value.status == 200, spoken.value.text()
                            player = page.get_by_label("Jarvis-Antwort als Audio")
                            expect(player).to_be_visible()
                            page.get_by_role("button", name="Wiedergeben").click()
                            page.wait_for_function("document.querySelector('audio')?.currentTime > 0.1")
                            generated_audio = player.element_handle()
                            page.get_by_role("button", name="Mikrofon starten / Antwort unterbrechen").click()
                            assert generated_audio.evaluate("element => element.paused")
                            print(json.dumps({"real_generated_speech": True,
                                              "voice_model": spoken.value.json()["model"]}), flush=True)
                        else:
                            page.get_by_role("button", name="Transkript senden und vorlesen").click()
                            expect(page.locator(".message.user").last).to_be_visible(timeout=120_000)
                            expect(page.locator(".message.assistant").last).to_contain_text("Jarvis", timeout=180_000)
                            expect(page.get_by_text("Erzeuge hörbare Antwort", exact=False)).to_be_visible(timeout=180_000)
                            # The user can interrupt an answer before synthesis finishes.
                            page.get_by_role("button", name="Mikrofon starten / Antwort unterbrechen").click()
                        expect(page.get_by_text("Mikrofon aktiv", exact=False)).to_be_visible()
                        assert page.locator("audio").count() == 0
                        page.get_by_role("button", name="Sprachmodus beenden").click()
                        expect(page.get_by_text("Aus. Mikrofon", exact=False)).to_be_visible()
                        assert page.evaluate("() => navigator.mediaDevices !== undefined")
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.evaluate("""() => {
                          const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
                          navigator.mediaDevices.getUserMedia = async (...args) => {
                            const stream = await original(...args);
                            window.__jarvisTestTrack = stream.getAudioTracks()[0];
                            return stream;
                          };
                        }""")
                        late_uploads = []
                        page.on("request", lambda request: late_uploads.append(request.url)
                                if request.url.endswith("/voice/transcribe") else None)
                        page.get_by_role("button", name="Sprachmodus starten").click()
                        page.get_by_role("button", name="Mikrofon starten / Antwort unterbrechen").click()
                        page.wait_for_function("window.__jarvisTestTrack?.readyState === 'live'")
                        page.get_by_role("button", name="Aufgaben", exact=True).click()
                        page.wait_for_function("window.__jarvisTestTrack?.readyState === 'ended'")
                        page.wait_for_timeout(500)
                        assert late_uploads == [], late_uploads
                        print(json.dumps({"voice_turn": True, "interruption": True,
                                          "microphone_off": True, "navigation_closes_microphone": True,
                                          "edge_390px": True}), flush=True)
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
