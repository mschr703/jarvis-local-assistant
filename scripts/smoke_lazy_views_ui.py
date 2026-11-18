"""Unused React views stay unloaded until opened; visited views keep their state."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from smoke_code_project_ui import wait_ui
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, stop_test_server


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-lazy-views-ui-") as temporary:
        root = Path(temporary)
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(root / "data"), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="fixture")
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
                        scripts = []
                        page.on("request", lambda request: scripts.append(request.url)
                                if "/src/" in request.url else None)
                        page.goto("http://127.0.0.1:5173/", wait_until="networkidle")
                        expect(page.get_by_role("heading", name="Jarvis", exact=True)).to_be_visible()
                        assert not any("ChatView.tsx" in url for url in scripts)
                        assert not any("UnifiedSearchView.tsx" in url for url in scripts)
                        page.get_by_role("button", name="Suche", exact=True).click()
                        expect(page.get_by_role("heading", name="Gemeinsame Suche")).to_be_visible()
                        assert any("UnifiedSearchView.tsx" in url for url in scripts)
                        search = page.get_by_label("Gemeinsame Suche")
                        search.fill("Projekt Orion")
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        expect(page.get_by_role("heading", name="Dokumente", exact=True)).to_be_visible()
                        page.get_by_role("button", name="Suche", exact=True).click()
                        expect(search).to_have_value("Projekt Orion")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        expect(page.get_by_label("Nachricht")).to_be_visible()
                        assert any("ChatView.tsx" in url for url in scripts)
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"unused_views_deferred": True,
                                          "opened_views_loaded": True,
                                          "search_state_retained": True,
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
