"""Explicit dynamic browser tool, screenshot route and public preflight."""

import json
import threading

import pytest
from fastapi.testclient import TestClient

from jarvis import dynamic_browser
from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.dynamic_browser import read_dynamic_page
from jarvis.engine import ToolUserError


def test_dynamic_read_requires_public_https_before_worker(tmp_path):
    with pytest.raises(ToolUserError, match="HTTPS"):
        read_dynamic_page("http://example.com/", tmp_path, threading.Event())
    with pytest.raises(ToolUserError, match="Private|gesperrt"):
        read_dynamic_page("https://127.0.0.1/private", tmp_path, threading.Event())


def test_cross_domain_redirect_is_blocked_before_browser(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.desktop.public_target", lambda _url: (
        None, "example.com", 443, "93.184.216.34"))
    monkeypatch.setattr("jarvis.desktop.fetch_public_document", lambda _url, **_kwargs: (
        "https://other.example/page", "", 200, "text/html"))
    with pytest.raises(ToolUserError, match="Weiterleitung"):
        read_dynamic_page("https://example.com/page", tmp_path, threading.Event())


def test_browser_worker_timeout_stops_child_tree(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.desktop.public_target", lambda _url: (
        None, "example.com", 443, "93.184.216.34"))
    monkeypatch.setattr("jarvis.desktop.fetch_public_document", lambda url, **_kwargs: (
        url, "", 200, "text/html"))
    monkeypatch.setattr(dynamic_browser, "PROCESS_TIMEOUT", 0)
    original_is_file = dynamic_browser.Path.is_file
    monkeypatch.setattr(dynamic_browser.Path, "is_file", lambda path: (
        True if path.name in {"python.exe", "read_page.py"} else original_is_file(path)))

    class Child:
        def poll(self):
            return None

    stopped = []
    monkeypatch.setattr(dynamic_browser.subprocess, "Popen", lambda *_args, **_kwargs: Child())
    monkeypatch.setattr(dynamic_browser, "_stop_tree", lambda child: stopped.append(child))
    with pytest.raises(ToolUserError, match="Zeit-/Ausgabegrenze"):
        read_dynamic_page("https://example.com/page", tmp_path, threading.Event())
    assert stopped


def test_dynamic_read_screenshot_stays_bound_to_run(tmp_path, monkeypatch):
    filename = "a" * 32 + ".png"
    png = b"\x89PNG\r\n\x1a\n" + b"fixture"

    def fake_read(url, data_dir, cancel_event):
        assert url == "https://example.com/dynamic"
        assert not cancel_event.is_set()
        folder = data_dir / "browser-screenshots"
        folder.mkdir(parents=True)
        (folder / filename).write_bytes(png)
        return {"url": url, "title": "Dynamisch", "text": "Sichtbarer Seiteninhalt",
                "status": "read", "actions": ["navigate", "read"], "steps": 1,
                "screenshot": filename}

    monkeypatch.setattr("jarvis.dynamic_browser.read_dynamic_page", fake_read)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        headers = {"x-jarvis-csrf": client.get("/session").json()["csrf"]}
        run = client.post("/runs", headers=headers, json={
            "tool_name": "browser_dynamic_read",
            "args": {"url": "https://example.com/dynamic"},
        }).json()
        assert run["status"] == "succeeded"
        assert json.loads(run["result_json"])["text"] == "Sichtbarer Seiteninhalt"
        screenshot = client.get(f"/runs/{run['id']}/screenshot")
        assert screenshot.status_code == 200 and screenshot.content == png
        assert screenshot.headers["content-type"] == "image/png"
        assert client.get("/runs/missing/screenshot").status_code == 404
    archive = tmp_path.parent / f"{tmp_path.name}-backup.zip"
    create_backup(tmp_path, archive)
    restored = tmp_path.parent / f"{tmp_path.name}-restored"
    restore_backup(archive, restored)
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=restored,
                                        JARVIS_OLLAMA_MODEL="fixture"))) as client:
        client.get("/session")
        assert client.get(f"/runs/{run['id']}/screenshot").content == png
