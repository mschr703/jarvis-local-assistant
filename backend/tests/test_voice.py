"""The local audio endpoint is bounded and stays behind the Jarvis session guard."""

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.voice import VoiceError, VoiceTranscriber


def test_voice_transcription_requires_session_and_audio(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(VoiceTranscriber, "available", lambda self: True)

    async def transcribe(self, audio):
        seen.append(audio)
        return {"text": "Guten Morgen, Maxim.", "audio_seconds": 1.5,
                "stt_seconds": 0.8, "model": "whisper-base"}

    monkeypatch.setattr(VoiceTranscriber, "transcribe", transcribe)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path))) as client:
        assert client.post("/voice/transcribe", content=b"x" * 200,
                           headers={"content-type": "audio/wav"}).status_code == 401
        token = client.get("/session").json()["csrf"]
        assert client.get("/voice/status").json()["stt_ready"] is True
        audio = b"RIFF" + b"x" * 120
        assert client.post("/voice/transcribe", content=audio,
                           headers={"content-type": "audio/wav"}).status_code == 403
        headers = {"content-type": "audio/wav", "x-jarvis-csrf": token}
        response = client.post("/voice/transcribe", content=audio, headers=headers)
        assert response.status_code == 200
        assert response.json()["text"] == "Guten Morgen, Maxim."
        assert seen == [audio]
        assert client.post("/voice/transcribe", content=audio,
                           headers={**headers, "content-type": "text/plain"}).status_code == 415
        assert client.post("/voice/transcribe", content=b"x" * 90,
                           headers=headers).status_code == 422
        assert client.post("/voice/transcribe", content=b"x" * (8 * 1024 * 1024 + 1),
                           headers=headers).status_code == 413
        assert seen == [audio]


def test_voice_worker_error_does_not_store_audio_or_modify_chat(tmp_path, monkeypatch):
    monkeypatch.setattr(VoiceTranscriber, "available", lambda self: True)

    async def fail(self, audio):
        raise VoiceError("Keine verständliche Sprache erkannt")

    monkeypatch.setattr(VoiceTranscriber, "transcribe", fail)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path))) as client:
        chat = client.post("/chats", headers={"x-jarvis-csrf": client.get("/session").json()["csrf"]}).json()
        headers = {"content-type": "audio/webm",
                   "x-jarvis-csrf": client.get("/session").json()["csrf"]}
        response = client.post("/voice/transcribe", content=b"x" * 200, headers=headers)
        assert response.status_code == 422
        assert client.get(f"/chats/{chat['id']}").json()["messages"] == []
        assert list(tmp_path.rglob("*.webm")) == []


def test_voice_missing_local_model_is_explained(tmp_path, monkeypatch):
    monkeypatch.setattr(VoiceTranscriber, "available", lambda self: False)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path))) as client:
        token = client.get("/session").json()["csrf"]
        assert client.get("/voice/status").json()["stt_ready"] is False
        response = client.post("/voice/transcribe", content=b"x" * 200,
                               headers={"content-type": "audio/webm", "x-jarvis-csrf": token})
        assert response.status_code == 503
        assert "Einrichtung" in response.json()["detail"]
