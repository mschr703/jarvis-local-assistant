"""Begrenzte Windows- und Browserwerkzeuge ohne modellgenerierte Shell-Befehle."""

import hashlib
import http.client
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
import zlib
from collections.abc import Callable
from html import escape
from pathlib import Path
from typing import Literal
from urllib.parse import SplitResult, urljoin, urlsplit
from uuid import uuid4

import truststore
from pydantic import BaseModel, ConfigDict, Field, field_validator

from jarvis.documents import MAX_FILE_BYTES, DocumentStore
from jarvis.engine import RunEngine, ToolDefinition, ToolUserError


def child_environment() -> dict[str, str]:
    """Desktopanwendungen benötigen den Backend-Schlüssel nicht."""
    environment = os.environ.copy()
    environment.pop("JARVIS_OPENROUTER_API_KEY", None)
    return environment


class OpenAppArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_id: Literal["notepad", "calculator"]


class OpenFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_id: str = Field(min_length=36, max_length=36)
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class BrowserReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=10, max_length=2048)


class BrowserDynamicArgs(BrowserReadArgs):
    """Explicit public page selected for a JavaScript-rendered read."""


class SaveNoteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=10_000)

    @field_validator("title", "content")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Notiztitel und -text dürfen nicht leer sein")
        return value


def public_target(url: str) -> tuple[SplitResult, str, int, str]:
    if url != url.strip() or any(ord(char) < 32 or char == "\\" for char in url):
        raise ValueError("Ungültige URL")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        username, password = parts.username, parts.password
    except ValueError as exc:
        raise ValueError("Ungültige URL") from exc
    if parts.scheme not in {"http", "https"} or not host:
        raise ValueError("Nur öffentliche HTTP(S)-Adressen sind erlaubt")
    if username is not None or password is not None or parts.fragment:
        raise ValueError("URL mit Zugangsdaten oder Fragment ist nicht erlaubt")
    host = host.lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Lokale Adressen sind gesperrt")
    try:
        literal_address = ipaddress.ip_address(host)
    except ValueError:
        literal_address = None
    if literal_address is not None and not literal_address.is_global:
        raise ValueError("Private oder reservierte Zieladresse ist gesperrt")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("Ungültiger Port") from exc
    if port not in {80, 443}:
        raise ValueError("Nur Standardports 80 und 443 sind erlaubt")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ValueError("Hostname konnte nicht aufgelöst werden") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("Private oder reservierte Zieladresse ist gesperrt")
    return parts, host, port, min(addresses, key=lambda address: (
        ipaddress.ip_address(address).version, address,
    ))


def validate_public_url(url: str) -> None:
    public_target(url)


class PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str):
        super().__init__(host, port, timeout=15)
        self.address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self.address, self.port), self.timeout)


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str):
        super().__init__(
            host, port, timeout=15,
            context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
        )
        self.address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self.address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def read_bounded_body(response: http.client.HTTPResponse, sock: socket.socket | None,
                      deadline: float, clock: Callable[[], float] = time.monotonic) -> bytes:
    body = bytearray()
    # read1 returns available data instead of waiting for the complete size. A
    # slow trickle must not keep the worker alive beyond the overall deadline.
    if not hasattr(response, "read1"):
        return response.read(2_000_001)
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise ValueError("Webseite überschritt das Zeitlimit")
        if sock is not None:
            sock.settimeout(min(15, remaining))
        chunk = response.read1(min(65_536, 2_000_001 - len(body)))
        if not chunk:
            return bytes(body)
        body.extend(chunk)
        if len(body) > 2_000_000:
            raise ValueError("Webseite ist zu groß")


