import gzip
import hashlib
import os
import socket
import ssl
from email.message import Message
from pathlib import Path

import pytest
import truststore

from jarvis.db import Database
from jarvis.desktop import (
    BrowserReadArgs,
    DesktopTools,
    OpenAppArgs,
    OpenFileArgs,
    PinnedHTTPConnection,
    PinnedHTTPSConnection,
    SaveNoteArgs,
    child_environment,
    fetch_public_document,
    public_target,
    read_bounded_body,
    validate_public_url,
)
from jarvis.documents import DocumentStore


def test_open_app_does_not_pass_openrouter_key_to_child(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_OPENROUTER_API_KEY", "fixture-not-a-real-key")
    monkeypatch.setenv("JARVIS_TEST_MARKER", "preserved")
    monkeypatch.setattr("jarvis.desktop.sys.platform", "win32")
    launched = []

    class FakeProcess:
        pid = 123

    monkeypatch.setattr("jarvis.desktop.subprocess.Popen",
                        lambda *args, **kwargs: launched.append((args, kwargs)) or FakeProcess())
    desktop = DesktopTools(DocumentStore(Database(tmp_path / "jarvis.db")), tmp_path)
    assert desktop.open_app(OpenAppArgs(app_id="calculator"))["status"] == "opened"
    assert launched[0][0] == (["calc.exe"],)
    assert launched[0][1]["env"]["JARVIS_TEST_MARKER"] == "preserved"
    assert "JARVIS_OPENROUTER_API_KEY" not in launched[0][1]["env"]
    assert "JARVIS_OPENROUTER_API_KEY" not in child_environment()


def test_browser_launch_does_not_pass_openrouter_key_to_edge(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_OPENROUTER_API_KEY", "fixture-not-a-real-key")
    monkeypatch.setattr("jarvis.desktop.fetch_public_document",
                        lambda _url: ("https://example.com/", "<title>Example</title><body>OK</body>", 200, "text/html"))
    launches = []

    class FakePage:
        def set_content(self, *_args, **_kwargs):
            pass

        def title(self):
            return "Example"

        def locator(self, _selector):
            return self

        def inner_text(self, **_kwargs):
            return "OK"

    class FakeContext:
        def route(self, *_args):
            pass

        def new_page(self):
            return FakePage()

        def close(self):
            pass

    class FakeChromium:
        def launch_persistent_context(self, *_args, **kwargs):
            launches.append(kwargs)
            return FakeContext()

    class FakePlaywright:
        chromium = FakeChromium()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("playwright.sync_api.sync_playwright", FakePlaywright)
    desktop = DesktopTools(DocumentStore(Database(tmp_path / "jarvis.db")), tmp_path)
    result = desktop.browser_read(BrowserReadArgs(url="https://example.com/"))
    assert result["title"] == "Example" and result["text"] == "OK"
    assert len(launches) == 1
    assert "JARVIS_OPENROUTER_API_KEY" not in launches[0]["env"]


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/",
    "http://10.1.2.3/", "file:///etc/passwd", "http://example.com:8080/",
    "https://user:pass@example.com/",
])
def test_private_and_unsafe_urls_blocked(url):
    with pytest.raises(ValueError):
        validate_public_url(url)


def test_https_connection_uses_native_verified_trust_store():
    connection = PinnedHTTPSConnection("example.com", 443, "93.184.215.14")
    assert isinstance(connection._context, truststore.SSLContext)
    assert connection._context.verify_mode == ssl.CERT_REQUIRED
    assert connection._context.check_hostname
    connection.close()


def test_tls_certificate_error_has_clear_safe_reason(monkeypatch):
    class BadCertificateConnection:
        timeout = 15

        def request(self, *_args, **_kwargs):
            raise ssl.SSLCertVerificationError("test")

        def close(self):
            pass

    monkeypatch.setattr("jarvis.desktop.PinnedHTTPSConnection",
                        lambda *_args: BadCertificateConnection())
    with pytest.raises(ValueError, match="TLS-Zertifikat"):
        fetch_public_document("https://example.com/")


def test_dns_resolution_must_be_public(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)),
    ])
    with pytest.raises(ValueError, match="Private"):
        validate_public_url("https://example.com/")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
    ])
    validate_public_url("https://example.com/")


def test_public_target_prefers_ipv4_when_both_families_are_available(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:4700::1111", 443, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
    ])
    assert public_target("https://example.com/")[3] == "93.184.215.14"


