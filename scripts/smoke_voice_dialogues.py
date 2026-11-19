"""Three real local German STT → Ollama dialogue turns in an isolated profile."""

import io
import json
import os
import sys
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from jarvis.app import create_app
from jarvis.config import Settings


def clip(audio: bytes, start: float, end: float) -> bytes:
    with wave.open(io.BytesIO(audio), "rb") as source:
        params = source.getparams()
        source.setpos(int(start * params.framerate))
        frames = source.readframes(int((end - start) * params.framerate))
    target = io.BytesIO()
    with wave.open(target, "wb") as result:
        result.setparams(params)
        result.writeframes(frames)
    return target.getvalue()


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Aufruf: python scripts/smoke_voice_dialogues.py <16-bit-PCM-WAV> <Modellordner>")
    sample = Path(sys.argv[1]).read_bytes()
    os.environ["JARVIS_VOICE_MODEL_DIR"] = str(Path(sys.argv[2]).resolve())
    with wave.open(io.BytesIO(sample), "rb") as source:
        duration = source.getnframes() / source.getframerate()
    samples = [clip(sample, 0, 2.1), clip(sample, 2.1, duration), sample]
    with TemporaryDirectory(prefix="jarvis-voice-dialogues-") as temporary:
        settings = Settings(JARVIS_DATA_DIR=Path(temporary), JARVIS_PROVIDER="ollama",
                            JARVIS_OLLAMA_MODEL="qwen3:4b-instruct",
                            JARVIS_OLLAMA_CONTEXT_TOKENS=4096, JARVIS_MAX_OUTPUT_TOKENS=64)
        with TestClient(create_app(settings)) as client:
            token = client.get("/session").json()["csrf"]
            headers = {"x-jarvis-csrf": token}
            chat = client.post("/chats", headers=headers).json()
            results = []
            for number, audio in enumerate(samples, start=1):
                result = client.post("/voice/transcribe", content=audio,
                                     headers={**headers, "content-type": "audio/wav"})
                assert result.status_code == 200, result.text
                transcript = result.json()["text"]
                response = client.post(f"/chats/{chat['id']}/stream",
                                       json={"content": transcript, "allow_cloud": False},
                                       headers=headers)
                assert response.status_code == 200, response.text
                messages = client.get(f"/chats/{chat['id']}").json()["messages"]
                assert messages[-2]["role"] == "user" and messages[-2]["content"] == transcript
                assert messages[-1]["role"] == "assistant", messages[-1]
                assert messages[-1]["status"] == "complete" and messages[-1]["content"].strip(), messages[-1]
                results.append({"round": number, "transcript": transcript,
                                "answer": messages[-1]["content"],
                                "stt_seconds": result.json()["stt_seconds"],
                                "llm_seconds": messages[-1]["usage_duration_seconds"]})
                print(json.dumps(results[-1]), flush=True)
            assert len({item["transcript"] for item in results}) == 3
            print(json.dumps({"three_dialogues": True, "local_only": True}), flush=True)


if __name__ == "__main__":
    main()
