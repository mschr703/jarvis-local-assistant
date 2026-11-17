"""Long-lived, local German STT worker. One JSON request per input line."""

from __future__ import annotations

import base64
import io
import json
import sys
import time
from pathlib import Path

from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio


def main() -> None:
    model_root = Path(sys.argv[1]).resolve()
    if not model_root.is_dir():
        raise RuntimeError("Whisper-Modell fehlt; zuerst scripts/setup_voice.py ausführen")
    model = WhisperModel("base", device="cpu", compute_type="int8",
                         download_root=str(model_root), local_files_only=True, cpu_threads=4)
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        started = time.monotonic()
        try:
            request = json.loads(line)
            audio_bytes = base64.b64decode(request["audio"], validate=True)
            if not 100 <= len(audio_bytes) <= 8 * 1024 * 1024:
                raise ValueError("Audio muss zwischen 100 Byte und 8 MiB groß sein")
            samples = decode_audio(io.BytesIO(audio_bytes), sampling_rate=16000)
            duration = len(samples) / 16000
            if not 0.2 <= duration <= 35:
                raise ValueError("Sprachaufnahme muss zwischen 0,2 und 35 Sekunden dauern")
            segments, _info = model.transcribe(samples, language="de", beam_size=3,
                                               vad_filter=True, condition_on_previous_text=False)
            text = " ".join(segment.text.strip() for segment in segments).strip()
            if not text:
                raise ValueError("Keine verständliche Sprache erkannt")
            result = {"text": text, "audio_seconds": round(duration, 2),
                      "stt_seconds": round(time.monotonic() - started, 2), "model": "whisper-base"}
        except Exception as exc:  # noqa: BLE001 - request errors must not kill the worker.
            result = {"error": str(exc)[:300]}
        print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
