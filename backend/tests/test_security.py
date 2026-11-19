"""Lokale API-Grenzen greifen vor Parsing und behalten reguläre Zugriffe bei."""

import asyncio

from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.security import MAX_API_BODY_BYTES, LocalApiBoundary

ORIGIN = "chrome-extension://" + "a" * 32


def test_headers_body_limit_and_existing_session_guards(tmp_path):
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path,
                                       JARVIS_OLLAMA_MODEL="fixture"))) as client:
        health = client.get("/health")
        assert health.status_code == 200
        for key, value in {"cache-control": "no-store", "referrer-policy": "no-referrer",
                           "x-content-type-options": "nosniff",
                           "x-frame-options": "DENY"}.items():
            assert health.headers[key] == value
        csrf = client.get("/session").json()["csrf"]
        assert client.post("/skills", json={"name": "Kurz", "content": "K"}).status_code == 403
        assert client.post("/skills", headers={"origin": "https://fremd.example",
                                              "x-jarvis-csrf": csrf},
                           json={"name": "Kurz", "content": "K"}).status_code == 403
        oversized = client.post("/skills", headers={"x-jarvis-csrf": csrf},
                                content=b"x" * (MAX_API_BODY_BYTES + 1))
        assert oversized.status_code == 413
        assert oversized.headers["cache-control"] == "no-store"
        assert client.post("/skills", headers={"x-jarvis-csrf": csrf},
                           json={"name": "Kurz", "content": "K"}).status_code == 200


def test_streamed_body_limit_without_content_length():
    called = False
    responses = []
    chunks = iter(({"type": "http.request", "body": b"a" * 600_000, "more_body": True},
                   {"type": "http.request", "body": b"b" * 600_000, "more_body": False}))

    async def receive():
        return next(chunks)

    async def send(message):
        responses.append(message)

    async def inner(_scope, _receive, _send):
        nonlocal called
        called = True

    scope = {"type": "http", "method": "POST", "headers": [], "path": "/test",
             "http_version": "1.1", "scheme": "http", "query_string": b"",
             "server": ("127.0.0.1", 8765), "client": ("127.0.0.1", 1), "root_path": ""}
    asyncio.run(LocalApiBoundary(inner)(scope, receive, send))
    assert not called
    assert responses[0]["status"] == 413
    assert (b"x-content-type-options", b"nosniff") in responses[0]["headers"]


def test_declared_limit_rejects_before_read_and_small_chunks_replay():
    scope = {"type": "http", "method": "POST", "headers": [], "path": "/test",
             "http_version": "1.1", "scheme": "http", "query_string": b"",
             "server": ("127.0.0.1", 8765), "client": ("127.0.0.1", 1), "root_path": ""}
    responses = []

    async def never_receive():
        raise AssertionError("Großer deklarierter Body wurde gelesen")

    async def send(message):
        responses.append(message)

    async def inner(_scope, receive, send):
        body = await receive()
        assert body == {"type": "http.request", "body": b"Hallo Welt",
                        "more_body": False}
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    too_large = {**scope, "headers": [(b"content-length", str(MAX_API_BODY_BYTES + 1).encode())]}
    asyncio.run(LocalApiBoundary(inner)(too_large, never_receive, send))
    assert responses[0]["status"] == 413
    responses.clear()
    chunks = iter(({"type": "http.request", "body": b"Hallo ", "more_body": True},
                   {"type": "http.request", "body": b"Welt", "more_body": False}))

    async def receive():
        return next(chunks)

    asyncio.run(LocalApiBoundary(inner)(scope, receive, send))
    assert responses[0]["status"] == 200
    assert (b"cache-control", b"no-store") in responses[0]["headers"]


def test_extension_pairing_brute_force_limit_and_new_code(tmp_path):
    with TestClient(create_app(Settings(JARVIS_DATA_DIR=tmp_path,
                                       JARVIS_OLLAMA_MODEL="fixture"))) as client:
        csrf = client.get("/session").json()["csrf"]
        headers = {"origin": ORIGIN}
        management = {"x-jarvis-csrf": csrf}
        code = client.post("/extension-management/code", headers=management).json()["code"]
        for _ in range(8):
            assert client.post("/extension/pair", headers=headers,
                               json={"code": "wrong-code-123"}).status_code == 403
        limited = client.post("/extension/pair", headers=headers, json={"code": code})
        assert limited.status_code == 429
        assert "neuen Code" in limited.json()["detail"]
        fresh = client.post("/extension-management/code", headers=management).json()["code"]
        paired = client.post("/extension/pair", headers=headers, json={"code": fresh})
        assert paired.status_code == 200 and paired.json()["token"]