def test_open_file_requires_active_allowlist_and_resolved_path(tmp_path, monkeypatch):
    root = tmp_path / "files"
    root.mkdir()
    path = root / "note.txt"
    path.write_text("Erlaubt", encoding="utf-8")
    store = DocumentStore(Database(tmp_path / "jarvis.db"))
    folder = store.add_folder(str(root), "local_only")
    store.index_folder(folder["id"])
    document = store.documents()[0]
    opened = []
    monkeypatch.setenv("JARVIS_OPENROUTER_API_KEY", "fixture-not-a-real-key")
    monkeypatch.setattr("jarvis.desktop.sys.platform", "win32")
    monkeypatch.setattr("jarvis.desktop.subprocess.run",
                        lambda *args, **kwargs: opened.append((args, kwargs)))
    desktop = DesktopTools(store, tmp_path)
    result = desktop.open_file(OpenFileArgs(file_id=document["id"]))
    assert result["status"] == "opened" and len(opened) == 1
    assert opened[0][0][0][-1] == str(path.resolve())
    assert "JARVIS_OPENROUTER_API_KEY" not in opened[0][1]["env"]
    store.set_excluded(document["id"], True)
    with pytest.raises(ValueError, match="nicht freigegeben"):
        desktop.open_file(OpenFileArgs(file_id=document["id"]))


def test_open_file_with_work_state_hash_blocks_intervening_change(tmp_path, monkeypatch):
    root = tmp_path / "files"
    root.mkdir()
    path = root / "note.txt"
    path.write_text("Bisher", encoding="utf-8")
    store = DocumentStore(Database(tmp_path / "jarvis.db"))
    folder = store.add_folder(str(root), "local_only")
    store.index_folder(folder["id"])
    document = store.documents()[0]
    opened = []
    monkeypatch.setattr("jarvis.desktop.sys.platform", "win32")
    monkeypatch.setattr("jarvis.desktop.subprocess.run",
                        lambda *args, **kwargs: opened.append((args, kwargs)))
    args = OpenFileArgs(file_id=document["id"], expected_sha256=document["sha256"])
    DesktopTools(store, tmp_path).open_file(args)
    assert len(opened) == 1
    path.write_text("Extern geändert", encoding="utf-8")
    with pytest.raises(ValueError, match="seit dem gespeicherten Arbeitsstand"):
        DesktopTools(store, tmp_path).open_file(args)
    assert len(opened) == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() != document["sha256"]


def test_open_file_blocks_retargeted_grant(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "note.txt").write_text("Freigegeben", encoding="utf-8")
    private = tmp_path / "private"
    private.mkdir()
    store = DocumentStore(Database(tmp_path / "jarvis.db"))
    folder = store.add_folder(str(root), "local_only")
    store.index_folder(folder["id"])
    document = store.documents()[0]
    monkeypatch.setattr("jarvis.desktop.sys.platform", "win32")
    opened = []
    monkeypatch.setattr("jarvis.desktop.subprocess.run",
                        lambda *args, **kwargs: opened.append((args, kwargs)))
    original_resolve = Path.resolve

    def retargeted(path, *args, **kwargs):
        return private if path == root else original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", retargeted)
    with pytest.raises(ValueError, match="umgeleitet"):
        DesktopTools(store, tmp_path).open_file(OpenFileArgs(file_id=document["id"]))
    assert opened == []


def test_failed_note_flush_leaves_no_partial_file(tmp_path, monkeypatch):
    store = DocumentStore(Database(tmp_path / "jarvis.db"))
    desktop = DesktopTools(store, tmp_path)
    original_fsync = os.fsync
    failed = False

    def fail_once(descriptor):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("simulierter Schreibfehler")
        return original_fsync(descriptor)

    monkeypatch.setattr("jarvis.desktop.os.fsync", fail_once)
    arguments = SaveNoteArgs(title="Titel", content="Text")
    with pytest.raises(OSError, match="Schreibfehler"):
        desktop.save_note(arguments)
    assert list((tmp_path / "notes").glob("*.txt")) == []
    result = desktop.save_note(arguments)
    assert Path(result["path"]).read_text(encoding="utf-8") == "Titel\n\nText\n"


def test_save_note_rejects_whitespace_only_content():
    with pytest.raises(ValueError, match="nicht leer"):
        SaveNoteArgs(title="Titel", content="  \n  ")


def test_pinned_connection_uses_prevalidated_ip_without_new_dns(monkeypatch):
    calls = []
    fake_socket = object()
    monkeypatch.setattr(socket, "create_connection",
                        lambda address, timeout: calls.append((address, timeout)) or fake_socket)
    connection = PinnedHTTPConnection("example.com", 80, "93.184.215.14")
    connection.connect()
    assert calls == [(("93.184.215.14", 80), 15)]
    assert connection.sock is fake_socket


