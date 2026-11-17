"""Kleine ASGI-Grenzen für die ausschließlich lokale HTTP-API."""

from starlette.responses import JSONResponse

MAX_API_BODY_BYTES = 1_048_576

SECURITY_HEADERS = {
    b"cache-control": b"no-store",
    b"referrer-policy": b"no-referrer",
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"DENY",
}


class LocalApiBoundary:
    """Prüft die tatsächliche Bodygröße vor FastAPI und ergänzt Antwortheader."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def secured_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers = [(key, value) for key, value in headers
                           if key.lower() not in SECURITY_HEADERS]
                headers.extend(SECURITY_HEADERS.items())
                message = {**message, "headers": headers}
            await send(message)

        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            lengths = [value for key, value in scope["headers"]
                       if key.lower() == b"content-length"]
            try:
                if len(lengths) > 1:
                    raise ValueError("Mehrere Content-Length-Header")
                declared = int(lengths[0]) if lengths else None
                if declared is not None and declared < 0:
                    raise ValueError("Negative Content-Length")
            except ValueError:
                return await JSONResponse({"detail": "Content-Length ist ungültig"},
                                          status_code=400)(scope, receive, secured_send)
            if declared is not None and declared > MAX_API_BODY_BYTES:
                return await self._too_large(scope, receive, secured_send)
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                if message["type"] != "http.request":
                    continue
                body.extend(message.get("body", b""))
                if len(body) > MAX_API_BODY_BYTES:
                    return await self._too_large(scope, receive, secured_send)
                if not message.get("more_body", False):
                    break
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            return await self.app(scope, replay, secured_send)
        return await self.app(scope, receive, secured_send)

    @staticmethod
    async def _too_large(scope, receive, send):
        return await JSONResponse({"detail": "API-Anfrage überschreitet 1 MiB"},
                                  status_code=413)(scope, receive, send)
