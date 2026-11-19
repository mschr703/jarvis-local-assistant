"""Public, read-only Agent Reach source restrictions and caption failure evidence."""

import json
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from jarvis import agent_reach
from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.provider import Completion
from jarvis.research import read_source


@pytest.mark.parametrize(("url", "expected"), [
    ("https://www.youtube.com/watch?v=aircAruvnKk",
     ("youtube", "https://www.youtube.com/watch?v=aircAruvnKk")),
    ("https://youtu.be/aircAruvnKk",
     ("youtube", "https://www.youtube.com/watch?v=aircAruvnKk")),
    ("https://github.com/Panniantong/Agent-Reach",
     ("github", "https://github.com/Panniantong/Agent-Reach")),
    ("https://github.com/owner/repo/issues/42",
     ("github", "https://github.com/owner/repo/issues/42")),
    ("http://www.youtube.com/watch?v=aircAruvnKk", None),
    ("https://youtube.com.evil.test/watch?v=aircAruvnKk", None),
    ("https://github.com@evil.test/owner/repo", None),
    ("https://github.com/owner/repo?token=secret", None),
    ("https://github.com/owner/repo#private-note", None),
    ("https://github.com:bad/owner/repo", None),
    ("https://github.com/owner/repo/tree/main", None),
    ("https://www.youtube.com/playlist?list=private", None),
])
def test_only_explicit_public_targets(url, expected):
    assert agent_reach.public_target(url) == expected


def test_github_read_uses_only_public_channel(monkeypatch):
    calls = []

    def fake_run(*args):
        calls.append(args)
        return {"ok": True, "items": [{
            "title": "owner/repo", "text": "Öffentlicher README-Inhalt. " * 12,
        }]}

    monkeypatch.setattr(agent_reach, "_run", fake_run)
    result = read_source("https://github.com/owner/repo", "Eigene Quelle")
    assert calls == [("github", "read", "https://github.com/owner/repo")]
    assert result["media_type"] == "github"
    assert "README-Inhalt" in result["text"]
    assert result["url"] == "https://github.com/owner/repo"


def test_youtube_subtitles_and_visible_missing_caption_error(monkeypatch):
    calls = []

    def fake_run(channel, action, target, *options):
        calls.append((channel, action, target, options))
        if action == "read":
            return {"ok": True, "items": [{
                "title": "Ein Video", "author": "Ein Kanal",
                "published_at": "2026-01-02",
                "text": "Dies ist die öffentlich lesbare Beschreibung. " * 8,
            }]}
        return {"ok": True, "items": [{"text": "Wörtliche Untertitelstelle. " * 15}]}

    monkeypatch.setattr(agent_reach, "_run", fake_run)
    url = "https://www.youtube.com/watch?v=aircAruvnKk"
    result = read_source(url, "Direkte Quelle")
    assert result["media_type"] == "video"
    assert "Wörtliche Untertitelstelle." in result["text"]
    assert result["caption_error"] is None
    assert calls == [
        ("youtube", "read", url, ()),
        ("youtube", "subtitle", url, ("--lang", "de,de-orig,en,en-orig")),
    ]

    def no_captions(channel, action, target, *options):
        return (fake_run(channel, action, target, *options) if action == "read"
                else {"ok": False, "error": {
                    "code": "NOT_FOUND", "message": "Für dieses Video keine Untertitel",
                }})

    monkeypatch.setattr(agent_reach, "_run", no_captions)
    missing = read_source(url, "Direkte Quelle")
    assert missing["media_type"] == "video"
    assert "Untertitel nicht verfügbar" in missing["text"]
    assert "keine Untertitel" in missing["caption_error"]
    assert "Wörtliche Untertitelstelle" not in missing["text"]


def test_cli_runs_without_keys_or_saved_login(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("GH_TOKEN", "secret")
    monkeypatch.setenv("YOUTUBE_COOKIES", "secret")
    monkeypatch.setenv("OTHER_PRIVATE_CREDENTIAL", "secret")
    captured = {}

    def fake_subprocess(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        captured["timeout"] = kwargs["timeout"]
        return SimpleNamespace(stdout=json.dumps({"ok": True, "items": [{"title": "x"}]}),
                               returncode=0)

    monkeypatch.setattr(agent_reach.subprocess, "run", fake_subprocess)
    result = agent_reach._run("github", "read", "https://github.com/owner/repo")
    assert result["ok"] is True
    assert captured["command"][1:4] == ["github", "read", "https://github.com/owner/repo"]
    assert captured["command"][-4:] == ["--backend", "github-api", "--timeout", "60"]
    assert "OPENAI_API_KEY" not in captured["env"]
    assert "GH_TOKEN" not in captured["env"]
    assert "YOUTUBE_COOKIES" not in captured["env"]
    assert "OTHER_PRIVATE_CREDENTIAL" not in captured["env"]
    assert captured["env"]["GH_CONFIG_DIR"].startswith(captured["env"]["HOME"])
    assert captured["env"]["APPDATA"].startswith(captured["env"]["HOME"])
    assert captured["timeout"] == 80


def test_missing_subtitles_are_visible_in_saved_report(tmp_path, monkeypatch):
    url = "https://www.youtube.com/watch?v=aircAruvnKk"

    def fake_run(_channel, action, _target, *_options):
        if action == "read":
            return {"ok": True, "items": [{
                "title": "Video ohne Untertitel", "author": "Ein Kanal",
                "text": "Ein öffentlicher Videotext zu neuronalen Netzen. " * 8,
            }]}
        return {"ok": False, "error": {"message": "keine Untertitel verfügbar"}}

    async def fake_generate(*_args):
        return Completion("Die Beschreibung nennt neuronale Netze.", 30, 15, 0,
                          "local_no_api_charge")

    monkeypatch.setattr(agent_reach, "_run", fake_run)
    monkeypatch.setattr("jarvis.app.generate_text", fake_generate)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        response = client.post("/research", headers=headers, json={
            "question": "Was behandelt dieses Video?", "source_urls": [url],
        })
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["sources"][0]["media_type"] == "video"
        assert report["sources"][0]["caption_error"] == "YouTube-Untertitel: keine Untertitel verfügbar"
        assert "Untertitel nicht verfügbar" in report["sources"][0]["text"]
        assert "keine Untertitel verfügbar" in report["errors"][0]
        assert client.get(f"/research/{report['id']}").json() == report


def test_cli_timeout_is_a_visible_source_error(monkeypatch):
    def timed_out(command, **_kwargs):
        raise subprocess.TimeoutExpired(command, 80)

    monkeypatch.setattr(agent_reach.subprocess, "run", timed_out)
    with pytest.raises(ValueError, match="Zeitlimit"):
        agent_reach.read_public_source("https://github.com/owner/repo")
