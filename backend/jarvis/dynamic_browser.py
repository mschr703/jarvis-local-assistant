"""Bounded, cancellable wrapper around the isolated browser-use page reader."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from jarvis.engine import ToolUserError

MARKER = "__JARVIS_JSON__"
MAX_SCREENSHOT_BYTES = 2_000_000
MAX_LOG_BYTES = 1_000_000
PROCESS_TIMEOUT = 95


def _stop_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, timeout=8, check=False)
    else:
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def read_dynamic_page(url: str, data_dir: Path, cancel_event: threading.Event) -> dict:
    from jarvis.desktop import fetch_public_document, public_target

    if urlsplit(url).scheme != "https":
        raise ToolUserError("Dynamisches Lesen erlaubt nur öffentliche HTTPS-Seiten")
    try:
        _, host, _, _ = public_target(url)
    except ValueError as exc:
        raise ToolUserError(str(exc)) from exc
    try:
        final_url, _, status_code, _ = fetch_public_document(url, allowed_host=host)
    except ValueError as exc:
        raise ToolUserError(str(exc)) from exc
    if urlsplit(final_url).hostname != host:
        raise ToolUserError("Weiterleitung auf eine andere Domain gesperrt")
    if cancel_event.is_set():
        return {}
    root = Path(__file__).resolve().parents[2]
    worker_dir = root / "integrations" / "browser_use"
    executable = worker_dir / ".venv" / ("Scripts/python.exe" if sys.platform == "win32"
                                          else "bin/python")
    script = worker_dir / "read_page.py"
    if not executable.is_file() or not script.is_file():
        raise ToolUserError("Browser-Use fehlt: python -m uv sync --project integrations/browser_use")
    screenshots = data_dir / "browser-screenshots"
    if screenshots.is_symlink() or screenshots.is_junction():
        raise ToolUserError("Screenshot-Ordner darf kein Link oder Junction sein")
    screenshots.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}.png"
    screenshot_path = screenshots / filename
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT", "COMSPEC",
               "USERPROFILE", "LOCALAPPDATA", "LANG", "LC_ALL"}
    environment = {name: value for name, value in os.environ.items()
                   if name.upper() in allowed}
    environment.update(ANONYMIZED_TELEMETRY="false", PYTHONIOENCODING="utf-8")
    command = [str(executable), str(script), final_url, str(screenshot_path)]
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            command, cwd=worker_dir, env=environment, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + PROCESS_TIMEOUT
        try:
            while process.poll() is None:
                if cancel_event.wait(0.25):
                    _stop_tree(process)
                    screenshot_path.unlink(missing_ok=True)
                    return {}
                if time.monotonic() >= deadline or log.tell() > MAX_LOG_BYTES:
                    _stop_tree(process)
                    raise ToolUserError("Dynamische Seite überschritt Zeit-/Ausgabegrenze")
            log.seek(0)
            output = log.read(MAX_LOG_BYTES + 1).decode("utf-8", errors="replace")
        finally:
            _stop_tree(process)
    if len(output.encode("utf-8")) > MAX_LOG_BYTES:
        screenshot_path.unlink(missing_ok=True)
        raise ToolUserError("Browser-Ausgabe ist zu groß")
    position = output.rfind(MARKER)
    if position < 0:
        screenshot_path.unlink(missing_ok=True)
        raise ToolUserError("Browser lieferte keinen lesbaren Status")
    try:
        payload = json.loads(output[position + len(MARKER):].splitlines()[0])
    except (ValueError, IndexError) as exc:
        screenshot_path.unlink(missing_ok=True)
        raise ToolUserError("Browser lieferte keinen gültigen Status") from exc
    if not isinstance(payload, dict):
        screenshot_path.unlink(missing_ok=True)
        raise ToolUserError("Browser lieferte keinen gültigen Status")
    if process.returncode or payload.get("error"):
        screenshot_path.unlink(missing_ok=True)
        raise ToolUserError(str(payload.get("error") or "Browser-Lesevorgang fehlgeschlagen")[:240])
    reported_url = str(payload.get("url") or "")
    try:
        reported = urlsplit(reported_url)
        same_target = (reported.scheme == "https" and reported.hostname == host
                       and reported.port in (None, 443) and reported.username is None
                       and reported.password is None)
    except ValueError:
        same_target = False
    if not same_target:
        screenshot_path.unlink(missing_ok=True)
        raise ToolUserError("Browser wurde auf eine andere Domain geleitet")
    if not screenshot_path.is_file() or screenshot_path.stat().st_size > MAX_SCREENSHOT_BYTES:
        screenshot_path.unlink(missing_ok=True)
        raise ToolUserError("Browser-Screenshot fehlt oder ist ungültig")
    with screenshot_path.open("rb") as stream:
        png_header = stream.read(8)
    if png_header != b"\x89PNG\r\n\x1a\n":
        screenshot_path.unlink(missing_ok=True)
        raise ToolUserError("Browser-Screenshot fehlt oder ist ungültig")
    return {"url": reported_url, "title": str(payload.get("title") or "")[:300],
            "text": str(payload.get("text") or "")[:12_000], "status_code": status_code,
            "status": "read", "actions": ["navigate", "read"], "steps": 1,
            "screenshot": filename}
