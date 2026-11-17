"""Bounded bridge from Jarvis to an isolated local Faster-Whisper worker."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

MAX_AUDIO_BYTES = 8 * 1024 * 1024
ALLOWED_AUDIO_TYPES = {"audio/webm", "audio/ogg", "audio/wav", "audio/x-wav", "audio/mp4"}


class VoiceError(ValueError):
    pass


class VoiceTranscriber:
    def __init__(self, data_dir: Path):
        self.repo = Path(__file__).resolve().parents[2]
        self.model_root = Path(os.environ.get("JARVIS_VOICE_MODEL_DIR", data_dir / "voice-models"))
        self.worker: asyncio.subprocess.Process | None = None
        self.lock = asyncio.Lock()

    def available(self) -> bool:
        python = self._python()
        snapshots = self.model_root / "models--Systran--faster-whisper-base" / "snapshots"
        return python.is_file() and snapshots.is_dir() and any(snapshots.glob("*/model.bin"))

    def _python(self) -> Path:
        venv = self.repo / "integrations" / "livekit" / ".venv"
        return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    async def _start(self) -> asyncio.subprocess.Process:
        if self.worker and self.worker.returncode is None:
            return self.worker
        if not self.available():
            raise VoiceError("Lokale Spracherkennung fehlt; Einrichtung in README.md → Sprachmodus")
        environment = os.environ.copy()
        environment["HF_HUB_OFFLINE"] = "1"
        environment["HF_HUB_DISABLE_TELEMETRY"] = "1"
        for key in ("JARVIS_OPENROUTER_API_KEY", "JARVIS_VOICESTUDIO_API_KEY"):
            environment.pop(key, None)
        worker = await asyncio.create_subprocess_exec(
            str(self._python()), str(self.repo / "integrations/livekit/whisper_worker.py"),
            str(self.model_root), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            cwd=str(self.repo), env=environment,
        )
        self.worker = worker
        try:
            line = await asyncio.wait_for(worker.stdout.readline(), 75)
            if not line or json.loads(line).get("ready") is not True:
                raise VoiceError("Lokales Whisper-Modell konnte nicht geladen werden")
        except (TimeoutError, ValueError) as exc:
            await self.close()
            raise VoiceError("Lokales Whisper-Modell konnte nicht geladen werden") from exc
        return worker

    async def transcribe(self, audio: bytes) -> dict:
        if not 100 <= len(audio) <= MAX_AUDIO_BYTES:
            raise VoiceError("Audio muss zwischen 100 Byte und 8 MiB groß sein")
        async with self.lock:
            worker = await self._start()
            assert worker.stdin and worker.stdout
            try:
                worker.stdin.write(json.dumps({"audio": base64.b64encode(audio).decode("ascii")}).encode() + b"\n")
                await asyncio.wait_for(worker.stdin.drain(), 10)
                line = await asyncio.wait_for(worker.stdout.readline(), 90)
                if not line:
                    raise VoiceError("Lokale Spracherkennung wurde beendet")
                result = json.loads(line)
                if "error" in result:
                    raise VoiceError(str(result["error"]))
                return result
            except (TimeoutError, BrokenPipeError, json.JSONDecodeError) as exc:
                await self.close()
                raise VoiceError("Lokale Spracherkennung antwortet nicht") from exc

    async def close(self) -> None:
        worker, self.worker = self.worker, None
        if worker and worker.returncode is None:
            worker.kill()
            await worker.wait()
