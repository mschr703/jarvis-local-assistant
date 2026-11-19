from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.quick_capture import QuickCaptureInput, QuickCaptureStore


def test_capture_is_one_time_and_requires_local_session(tmp_path):
    app = create_app(Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture"))
    with TestClient(app) as client:
        payload = {"text": "Bewusst markiert", "files": [str(tmp_path / "notiz.txt")], "notice": ""}
        assert client.post("/quick-captures", json=payload).status_code == 401
        token = client.get("/session").json()["csrf"]
        assert client.post("/quick-captures", json=payload).status_code == 403
        response = client.post("/quick-captures", headers={"x-jarvis-csrf": token}, json=payload)
        assert response.status_code == 200
        key = response.json()["token"]
        assert client.get(f"/quick-captures/{key}").json() == payload
        assert client.get(f"/quick-captures/{key}").status_code == 404
        assert client.get(f"/quick-captures/{'a' * 32}").status_code == 404


def test_capture_rejects_excess_and_expires(monkeypatch, tmp_path):
    store = QuickCaptureStore(ttl_seconds=5)
    now = [100.0]
    monkeypatch.setattr("jarvis.quick_capture.time.monotonic", lambda: now[0])
    key = store.add(QuickCaptureInput(text="kurz"))
    now[0] = 106.0
    assert store.take(key) is None
    app = create_app(Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_OLLAMA_MODEL="fixture"))
    with TestClient(app) as client:
        token = client.get("/session").json()["csrf"]
        headers = {"x-jarvis-csrf": token}
        assert client.post("/quick-captures", headers=headers,
                           json={"text": "x" * 100_001}).status_code == 422
        assert client.post("/quick-captures", headers=headers,
                           json={"files": ["relative.txt"]}).status_code == 422
