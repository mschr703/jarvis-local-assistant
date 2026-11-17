"""Temporäres sichtbares Edge-Profil für die manuelle S4a-Toolbar-Abnahme."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import sync_playwright
from smoke_ui import ROOT, port_free, stop_test_server, wait_ready

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database
from jarvis.tasks import TaskStore


class SourceHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (b"<!doctype html><title>Edge-Toolbar-Test</title>"
                b"<h1>Edge-Toolbar-Test</h1><p id='selected'>Frist: Montag</p>"
                b"<p>Erfahrung mit Python erforderlich.</p>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def main() -> None:
    if any(not port_free(port) for port in (8765, 5173, 8766)):
        raise RuntimeError("Testports 8765, 5173 oder 8766 sind belegt")
    source_server = ThreadingHTTPServer(("127.0.0.1", 8766), SourceHandler)
    source_thread = threading.Thread(target=source_server.serve_forever, daemon=True)
    source_thread.start()
    try:
        with TemporaryDirectory(prefix="jarvis-edge-toolbar-") as temporary:
            root = Path(temporary)
            data_dir = root / "data"
            environment = os.environ.copy()
            environment.update(JARVIS_DATA_DIR=str(data_dir), JARVIS_PROVIDER="ollama",
                               JARVIS_OLLAMA_MODEL="qwen3:4b-instruct")
            with (root / "server.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                                           cwd=ROOT, env=environment, stdout=log,
                                           stderr=subprocess.STDOUT)
                try:
                    wait_ready(process, timeout=120)
                    with sync_playwright() as playwright:
                        context = playwright.chromium.launch_persistent_context(
                            str(root / "edge-profile"), channel="msedge", headless=False,
                            args=[f"--disable-extensions-except={ROOT / 'extension'}",
                                  f"--load-extension={ROOT / 'extension'}"],
                            viewport={"width": 1280, "height": 780})
                        try:
                            worker = (context.service_workers[0] if context.service_workers else
                                      context.wait_for_event("serviceworker", timeout=30_000))
                            extension_id = worker.url.split("/")[2]
                            jarvis = context.new_page()
                            jarvis.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                            jarvis.get_by_role("button", name="Einstellungen").click()
                            jarvis.get_by_role("button", name="Neuen Kopplungscode erzeugen").click()
                            code = jarvis.locator(".notice code").inner_text()
                            source = context.new_page()
                            source.goto("http://127.0.0.1:8766/", wait_until="domcontentloaded")
                            source.evaluate("""() => { const range = document.createRange();
                                range.selectNodeContents(document.querySelector('#selected'));
                                const selection = window.getSelection();
                                selection.removeAllRanges(); selection.addRange(range); }""")
                            source.bring_to_front()
                            print(json.dumps({"ready": True, "pairing_code": code,
                                              "extension_id": extension_id,
                                              "source": "http://127.0.0.1:8766/"}), flush=True)
                            input("Nach dem echten Toolbar-Klick und Speichern Enter drücken: ")
                            tasks = TaskStore(Database(data_dir / "jarvis.db")).list_tasks()
                            assert len(tasks) == 1, tasks
                            assert tasks[0]["source_url"] == "http://127.0.0.1:8766/"
                            assert "Frist: Montag" in tasks[0]["notes"]
                            assert "Erfahrung mit Python erforderlich." in tasks[0]["notes"]
                            print(json.dumps({"toolbar_capture": "succeeded", "tasks": len(tasks),
                                              "selected_text": True, "page_text": True}), flush=True)
                        finally:
                            context.close()
                finally:
                    stop_test_server(process)
    finally:
        source_server.shutdown()
        source_server.server_close()


if __name__ == "__main__":
    main()
