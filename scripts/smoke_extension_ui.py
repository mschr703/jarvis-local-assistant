"""Echte MV3-Popup-Übernahme mit isoliertem Jarvis und lokalem Quelltab."""

import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from smoke_ui import ROOT, port_free, stop_test_server, wait_ready

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database
from jarvis.tasks import TaskStore


class SourceHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (b"<!doctype html><title>Test-Ausschreibung</title>"
                b"<h1>Test-Ausschreibung</h1><p id='selected'>Frist: Montag</p>"
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
        with TemporaryDirectory(prefix="jarvis-extension-ui-") as temporary:
            root = Path(temporary)
            extension = root / "extension-test"
            shutil.copytree(ROOT / "extension", extension)
            manifest_path = extension / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            # Nur der Testkopie Zugriff auf die lokale Quellseite geben: Playwright
            # öffnet Popups direkt und kann keinen echten activeTab-Klick auslösen.
            manifest["permissions"].append("tabs")
            manifest["host_permissions"].append("http://127.0.0.1:8766/*")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            environment = os.environ.copy()
            environment.update(JARVIS_DATA_DIR=str(root / "data"), JARVIS_PROVIDER="ollama",
                               JARVIS_OLLAMA_MODEL="qwen3:4b-instruct",
                               JARVIS_TIMEZONE="Europe/Paris")
            with (root / "server.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                                           cwd=ROOT, env=environment, stdout=log,
                                           stderr=subprocess.STDOUT)
                try:
                    wait_ready(process, timeout=120)
                    with sync_playwright() as playwright:
                        context = playwright.chromium.launch_persistent_context(
                            str(root / "browser"), channel="chromium", headless=True,
                            args=[f"--disable-extensions-except={extension}",
                                  f"--load-extension={extension}"],
                            viewport={"width": 390, "height": 844})
                        try:
                            worker = (context.service_workers[0] if context.service_workers else
                                      context.wait_for_event("serviceworker", timeout=20_000))
                            extension_id = worker.url.split("/")[2]
                            jarvis = context.new_page()
                            jarvis.set_default_timeout(30_000)
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
                            hostile = source.evaluate("""async () => {
                                try { await fetch('http://127.0.0.1:8765/extension/capture', {
                                    method: 'POST', headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({title: 'Fremde Seite'}) });
                                    return 'unexpected';
                                } catch { return 'blocked'; }
                            }""")
                            assert hostile == "blocked"
                            assert TaskStore(Database(root / "data" / "jarvis.db")).list_tasks() == []
                            popup = context.new_page()
                            popup.goto(f"chrome-extension://{extension_id}/popup.html")
                            popup.set_default_timeout(30_000)
                            expect(popup.get_by_role("heading", name="Tab an Jarvis")).to_be_visible()
                            popup.get_by_label("Kopplungscode").fill(code)
                            popup.get_by_role("button", name="Koppeln").click()
                            expect(popup.locator("#selection")).to_have_value("Frist: Montag")
                            expect(popup.locator("#source-url")).to_have_text("http://127.0.0.1:8766/")
                            popup.get_by_label("Aufgabe").fill("Ausschreibung einordnen")
                            popup.get_by_label("Seitentext zusätzlich übernehmen", exact=False).check()
                            expect(popup.locator("#page-preview")).to_contain_text("Erfahrung mit Python")
                            popup.get_by_role("button", name="Als Aufgabe speichern").click()
                            expect(popup.get_by_text("Aufgabe mit Quellenlink gespeichert.")).to_be_visible()
                            popup.get_by_role("button", name="Als Aufgabe speichern").click()
                            expect(popup.get_by_text("Diese Aufgabe war bereits gespeichert.")).to_be_visible()
                            tasks = TaskStore(Database(root / "data" / "jarvis.db")).list_tasks()
                            assert len(tasks) == 1
                            assert tasks[0]["source_url"] == "http://127.0.0.1:8766/"
                            assert "Frist: Montag" in tasks[0]["notes"]
                            assert "Erfahrung mit Python" in tasks[0]["notes"]
                            with Database(root / "data" / "jarvis.db").connect() as db:
                                assert db.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                            jarvis.bring_to_front()
                            jarvis.locator("nav button").filter(has_text="Aufgaben").click()
                            expect(jarvis.get_by_text("Ausschreibung einordnen")).to_be_visible()
                            expect(jarvis.get_by_role("link", name="Quellenlink öffnen")).to_have_attribute(
                                "href", "http://127.0.0.1:8766/")
                            assert jarvis.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                            print(json.dumps({"extension_id": extension_id, "tasks": len(tasks),
                                              "selected_text": True, "page_text": True,
                                              "duplicate": False, "mobile_width": "fits"}))
                        finally:
                            context.close()
                finally:
                    stop_test_server(process)
    finally:
        source_server.shutdown()
        source_server.server_close()


if __name__ == "__main__":
    main()
