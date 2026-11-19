"""Nur für den PDF-UI-Smoke: öffentliche Such-/HTTPS-Antwort durch Fixture ersetzen."""

import importlib
import os
import socket
from email.message import Message
from pathlib import Path

import uvicorn
from jarvis import desktop
from jarvis.app import create_app

PDF = Path(os.environ["JARVIS_PDF_FIXTURE"]).read_bytes()
HTML = ('<meta property="article:published_time" content="2025-06-14">'
        "<title>Forschungsseite</title><main><p>" +
        "Europa Clipper erforscht den Jupitermond Europa und untersucht seine eisige Oberflaeche im Jahr 2025. " * 5 +
        "</p></main>").encode()
real_getaddrinfo = socket.getaddrinfo


def fixture_getaddrinfo(host, port, *args, **kwargs):
    if host == "example.com":
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", port))]
    return real_getaddrinfo(host, port, *args, **kwargs)


class FixtureHTTPSConnection:
    def __init__(self, host, port, address):
        if (host, port, address) != ("example.com", 443, "93.184.215.14"):
            raise ValueError("Unerwartetes PDF-Fixture-Ziel")

    def request(self, *_args, **_kwargs):
        self.path = _args[1]

    def getresponse(self):
        payload = PDF if self.path == "/report.pdf" else HTML
        media_type = "application/pdf" if self.path == "/report.pdf" else "text/html"

        class Response:
            status = 200
            headers = Message()

            @staticmethod
            def getheader(_name, default=None):
                return default

            @staticmethod
            def read(_limit):
                return payload

        Response.headers["Content-Type"] = media_type
        return Response()

    def close(self):
        pass


socket.getaddrinfo = fixture_getaddrinfo
desktop.PinnedHTTPSConnection = FixtureHTTPSConnection
app_module = importlib.import_module("jarvis.app")
def fixture_search(query):
    if query == "Europa Clipper Instrumente":
        return ([{"title": "Forschungsseite", "url": "https://example.com/overview"}],
                "HTML-Fixture-Suche")
    return ([{"title": "Missionsbericht", "url": "https://example.com/report.pdf"}],
            "PDF-Fixture-Suche")


app_module.search_public_web = fixture_search
app = create_app()
app.state.work_states.create("Atlas", "Entwurf freigegeben", "Budget noch offen",
                              "Nächsten Entwurf prüfen", [], [], [])


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765)
