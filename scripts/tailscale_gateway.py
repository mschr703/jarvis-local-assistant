"""Opt-in, identity-gated Jarvis access through Tailscale Serve only."""

from __future__ import annotations

import os
import re
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from starlette.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
MAX_WRITE_BYTES = 1_048_576
HOST_RE = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net$")
REQUEST_HEADERS = {"accept", "accept-language", "authorization", "content-type", "cookie",
                   "if-none-match", "if-modified-since", "range", "x-jarvis-csrf"}
RESPONSE_HEADERS = {"cache-control", "content-disposition", "content-encoding",
                    "content-type", "etag", "expires", "last-modified", "location",
                    "referrer-policy", "set-cookie", "x-content-type-options", "x-frame-options"}


def create_app(*, host: str | None = None, login: str | None = None,
               port: int | None = None, dist: Path | None = None,
               backend: str = "http://127.0.0.1:8765") -> FastAPI:
    host = (host or os.environ.get("JARVIS_TAILSCALE_HOST", "")).strip().rstrip(".").lower()
    login = (login or os.environ.get("JARVIS_TAILSCALE_LOGIN", "")).strip().casefold()
    if port is None:
        try:
            port = int(os.environ.get("JARVIS_TAILSCALE_PORT", "8443"))
        except ValueError as exc:
            raise ValueError("Ungültiger Tailscale-Port") from exc
    if not HOST_RE.fullmatch(host) or not login or "@" not in login:
        raise ValueError("Tailscale-Host und erlaubtes Nutzer-Login müssen gesetzt sein")
    if not 1 <= port <= 65535:
        raise ValueError("Ungültiger Tailscale-Port")
    dist = dist or ROOT / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        raise ValueError("Frontend-Build fehlt; zuerst pnpm --dir frontend build")
    expected_host = f"{host}:{port}"
    expected_origin = f"https://{expected_host}"
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def tailscale_identity(request: Request, call_next):
        if request.headers.get("host", "").lower() != expected_host:
            return PlainTextResponse("Tailscale-Host nicht erlaubt", status_code=403)
        identity = request.headers.get("tailscale-user-login", "").strip().casefold()
        if identity != login:
            return PlainTextResponse("Tailscale-Nutzer nicht erlaubt", status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != expected_origin:
            return PlainTextResponse("Origin nicht erlaubt", status_code=403)
        return await call_next(request)

    @app.api_route("/api/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH",
                                                "DELETE", "OPTIONS"])
    async def api_proxy(path: str, request: Request):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > MAX_WRITE_BYTES:
                    return PlainTextResponse("Anfrage zu groß", status_code=413)
            content = bytes(body)
        else:
            content = None
        headers = {key: value for key, value in request.headers.items()
                   if key.lower() in REQUEST_HEADERS}
        if request.headers.get("origin"):
            headers["origin"] = "http://127.0.0.1:5173"
        url = f"{backend.rstrip('/')}/{path}"
        if request.url.query:
            url += "?" + request.url.query
        client = httpx.AsyncClient(timeout=120, follow_redirects=False)
        try:
            upstream = await client.send(client.build_request(
                request.method, url, headers=headers, content=content), stream=True)
        except httpx.HTTPError:
            await client.aclose()
            return PlainTextResponse("Lokales Jarvis-Backend nicht erreichbar", status_code=502)

        async def response_body():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        response_headers = {key: value for key, value in upstream.headers.items()
                            if key.lower() in RESPONSE_HEADERS}
        location = response_headers.get("location")
        if location:
            local_prefix = backend.rstrip("/") + "/"
            if location.startswith(local_prefix):
                response_headers["location"] = expected_origin + "/api/" + location[len(local_prefix):]
            elif location.startswith("/") and not location.startswith("//"):
                response_headers["location"] = expected_origin + "/api" + location
        return StreamingResponse(response_body(), status_code=upstream.status_code,
                                 headers=response_headers)

    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    async def frontend(path: str):
        file = (dist / path).resolve()
        if path and file.is_file() and file.is_relative_to(dist.resolve()):
            return FileResponse(file)
        return FileResponse(dist / "index.html")

    return app
