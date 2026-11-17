"""Explizite Nutzer-Webziele für modellgenerierte Werkzeugpläne."""

import re
from urllib.parse import urlsplit

URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
TRAILING_PROSE = ".,;!?)]}"


def canonical_web_target(url: str) -> tuple[str, str, int, str, str] | None:
    if (not isinstance(url, str) or len(url) > 2048 or url != url.strip()
            or any(ord(char) < 32 or char == "\\" for char in url)):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
        user, password = parts.username, parts.password
    except ValueError:
        return None
    if (parts.scheme not in {"http", "https"} or not host or user is not None
            or password is not None or parts.fragment):
        return None
    default_port = 443 if parts.scheme == "https" else 80
    if port not in {None, default_port}:
        return None
    return parts.scheme, host.lower().rstrip("."), default_port, parts.path or "/", parts.query


def explicit_web_targets(objective: str) -> set[tuple[str, str, int, str, str]]:
    allowed = set()
    for match in URL_IN_TEXT.finditer(objective):
        target = canonical_web_target(match.group().rstrip(TRAILING_PROSE))
        if target is not None:
            allowed.add(target)
    return allowed


def require_explicit_web_target(url: str, allowed: set[tuple[str, str, int, str, str]]) -> None:
    target = canonical_web_target(url)
    if target is None or target not in allowed:
        raise ValueError("Browserziel muss als vollständige HTTP(S)-URL ausdrücklich im Nutzerauftrag stehen")
