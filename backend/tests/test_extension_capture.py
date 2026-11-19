"""Edge-Kopplung, lokale Origin-Grenze und einmalige Aufgabenübernahme."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.backup import create_backup, restore_backup
from jarvis.config import Settings
from jarvis.db import Database
from jarvis.extension_capture import ExtensionCaptureStore
from jarvis.tasks import TaskStore

ORIGIN = "chrome-extension://" + "a" * 32
OTHER_ORIGIN = "chrome-extension://" + "b" * 32


def test_production_manifest_requests_only_click_scoped_tab_access():
    path = Path(__file__).resolve().parents[2] / "extension" / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert set(manifest["permissions"]) == {"activeTab", "scripting", "storage"}
    assert manifest["host_permissions"] == ["http://127.0.0.1:8765/*"]
    assert "content_scripts" not in manifest


def session(client):
    return {"x-jarvis-csrf": client.get("/session").json()["csrf"]}


def capture_payload():
    return {"request_id": str(uuid4()), "title": "Ausschreibung prüfen",
            "url": "https://example.com/jobs/42?ref=jarvis",
            "source_title": "Ausschreibung", "selection": "Frist: Montag",
            "page_text": ""}


def test_extension_requires_local_pairing_and_blocks_web_origins(tmp_path):
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path,
                                       JARVIS_OLLAMA_MODEL="fixture"))) as client:
        csrf = session(client)
        code = client.post("/extension-management/code", headers=csrf).json()["code"]
        preflight = client.options("/extension/capture", headers={
            "origin": ORIGIN, "access-control-request-method": "POST",
            "access-control-request-headers": "authorization,content-type"})
        assert preflight.status_code == 204
        assert preflight.headers["access-control-allow-origin"] == ORIGIN
        assert client.options("/extension/capture", headers={
            "origin": "https://evil.example", "access-control-request-method": "POST"
        }).status_code == 403
        assert client.post("/extension/pair", headers={"origin": "https://evil.example"},
                           json={"code": code}).status_code == 403
        assert client.post("/extension/pair", headers={"origin": ORIGIN},
                           json={"code": "wrong-code"}).status_code == 403
        token = client.post("/extension/pair", headers={"origin": ORIGIN},
                            json={"code": code}).json()["token"]
        assert client.post("/extension/pair", headers={"origin": OTHER_ORIGIN},
                           json={"code": code}).status_code == 403
        payload = capture_payload()
        headers = {"origin": ORIGIN, "authorization": f"Bearer {token}"}
        assert client.post("/extension/capture", headers={"origin": OTHER_ORIGIN,
                           "authorization": f"Bearer {token}"}, json=payload).status_code == 403
        assert client.post("/extension/capture", headers={"origin": "https://evil.example",
                           "authorization": f"Bearer {token}"}, json=payload).status_code == 403
        first = client.post("/extension/capture", headers=headers, json=payload)
        assert first.status_code == 200 and first.json()["created"] is True
        assert first.headers["access-control-allow-origin"] == ORIGIN
        task_id = first.json()["task_id"]
        task = next(item for item in client.get("/tasks").json() if item["id"] == task_id)
        assert task["source_url"] == payload["url"]
        assert "Frist: Montag" in task["notes"] and "Ausschreibung" in task["notes"]
        repeat = client.post("/extension/capture", headers=headers, json=payload)
        assert repeat.json() == {"task_id": task_id, "created": False}
        changed = dict(payload, title="Andere Aufgabe")
        assert client.post("/extension/capture", headers=headers, json=changed).status_code == 409
        assert len(client.get("/tasks").json()) == 1
        assert client.delete(f"/extension-management/pairs/{'a' * 32}", headers=csrf).json() == {"removed": True}
        assert client.post("/extension/capture", headers=headers, json=payload).status_code == 403


def test_pair_code_expiry_and_invalid_urls(tmp_path):
    app = create_app(Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture"))
    with TestClient(app) as client:
        csrf = session(client)
        code = client.post("/extension-management/code", headers=csrf).json()["code"]
        app.state.extension_captures._pairing = (code, datetime.now(UTC) - timedelta(seconds=1))
        assert client.post("/extension/pair", headers={"origin": ORIGIN},
                           json={"code": code}).status_code == 403
        fresh = client.post("/extension-management/code", headers=csrf).json()["code"]
        token = client.post("/extension/pair", headers={"origin": ORIGIN},
                            json={"code": fresh}).json()["token"]
        headers = {"origin": ORIGIN, "authorization": f"Bearer {token}"}
        for url in ("javascript:alert(1)", "https://user:pass@example.com/",
                    "https://example.com/\nInjected", "ftp://example.com/file"):
            assert client.post("/extension/capture", headers=headers,
                               json=dict(capture_payload(), url=url)).status_code == 422
        assert client.get("/tasks").json() == []


def test_parallel_capture_and_backup_restore(tmp_path):
    source = tmp_path / "source"
    db = Database(source / "jarvis.db")
    store = ExtensionCaptureStore(db)
    code = store.issue_code()["code"]
    token = store.pair(ORIGIN, code)["token"]
    payload = capture_payload()

    def send():
        return store.capture(ORIGIN, token, **payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda _: send(), range(2)))
    assert sorted(item["created"] for item in answers) == [False, True]
    assert answers[0]["task_id"] == answers[1]["task_id"]
    archive = tmp_path / "backup.zip"
    assert create_backup(source, archive)["schema"] == 57
    target = tmp_path / "restored"
    restore_backup(archive, target)
    restored = TaskStore(Database(target / "jarvis.db")).list_tasks()
    assert len(restored) == 1 and restored[0]["source_url"] == payload["url"]
    assert ExtensionCaptureStore(Database(target / "jarvis.db")).capture(
        ORIGIN, token, **payload) == {"task_id": restored[0]["id"], "created": False}