def test_public_fetch_rejects_private_redirect_before_second_connection(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
    ])
    calls = []

    class RedirectConnection:
        def __init__(self, host, port, address):
            calls.append((host, port, address))

        def request(self, *_args, **_kwargs):
            pass

        def getresponse(self):
            class Response:
                status = 302

                @staticmethod
                def getheader(_name):
                    return "http://127.0.0.1/private"

            return Response()

        def close(self):
            pass

    monkeypatch.setattr("jarvis.desktop.PinnedHTTPSConnection", RedirectConnection)
    with pytest.raises(ValueError, match="Private"):
        fetch_public_document("https://example.com/")
    assert calls == [("example.com", 443, "93.184.215.14")]


def test_public_fetch_rejects_other_host_before_second_dns_lookup(monkeypatch):
    resolved = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *args, **kwargs: (
        resolved.append(host) or [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                                   ("93.184.215.14", 443))]))

    class RedirectConnection:
        def __init__(self, *_args):
            pass

        def request(self, *_args, **_kwargs):
            pass

        def getresponse(self):
            class Response:
                status = 302

                @staticmethod
                def getheader(_name):
                    return "https://other.example/public"

            return Response()

        def close(self):
            pass

    monkeypatch.setattr("jarvis.desktop.PinnedHTTPSConnection", RedirectConnection)
    with pytest.raises(ValueError, match="andere Domain"):
        fetch_public_document("https://example.com/", allowed_host="example.com")
    assert resolved == ["example.com"]


def test_public_fetch_uses_checked_ip_and_limits_body(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
    ])
    calls = []

    class FakeConnection:
        def __init__(self, host, port, address):
            calls.append((host, port, address))

        def request(self, *_args, **_kwargs):
            pass

        def getresponse(self):
            class Response:
                status = 200
                headers = Message()

                @staticmethod
                def getheader(_name, default=None):
                    return default

                @staticmethod
                def read(_limit):
                    return b"<title>Example</title><body>OK</body>"

            Response.headers["Content-Type"] = "text/html; charset=utf-8"
            return Response()

        def close(self):
            pass

    monkeypatch.setattr("jarvis.desktop.PinnedHTTPSConnection", FakeConnection)
    url, html, status, kind = fetch_public_document("https://example.com/")
    assert (url, status, kind) == ("https://example.com/", 200, "text/html")
    assert "Example" in html
    assert calls == [("example.com", 443, "93.184.215.14")]


@pytest.mark.parametrize("payload,error", [
    (gzip.compress(b"A" * 2_000_001), "nach Dekomprimierung zu groß"),
    (b"not a gzip stream", "beschädigt"),
])
def test_public_fetch_rejects_oversized_or_corrupt_gzip(monkeypatch, payload, error):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
    ])

    class FakeConnection:
        def __init__(self, *_args):
            pass

        def request(self, *_args, **_kwargs):
            pass

        def getresponse(self):
            class Response:
                status = 200
                headers = Message()

                @staticmethod
                def getheader(name, default=None):
                    return "gzip" if name == "Content-Encoding" else default

                @staticmethod
                def read(_limit):
                    return payload

            Response.headers["Content-Type"] = "text/html; charset=utf-8"
            return Response()

        def close(self):
            pass

    monkeypatch.setattr("jarvis.desktop.PinnedHTTPSConnection", FakeConnection)
    with pytest.raises(ValueError, match=error):
        fetch_public_document("https://example.com/")


def test_public_fetch_reads_bounded_gzip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
    ])
    requested = []
    payload = gzip.compress(b"<title>Compressed</title><body>Readable</body>")

    class FakeConnection:
        def __init__(self, *_args):
            pass

        def request(self, _method, _path, headers):
            requested.append(headers["Accept-Encoding"])

        def getresponse(self):
            class Response:
                status = 200
                headers = Message()

                @staticmethod
                def getheader(name, default=None):
                    return "gzip" if name == "Content-Encoding" else default

                @staticmethod
                def read(_limit):
                    return payload

            Response.headers["Content-Type"] = "text/html; charset=utf-8"
            return Response()

        def close(self):
            pass

    monkeypatch.setattr("jarvis.desktop.PinnedHTTPSConnection", FakeConnection)
    _, html, _, _ = fetch_public_document("https://example.com/")
    assert "Readable" in html and requested == ["gzip, identity"]


def test_slow_trickle_cannot_extend_browser_read_forever():
    timeouts = []

    class FakeSocket:
        def settimeout(self, timeout):
            timeouts.append(timeout)

    class SlowResponse:
        def read1(self, _limit):
            return b"still arriving"

    ticks = iter([1.0, 21.0])
    with pytest.raises(ValueError, match="Zeitlimit"):
        read_bounded_body(SlowResponse(), FakeSocket(), 20.0,
                          clock=lambda: next(ticks))
    assert timeouts == [15]
