"""Bounded local VoiceStudio synthesis for immutable chat answers."""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import re
import wave
from pathlib import Path
from uuid import UUID, uuid4

import httpx

from jarvis.config import Settings

MAX_TEXT_CHARS = 6000
MAX_CHUNK_CHARS = 1500
MAX_AUDIO_BYTES = 32 * 1024 * 1024
MAX_CACHED_FILES = 30
MAX_CACHED_BYTES = 128 * 1024 * 1024
_AUDIO_NAME = re.compile(r"^[0-9a-f-]{36}-[0-9a-f]{16}\.wav$")


class SpeechError(ValueError):
    """VoiceStudio is unavailable or returned unusable audio."""


class SpeechValidationError(SpeechError):
    """The requested chat answer cannot be synthesized."""


def _uuid(value: str) -> str:
    try:
        result = str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise SpeechValidationError("Ungültige Gesprächs- oder Nachrichtenkennung") from exc
    if result != value:
        raise SpeechValidationError("Ungültige Gesprächs- oder Nachrichtenkennung")
    return result


def _chunks(text: str) -> list[str]:
    if not text.strip() or len(text) > MAX_TEXT_CHARS:
        raise SpeechValidationError("Nur fertige Antworten bis 6000 Zeichen vorlesen")
    chunks: list[str] = []
    current = ""
    for word in text.split():
        if len(word) > MAX_CHUNK_CHARS:
            raise SpeechValidationError("Ein einzelnes Wort ist für VoiceStudio zu lang")
        if len(current) + len(word) + 1 > MAX_CHUNK_CHARS:
            chunks.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        chunks.append(current)
    return chunks


def _wav_parts(audio: bytes) -> tuple[tuple, bytes]:
    if not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE":
        raise SpeechError("VoiceStudio lieferte keine WAV-Datei")
    try:
        with wave.open(io.BytesIO(audio), "rb") as stream:
            params = stream.getparams()
            if (params.nchannels not in (1, 2) or params.sampwidth not in (1, 2, 3, 4)
                    or params.comptype != "NONE" or not 8000 <= params.framerate <= 192000
                    or params.nframes < 1):
                raise SpeechError("VoiceStudio lieferte ein nicht unterstütztes WAV-Format")
            frames = stream.readframes(params.nframes)
            if len(frames) != params.nframes * params.nchannels * params.sampwidth:
                raise SpeechError("VoiceStudio lieferte eine unvollständige WAV-Datei")
            return (params.nchannels, params.sampwidth, params.framerate,
                    params.comptype, params.compname), frames
    except (EOFError, wave.Error) as exc:
        raise SpeechError("VoiceStudio lieferte eine beschädigte WAV-Datei") from exc


def _join_wav(parts: list[bytes]) -> bytes:
    if not parts:
        raise SpeechError("VoiceStudio lieferte kein Audio")
    if len(parts) == 1:
        _wav_parts(parts[0])
        return parts[0]
    first, frames = _wav_parts(parts[0])
    all_frames = [frames]
    for audio in parts[1:]:
        params, frames = _wav_parts(audio)
        if params != first:
            raise SpeechError("VoiceStudio wechselte das WAV-Format zwischen Textteilen")
        all_frames.append(frames)
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(first[0])
        stream.setsampwidth(first[1])
        stream.setframerate(first[2])
        stream.writeframes(b"".join(all_frames))
    return output.getvalue()


class SpeechStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.data_dir / "speech"
        self._lock = asyncio.Lock()

    def path(self, chat_id: str, message_id: str, content: str) -> Path:
        chat_id, message_id = _uuid(chat_id), _uuid(message_id)
        signature = hashlib.sha256((content + "\0" + self.settings.voicestudio_model
                                    + "\0" + self.settings.voicestudio_voice
                                    ).encode("utf-8")).hexdigest()[:16]
        return self.root / chat_id / f"{message_id}-{signature}.wav"

    def existing(self, chat_id: str, message_id: str, content: str) -> Path | None:
        path = self.path(chat_id, message_id, content)
        self._safe_folder(path.parent)
        if path.is_symlink() or path.is_junction():
            raise SpeechError("Audio-Datei darf kein Link sein")
        if not path.is_file() or path.stat().st_size > MAX_AUDIO_BYTES:
            return None
        try:
            _wav_parts(path.read_bytes())
        except SpeechError:
            return None
        return path

    async def generate(self, chat_id: str, message_id: str,
                       content: str) -> tuple[Path, bool]:
        path = self.path(chat_id, message_id, content)
        chunks = _chunks(content)
        async with self._lock:
            cached = self.existing(chat_id, message_id, content)
            if cached:
                return cached, True
            parts = []
            headers = {"Authorization": f"Bearer {self.settings.voicestudio_api_key}"}
            if not self.settings.voicestudio_api_key:
                headers = {}
            timeout = httpx.Timeout(600, connect=5)
            try:
                async with httpx.AsyncClient(timeout=timeout, trust_env=False,
                                             follow_redirects=False) as client:
                    for chunk in chunks:
                        async with client.stream(
                            "POST", self.settings.voicestudio_url + "/v1/audio/speech",
                            headers=headers, json={"model": self.settings.voicestudio_model,
                                                   "voice": self.settings.voicestudio_voice,
                                                   "input": chunk, "response_format": "wav"},
                        ) as response:
                            if response.status_code == 401:
                                raise SpeechError("VoiceStudio-Schlüssel fehlt oder ist ungültig")
                            if response.status_code != 200:
                                raise SpeechError(
                                    f"VoiceStudio meldete HTTP {response.status_code}; "
                                    "Stimme und Modell im lokalen Dienst prüfen")
                            if response.headers.get("content-type", "").split(";")[0].strip().lower() not in {
                                    "audio/wav", "audio/x-wav", "audio/wave"}:
                                raise SpeechError("VoiceStudio lieferte kein WAV-Audio")
                            body = bytearray()
                            async for block in response.aiter_bytes():
                                body.extend(block)
                                if len(body) > MAX_AUDIO_BYTES:
                                    raise SpeechError("VoiceStudio-Audio überschreitet 32 MiB")
                            parts.append(bytes(body))
                            if sum(len(part) for part in parts) > MAX_AUDIO_BYTES:
                                raise SpeechError("VoiceStudio-Audio überschreitet 32 MiB")
            except httpx.ConnectError as exc:
                raise SpeechError("VoiceStudio läuft nicht unter der lokalen Adresse") from exc
            except httpx.TimeoutException as exc:
                raise SpeechError("VoiceStudio-Audioerzeugung überschritt das Zeitlimit") from exc
            except httpx.HTTPError as exc:
                raise SpeechError("VoiceStudio-Verbindung ist fehlgeschlagen") from exc
            audio = _join_wav(parts)
            if len(audio) > MAX_AUDIO_BYTES:
                raise SpeechError("VoiceStudio-Audio überschreitet 32 MiB")
            self._safe_folder(path.parent)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.parent / f".{uuid4().hex}.tmp"
            try:
                temporary.write_bytes(audio)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            self.prune(protect=path)
            return path, False

    def _safe_folder(self, folder: Path) -> None:
        if self.root.is_symlink() or self.root.is_junction():
            raise SpeechError("Audio-Ordner darf kein Link sein")
        if folder.is_symlink() or folder.is_junction():
            raise SpeechError("Gesprächs-Audioordner darf kein Link sein")

    def prune(self, protect: Path | None = None) -> None:
        self._safe_folder(self.root)
        if not self.root.is_dir():
            return
        files = []
        for folder in self.root.iterdir():
            if folder.is_symlink() or folder.is_junction() or not folder.is_dir():
                continue
            for path in folder.iterdir():
                if path.is_file() and not path.is_symlink() and _AUDIO_NAME.fullmatch(path.name):
                    files.append(path)
        files.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
        total = 0
        for index, path in enumerate(files):
            total += path.stat().st_size
            if path != protect and (index >= MAX_CACHED_FILES or total > MAX_CACHED_BYTES):
                total -= path.stat().st_size
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        for folder in self.root.iterdir():
            if folder.is_dir() and not folder.is_symlink() and not folder.is_junction():
                try:
                    folder.rmdir()
                except OSError:
                    pass

    def delete_chat(self, chat_id: str) -> None:
        folder = self.root / _uuid(chat_id)
        self._safe_folder(folder)
        if not folder.is_dir():
            return
        for path in folder.iterdir():
            if path.is_file() and not path.is_symlink() and _AUDIO_NAME.fullmatch(path.name):
                path.unlink()
        try:
            folder.rmdir()
        except OSError:
            pass