def extract_public_pdf(body: bytes) -> dict:
    if not body.startswith(b"%PDF-"):
        raise ValueError("PDF-Signatur fehlt")
    script = Path(__file__).with_name("pdf_source_worker.py")
    try:
        completed = subprocess.run(
            [sys.executable, str(script)], input=body, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=8, check=False, env=child_environment(),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("PDF-Extraktion überschritt 8 Sekunden") from exc
    try:
        result = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("PDF-Extraktion lieferte kein gültiges Ergebnis") from exc
    if not isinstance(result, dict):
        result = {}
    if completed.returncode != 0:
        raise ValueError(str(result.get("error", "PDF ist nicht lesbar")))
    if (not isinstance(result.get("text"), str)
            or type(result.get("pages_read")) is not int
            or type(result.get("pages_total")) is not int):
        raise ValueError("PDF-Extraktion lieferte kein gültiges Ergebnis")
    return result


def fetch_public_document(url: str, *, allow_pdf: bool = False,
                          allowed_host: str | None = None) -> tuple[str, str, int, str]:
    current = url
    deadline = time.monotonic() + 20
    for _ in range(6):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Webseite überschritt das Zeitlimit")
        parts, host, port, address = public_target(current)
        if allowed_host is not None and (parts.scheme != "https" or host != allowed_host):
            raise ValueError("Weiterleitung auf eine andere Domain gesperrt")
        connection = (PinnedHTTPSConnection(host, port, address) if parts.scheme == "https"
                      else PinnedHTTPConnection(host, port, address))
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("Webseite überschritt das Zeitlimit")
            if hasattr(connection, "timeout"):
                connection.timeout = min(15, remaining)
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            connection.request("GET", path, headers={
                "Accept-Encoding": "gzip, identity", "User-Agent": "JarvisLocal/0.1",
            })
            sock = getattr(connection, "sock", None)
            if sock is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("Webseite überschritt das Zeitlimit")
                sock.settimeout(min(15, remaining))
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise ValueError("Weiterleitung ohne Ziel")
                next_url = urljoin(current, location)
                if allowed_host is not None:
                    destination = urlsplit(next_url)
                    if destination.scheme != "https" or destination.hostname != allowed_host:
                        raise ValueError("Weiterleitung auf eine andere Domain gesperrt")
                current = next_url
                continue
            if response.status >= 400:
                raise ValueError(f"Webseite antwortete mit HTTP {response.status}")
            content_type = response.headers.get_content_type()
            allowed = {"text/html", "application/xhtml+xml", "text/plain"}
            if allow_pdf:
                allowed.add("application/pdf")
            if content_type not in allowed:
                raise ValueError("Webseite liefert keinen unterstützten Text")
            encoding = response.getheader("Content-Encoding", "identity").lower()
            if encoding not in {"identity", "gzip"}:
                raise ValueError("Komprimierung der Webseite wird nicht unterstützt")
            body = read_bounded_body(response, sock, deadline)
            if len(body) > 2_000_000:
                raise ValueError("Webseite ist zu groß")
            if encoding == "gzip":
                try:
                    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                    body = decoder.decompress(body, 2_000_001)
                    if len(body) > 2_000_000 or decoder.unconsumed_tail:
                        raise ValueError("Webseite ist nach Dekomprimierung zu groß")
                    if not decoder.eof or decoder.unused_data:
                        raise ValueError("Komprimierte Webseite ist beschädigt")
                except zlib.error as exc:
                    raise ValueError("Komprimierte Webseite ist beschädigt") from exc
            if content_type == "application/pdf":
                pdf = extract_public_pdf(body)
                page_label = f"PDF · Seiten 1–{pdf['pages_read']} von {pdf['pages_total']}"
                html = f"<title>{page_label}</title><main><pre>{escape(pdf['text'])}</pre></main>"
                return current, html, response.status, content_type
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                html = body.decode(charset, "replace")
            except LookupError as exc:
                raise ValueError("Unbekannte Textkodierung") from exc
            if content_type == "text/plain":
                html = "<pre>" + escape(html) + "</pre>"
            return current, html, response.status, content_type
        except ssl.SSLCertVerificationError as exc:
            raise ValueError("TLS-Zertifikat der Webseite konnte nicht verifiziert werden") from exc
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            raise ValueError("Öffentliche Webseite konnte nicht geladen werden") from exc
        finally:
            connection.close()
    raise ValueError("Zu viele Weiterleitungen")


class DesktopTools:
    def __init__(self, documents: DocumentStore, data_dir: Path):
        self.documents = documents
        self.data_dir = Path(data_dir)
        self.browser_profile = self.data_dir / "browser-profile"

    def open_app(self, args: OpenAppArgs) -> dict:
        if sys.platform != "win32":
            raise RuntimeError("Desktopaktionen sind derzeit nur unter Windows unterstützt")
        executable = {"notepad": "notepad.exe", "calculator": "calc.exe"}[args.app_id]
        process = subprocess.Popen([executable], shell=False, env=child_environment())
        return {"app_id": args.app_id, "pid": process.pid, "status": "opened"}

    def open_file(self, args: OpenFileArgs) -> dict:
        if sys.platform != "win32":
            raise RuntimeError("Dateiöffnung ist derzeit nur unter Windows unterstützt")
        item = self.documents.get_document(args.file_id)
        if item is None or item["excluded"] or item["status"] != "indexed":
            raise ValueError("Datei ist nicht freigegeben oder nicht indexiert")
        folder = self.documents.get_folder(item["folder_id"])
        if folder is None:
            raise ValueError("Freigabeordner fehlt")
        root = self.documents.granted_root(folder)
        path = Path(item["path"]).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Datei liegt nicht im freigegebenen Ordner")
        if args.expected_sha256:
            before = path.stat()
            if before.st_size > MAX_FILE_BYTES:
                raise ValueError("Datei ist zu groß")
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    size += len(block)
                    if size > MAX_FILE_BYTES:
                        raise ValueError("Datei ist zu groß")
                    digest.update(block)
            after = path.stat()
            if (digest.hexdigest() != args.expected_sha256 or
                    (before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev)
                    != (after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev)):
                raise ToolUserError("Datei wurde seit dem gespeicherten Arbeitsstand geändert")
        # Die Dateizuordnung läuft in einem kurzlebigen Hilfsprozess, damit
        # die geöffnete Anwendung keine Backend-Secrets erbt.
        try:
            subprocess.run(
                [sys.executable, "-c", "import os, sys; os.startfile(sys.argv[1])", str(path)],
                env=child_environment(), stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=10, check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise ValueError("Datei konnte nicht geöffnet werden") from exc
        return {"file_id": args.file_id, "path": str(path), "status": "opened"}

    def browser_read(self, args: BrowserReadArgs) -> dict:
        try:
            final_url, html, status_code, _content_type = fetch_public_document(args.url)
        except ValueError as exc:
            raise ToolUserError(str(exc)) from exc
        from playwright.sync_api import Error, sync_playwright

        if self.browser_profile.is_symlink() or self.browser_profile.is_junction():
            raise ToolUserError("Browserprofil darf kein Link oder Junction sein")
        self.browser_profile.mkdir(parents=True, exist_ok=True)
        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    str(self.browser_profile), channel="msedge" if sys.platform == "win32" else None,
                    headless=True, java_script_enabled=False, service_workers="block",
                    accept_downloads=False, env=child_environment(),
                )
                try:
                    context.route("**/*", lambda route: route.abort())
                    page = context.new_page()
                    page.set_content(html, wait_until="domcontentloaded", timeout=10_000)
                    return {"url": final_url, "title": page.title()[:300],
                            "text": page.locator("body").inner_text(timeout=5000)[:12_000],
                            "status_code": status_code, "status": "read"}
                finally:
                    context.close()
        except Error as exc:
            raise ToolUserError("Browser konnte die geladene Seite nicht lesen") from exc

    def browser_dynamic_read(self, args: BrowserDynamicArgs,
                             cancel_event: threading.Event) -> dict:
        from jarvis.dynamic_browser import read_dynamic_page

        return read_dynamic_page(args.url, self.data_dir, cancel_event)

    def save_note(self, args: SaveNoteArgs) -> dict:
        notes_dir = self.data_dir / "notes"
        if notes_dir.is_symlink() or notes_dir.is_junction():
            raise ValueError("Notizordner darf kein Link oder Junction sein")
        notes_dir.mkdir(parents=True, exist_ok=True)
        path = notes_dir / f"{uuid4()}.txt"
        created = False
        try:
            with path.open("x", encoding="utf-8") as stream:
                created = True
                stream.write(args.title.strip() + "\n\n" + args.content.strip() + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            if created:
                path.unlink(missing_ok=True)
            raise
        return {"path": str(path), "status": "saved"}


def register_desktop_tools(engine: RunEngine, documents: DocumentStore, data_dir: Path) -> None:
    desktop = DesktopTools(documents, data_dir)
    engine.register(ToolDefinition("open_app", OpenAppArgs, desktop.open_app,
                                   scope="registered_app", effect="local_open", timeout_s=15))
    engine.register(ToolDefinition("open_file", OpenFileArgs, desktop.open_file,
                                   scope="indexed_allowed_file", effect="local_open", timeout_s=15))
    engine.register(ToolDefinition("browser_read", BrowserReadArgs, desktop.browser_read,
                                   scope="public_web", effect="read", timeout_s=75))
    engine.register(ToolDefinition("browser_dynamic_read", BrowserDynamicArgs,
                                   desktop.browser_dynamic_read, scope="public_web",
                                   effect="read", timeout_s=120, cancel_aware=True))
    engine.register(ToolDefinition("save_note", SaveNoteArgs, desktop.save_note,
                                   scope="jarvis_notes", effect="write", timeout_s=15))
