"""VoiceStudio speech stays on loopback and never changes the chat answer."""

import io
import json
import os
import wave
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.speech import SpeechError, SpeechStore, _chunks, _join_wav


def csrf(client):
    return {"x-jarvis-csrf": client.get("/session").json()["csrf"]}


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * 1600)
    return output.getvalue()


def test_speech_is_local_cached_bounded_and_deleted_with_chat(tmp_path, monkeypatch):
    calls = []
    audio = wav_bytes()

    def handler(request):
        calls.append(request)
        assert str(request.url) == "http://127.0.0.1:3900/v1/audio/speech"
        assert request.headers.get("authorization") is None
        body = json.loads(request.content)
        assert body["model"] == "tts-1" and body["voice"] == "default"
        assert body["response_format"] == "wav"
        assert body["input"] == "Guten Morgen! Das ist eine deutsche Antwort."
        return httpx.Response(200, headers={"content-type": "audio/wav"}, content=audio)

    original = httpx.AsyncClient
    monkeypatch.setattr("jarvis.speech.httpx.AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs))
    data = tmp_path / "data"
    settings = Settings(JARVIS_DATA_DIR=data, JARVIS_OLLAMA_MODEL="fixture")
    with TestClient(create_app(settings)) as client:
        db = Database(data / "jarvis.db")
        chat = db.create_chat()
        answer = db.add_message(chat["id"], "assistant",
                                "Guten Morgen! Das ist eine deutsche Antwort.")
        path = f"/chats/{chat['id']}/messages/{answer['id']}/speech"
        token = csrf(client)
        assert client.get(path).status_code == 404
        assert client.post(path).status_code == 403
        response = client.post(path, headers=token)
        assert response.status_code == 200, response.text
        assert response.json()["cached"] is False
        assert response.json()["voice"] == "default"
        assert client.get(path).content == audio
        assert client.post(path, headers=token).json()["cached"] is True
        assert len(calls) == 1
        assert db.list_messages(chat["id"])[0]["content"] == answer["content"]
        cached_path = SpeechStore(settings).path(chat["id"], answer["id"], answer["content"])
        assert cached_path.is_file()
        assert SpeechStore(settings).existing(chat["id"], answer["id"],
                                               answer["content"]) == cached_path
        assert client.delete(f"/chats/{chat['id']}", headers=token).status_code == 200
        assert not cached_path.exists()
        assert client.get(path).status_code == 404


def test_speech_offline_and_bad_requests_do_not_break_chat(tmp_path, monkeypatch):
    def unavailable(request):
        raise httpx.ConnectError("offline", request=request)

    original = httpx.AsyncClient
    monkeypatch.setattr("jarvis.speech.httpx.AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(unavailable), **kwargs))
    data = tmp_path / "data"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=data,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        db = Database(data / "jarvis.db")
        chat = db.create_chat()
        user = db.add_message(chat["id"], "user", "Hallo")
        answer = db.add_message(chat["id"], "assistant", "Guten Morgen.")
        token = csrf(client)
        wrong = f"/chats/{chat['id']}/messages/{user['id']}/speech"
        assert client.post(wrong, headers=token).status_code == 422
        path = f"/chats/{chat['id']}/messages/{answer['id']}/speech"
        response = client.post(path, headers=token)
        assert response.status_code == 503
        assert "VoiceStudio läuft nicht" in response.json()["detail"]
        assert client.get(f"/chats/{chat['id']}").json()["messages"][-1]["content"] == (
            "Guten Morgen.")
        long_answer = db.add_message(chat["id"], "assistant", "x" * 6001)
        too_long = client.post(f"/chats/{chat['id']}/messages/{long_answer['id']}/speech",
                               headers=token)
        assert too_long.status_code == 422


def test_speech_url_must_be_loopback(tmp_path):
    with pytest.raises(ValidationError):
        Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_VOICESTUDIO_URL="https://example.com")
    with pytest.raises(ValidationError):
        Settings(JARVIS_DATA_DIR=tmp_path,
                 JARVIS_VOICESTUDIO_URL="http://127.0.0.1:3900@evil.example")


def test_long_answer_chunks_merge_and_invalid_audio_is_refused():
    chunks = _chunks("Deutscher Satz. " * 120)
    assert len(chunks) == 2
    assert all(len(chunk) <= 1500 for chunk in chunks)
    combined = _join_wav([wav_bytes(), wav_bytes()])
    with wave.open(io.BytesIO(combined), "rb") as stream:
        assert stream.getnframes() == 3200
        assert stream.getframerate() == 16000
    with pytest.raises(SpeechError, match="keine WAV"):
        _join_wav([b"<html>falsches Audio</html>"])


def test_speech_does_not_follow_service_redirect(tmp_path, monkeypatch):
    requests = []

    def redirect(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://example.com/audio.wav"})

    original = httpx.AsyncClient
    monkeypatch.setattr("jarvis.speech.httpx.AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(redirect), **kwargs))
    data = tmp_path / "data"
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=data))) as client:
        db = Database(data / "jarvis.db")
        chat = db.create_chat()
        answer = db.add_message(chat["id"], "assistant", "Eine deutsche Antwort.")
        response = client.post(f"/chats/{chat['id']}/messages/{answer['id']}/speech",
                               headers=csrf(client))
        assert response.status_code == 503
        assert requests == ["http://127.0.0.1:3900/v1/audio/speech"]


def test_speech_cache_prunes_old_audio(tmp_path):
    store = SpeechStore(Settings(JARVIS_DATA_DIR=tmp_path))
    chat_id = str(uuid4())
    newest = None
    for index in range(32):
        path = store.path(chat_id, str(uuid4()), "Guten Morgen.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(wav_bytes())
        os.utime(path, (1_700_000_000 + index, 1_700_000_000 + index))
        newest = path
    store.prune(protect=newest)
    assert len(list((store.root / chat_id).glob("*.wav"))) == 30
    assert newest.is_file()


def test_cached_speech_survives_jarvis_restart_without_voicestudio(tmp_path, monkeypatch):
    requests = []

    def online(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "audio/wav"},
                              content=wav_bytes())

    original = httpx.AsyncClient
    monkeypatch.setattr("jarvis.speech.httpx.AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(online), **kwargs))
    settings = Settings(JARVIS_DATA_DIR=tmp_path / "data")
    db = Database(settings.data_dir / "jarvis.db")
    chat = db.create_chat()
    answer = db.add_message(chat["id"], "assistant", "Ein deutscher Satz.")
    route = f"/chats/{chat['id']}/messages/{answer['id']}/speech"
    with TestClient(create_app(settings)) as client:
        result = client.post(route, headers=csrf(client))
        assert result.status_code == 200 and result.json()["cached"] is False

    def offline(request):
        raise AssertionError("Cache darf nach Jarvis-Neustart keinen Modellaufruf brauchen")

    monkeypatch.setattr("jarvis.speech.httpx.AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(offline), **kwargs))
    with TestClient(create_app(settings)) as client:
        token = csrf(client)
        assert client.get(route).content == wav_bytes()
        result = client.post(route, headers=token)
        assert result.status_code == 200 and result.json()["cached"] is True
        assert len(requests) == 1
