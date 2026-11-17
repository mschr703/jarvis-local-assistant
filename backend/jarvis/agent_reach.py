"""Read-only public Agent Reach adapter for explicitly supplied research URLs."""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from jarvis.db import utcnow

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
GITHUB_PATH = re.compile(
    r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:/(issues|pull)/([0-9]+))?/?$"
)
MAX_TEXT = 1_500
MAX_OUTPUT = 1_000_000


def public_target(url: str) -> tuple[str, str] | None:
    """Return only canonical YouTube/GitHub targets; reject credentials and private hosts."""
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port or parsed.fragment):
            return None
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host in {"youtube.com", "www.youtube.com", "youtu.be"}:
        if host == "youtu.be":
            video_id = parsed.path.strip("/")
        elif parsed.path == "/watch":
            values = parse_qs(parsed.query).get("v", [])
            video_id = values[0] if len(values) == 1 else ""
        else:
            return None
        if not VIDEO_ID.fullmatch(video_id):
            return None
        return "youtube", f"https://www.youtube.com/watch?v={video_id}"
    if host == "github.com" and not parsed.query:
        match = GITHUB_PATH.fullmatch(parsed.path)
        if not match or match.group(1) in {".", ".."} or match.group(2) in {".", ".."}:
            return None
        owner, repo, kind, number = match.groups()
        path = f"{owner}/{repo}" + (f"/{kind}/{number}" if kind else "")
        return "github", f"https://github.com/{path}"
    return None


def _run(channel: str, action: str, target: str, *options: str) -> dict:
    binary = Path(sys.executable).with_name(
        "agent-reach.exe" if sys.platform == "win32" else "agent-reach"
    )
    if not binary.is_file():
        raise ValueError("Agent Reach fehlt: uv sync --project backend ausführen")
    with tempfile.TemporaryDirectory(
        prefix="jarvis-public-reach-", ignore_cleanup_errors=True,
    ) as temporary:
        home = Path(temporary)
        allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT",
                   "COMSPEC", "LANG", "LC_ALL", "PYTHONUTF8"}
        env = {name: value for name, value in os.environ.items()
               if name.upper() in allowed}
        env.update(HOME=str(home), USERPROFILE=str(home),
                   APPDATA=str(home / "appdata"), LOCALAPPDATA=str(home / "localappdata"),
                   GH_CONFIG_DIR=str(home / "gh"), XDG_CONFIG_HOME=str(home / "config"),
                   XDG_CACHE_HOME=str(home / "cache"))
        command = [str(binary), channel, action, target, *options,
                   "--backend", "yt-dlp" if channel == "youtube" else "github-api",
                   "--timeout", "60"]
        try:
            result = subprocess.run(
                command, cwd=home, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=80, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Öffentlicher Quellenabruf hat das Zeitlimit überschritten") from exc
        if len(result.stdout.encode("utf-8")) > MAX_OUTPUT:
            raise ValueError("Öffentliche Quelle ist zu groß")
        try:
            payload = json.loads(result.stdout)
        except ValueError as exc:
            raise ValueError("Agent Reach lieferte keine gültige Antwort") from exc
        if not isinstance(payload, dict):
            raise ValueError("Agent Reach lieferte keine gültige Antwort")  # noqa: TRY004
        return payload


def _first(payload: dict, label: str) -> dict:
    if payload.get("ok") is not True:
        error = payload.get("error") or {}
        message = str(error.get("message") or error.get("code") or "unbekannter Abruffehler")
        raise ValueError(f"{label}: {message[:240]}")
    items = payload.get("items")
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise ValueError(f"{label}: keine lesbare öffentliche Quelle")
    return items[0]


def read_public_source(url: str) -> dict:
    target = public_target(url)
    if target is None:
        raise ValueError("Nur öffentliche YouTube-Videos oder GitHub-Repositories/Issues/PRs")
    channel, canonical = target
    if channel == "github":
        item = _first(_run("github", "read", canonical), "GitHub")
        content = str(item.get("text") or "").strip()[:MAX_TEXT]
        if len(content) < 150:
            raise ValueError("GitHub-Quelle enthält zu wenig lesbaren Text")
        return {"url": canonical, "title": str(item.get("title") or canonical)[:300],
                "text": content, "media_type": "github", "retrieved_at": utcnow(),
                "published_at": None}
    metadata = _first(_run("youtube", "read", canonical), "YouTube-Metadaten")
    title = str(metadata.get("title") or canonical)[:300]
    description = str(metadata.get("text") or "").strip()[:500]
    author = str(metadata.get("author") or "").strip()[:120]
    header = f"Titel: {title}\nKanal: {author}\nBeschreibung: {description}\n"
    caption_error = None
    try:
        subtitle = _first(
            _run("youtube", "subtitle", canonical, "--lang", "de,de-orig,en,en-orig"),
            "YouTube-Untertitel",
        )
        transcript = str(subtitle.get("text") or "").strip()
        if len(transcript) < 150:
            raise ValueError("YouTube-Untertitel: kein lesbarer Text")
        content = (header + "\nUntertitel:\n" + transcript)[:MAX_TEXT]
    except ValueError as exc:
        caption_error = str(exc)[:300]
        content = (header + "\nUntertitel nicht verfügbar. "
                   "Der folgende Text enthält nur Metadaten und Beschreibung.")[:MAX_TEXT]
    if len(content) < 150:
        raise ValueError("YouTube-Quelle enthält zu wenig lesbaren Text")
    return {"url": canonical, "title": title, "text": content,
            "media_type": "video", "retrieved_at": utcnow(),
            "published_at": str(metadata.get("published_at") or "")[:40] or None,
            "caption_error": caption_error}
