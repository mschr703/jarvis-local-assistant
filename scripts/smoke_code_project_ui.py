"""Isolated Edge path for a granted local code project and file evidence."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, stop_test_server

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database
from jarvis.documents import DocumentStore


def wait_ui(process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 240
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Jarvis-Testserver wurde vor dem Start beendet")
        try:
            with urlopen("http://127.0.0.1:8765/session", timeout=2):
                pass
            with urlopen("http://127.0.0.1:5173/", timeout=2):
                return
        except OSError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(.5)
    raise RuntimeError(f"Jarvis-Testserver oder Oberfläche wurde nicht bereit: {last_error}")


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-code-project-ui-") as temporary:
        root = Path(temporary)
        project = root / "example"
        (project / ".git").mkdir(parents=True)
        (project / "src").mkdir()
        (project / "tests").mkdir()
        (project / "src" / "main.py").write_text("print('Hallo Projekt')\n", encoding="utf-8")
        (project / "tests" / "test_main.py").write_text("def test_main():\n    assert True\n",
                                                        encoding="utf-8")
        (project / "pyproject.toml").write_text("[project]\nname='example'\n",
                                                 encoding="utf-8")
        data = root / "data"
        folder = DocumentStore(Database(data / "jarvis.db")).add_folder(str(project),
                                                                          "local_only")
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct")
        with (root / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                                       cwd=ROOT, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT)
            try:
                wait_ui(process)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge", headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 390, "height": 844})
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded",
                                  timeout=120000)
                        page.get_by_role("button", name="Codeprojekte", exact=True).click()
                        expect(page.get_by_label("Codeprojekt-Ordner")).to_have_value(folder["id"],
                                                                                       timeout=30000)
                        page.get_by_role("button", name="Projekt überblicken").click()
                        overview = page.get_by_role("region", name="Projektüberblick")
                        expect(overview.get_by_text("Lokaler Git-Checkout erkannt", exact=False)).to_be_visible(timeout=30000)
                        expect(overview.get_by_text("Mögliche Einstiegspunkte (1)")).to_be_visible()
                        expect(overview.get_by_text("Tests (1)")).to_be_visible()
                        expect(overview.get_by_text("Konfiguration (1)")).to_be_visible()
                        row = overview.get_by_text("src/main.py", exact=True).locator("..")
                        row.get_by_role("button", name="Datei ansehen").click()
                        preview = page.get_by_role("region", name="Projektdatei")
                        expect(preview.get_by_text("1: print('Hallo Projekt')", exact=False)).to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"inventory": "source_backed", "preview": "line_numbered",
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
