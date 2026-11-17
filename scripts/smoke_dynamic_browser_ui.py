"""Real Edge check: explicit dynamic page, screenshot, mobile layout and stop."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from smoke_ui import ROOT, port_free, stop_test_server, wait_ready

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Browser-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-dynamic-browser-ui-") as temporary:
        root = Path(temporary)
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
                    browser = playwright.chromium.launch(channel="msedge", headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 390, "height": 844})
                        page.set_default_timeout(180_000)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.locator("nav button").filter(has_text="Aktivität").click()
                        page.get_by_label("Werkzeug", exact=True).select_option("browser_dynamic_read")
                        url = page.get_by_label("Öffentliche URL")
                        url.fill("https://todomvc.com/examples/react/dist/")
                        expect(page.get_by_text("Geplante Aktion:", exact=False)).to_contain_text(
                            "todomvc.com")
                        page.get_by_role("button", name="Im Browser lesen").click()
                        row = page.locator(".item-row").filter(has_text="browser_dynamic_read").first
                        expect(row.get_by_text("browser_dynamic_read · succeeded", exact=False)).to_be_visible(
                            timeout=180_000)
                        expect(row.get_by_text("TodoMVC: React", exact=False)).to_be_visible()
                        screenshot = row.get_by_alt_text("Screenshot der gelesenen öffentlichen Seite")
                        expect(screenshot).to_be_visible()
                        page.wait_for_function("""() => {
                          const img = document.querySelector('img[alt="Screenshot der gelesenen öffentlichen Seite"]');
                          return img && img.naturalWidth > 0;
                        }""")
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), \
                            "390px horizontal overflow"
                        url.fill("https://todomvc.com/examples/vue/dist/")
                        page.get_by_role("button", name="Im Browser lesen").click()
                        running = page.locator(".item-row").filter(has_text="browser_dynamic_read").first
                        expect(running.get_by_role("button", name="Stoppen")).to_be_visible(timeout=30_000)
                        running.get_by_role("button", name="Stoppen").click()
                        expect(running.get_by_text("browser_dynamic_read · cancelled", exact=False)).to_be_visible(
                            timeout=30_000)
                        runs = Database(root / "data" / "jarvis.db").list_runs()
                        assert any(item["status"] == "succeeded" for item in runs)
                        assert any(item["status"] == "cancelled" for item in runs)
                        print(json.dumps({"dynamic_browser": True, "screenshot": True,
                                          "stop": True, "edge_390px": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
