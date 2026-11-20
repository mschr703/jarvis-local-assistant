"""Tailscale gateway denies every route without the configured Serve identity."""

import importlib.util
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("jarvis_tailscale_gateway",
                                           ROOT / "scripts/tailscale_gateway.py")
assert SPEC and SPEC.loader
tailscale_gateway = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tailscale_gateway)


def test_tailnet_gateway_identity_static_and_session_proxy(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<title>Jarvis test</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("export const ready = true;", encoding="utf-8")
    backend = FastAPI()
    writes = []

    @backend.get("/session")
    def session(response: Response):
        response.set_cookie("jarvis_session", "test-token", httponly=True, samesite="strict")
        return {"csrf": "test-token"}

    @backend.post("/update")
    async def update(request: Request):
        writes.append({"origin": request.headers.get("origin"),
                       "csrf": request.headers.get("x-jarvis-csrf"),
                       "cookie": request.cookies.get("jarvis_session"),
                       "body": await request.json()})
        return {"saved": True}

    original_client = httpx.AsyncClient

    def mock_client(*_args, **_kwargs):
        return original_client(transport=httpx.ASGITransport(app=backend))

    monkeypatch.setattr(tailscale_gateway.httpx, "AsyncClient", mock_client)
    app = tailscale_gateway.create_app(host="maxim.example.ts.net",
                                       login="owner@example.org", dist=dist)
    headers = {"host": "maxim.example.ts.net:8443",
               "tailscale-user-login": "owner@example.org"}
    with TestClient(app) as client:
        assert client.get("/", headers={"host": headers["host"]}).status_code == 403
        assert client.get("/api/session", headers={"host": headers["host"]}).status_code == 403
        assert client.get("/assets/app.js", headers={"host": headers["host"]}).status_code == 403
        assert client.get("/", headers={**headers,
                                       "tailscale-user-login": "other@example.org"}).status_code == 403
        assert client.get("/", headers={**headers,
                                       "host": "untrusted.example.ts.net:8443"}).status_code == 403
        assert client.get("/", headers={**headers,
                                       "origin": "https://evil.example"}).status_code == 403
        assert client.get("/", headers=headers).text == "<title>Jarvis test</title>"
        assert "ready = true" in client.get("/assets/app.js", headers=headers).text
        session_response = client.get("/api/session", headers=headers)
        assert session_response.status_code == 200
        assert session_response.json() == {"csrf": "test-token"}
        assert "jarvis_session=" in session_response.headers["set-cookie"]
        sent = client.post("/api/update", headers={**headers,
                                                   "origin": "https://maxim.example.ts.net:8443",
                                                   "x-jarvis-csrf": "test-token"},
                           json={"item": "okay"})
        assert sent.status_code == 200 and sent.json() == {"saved": True}
        assert writes == [{"origin": "http://127.0.0.1:5173", "csrf": "test-token",
                           "cookie": "test-token", "body": {"item": "okay"}}]
        too_large = client.post("/api/update", headers=headers,
                                content=b"x" * (tailscale_gateway.MAX_WRITE_BYTES + 1))
        assert too_large.status_code == 413
        assert len(writes) == 1


def test_tailnet_gateway_rejects_unconfigured_host_and_login(tmp_path):
    (tmp_path / "index.html").write_text("ok", encoding="utf-8")
    for host, login in (("", "owner@example.org"), ("attacker.example.com", "owner@example.org"),
                        ("maxim.example.ts.net", "")):
        try:
            tailscale_gateway.create_app(host=host, login=login, dist=tmp_path)
        except ValueError:
            pass
        else:
            raise AssertionError((host, login))
