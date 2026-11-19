"""Echter Edge/Ollama-Klicktest für Websuche, Quellen und gespeicherten Bericht."""

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
from jarvis.research import ResearchStore


def overflow_nodes(page) -> list[dict]:
    return page.evaluate("""() => [...document.querySelectorAll('*')]
      .filter(e => e.getBoundingClientRect().right > innerWidth + 1)
      .slice(0, 12).map(e => ({tag:e.tagName,cls:e.className,
        right:Math.round(e.getBoundingClientRect().right),text:e.textContent.slice(0,90)}))""")


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Recherche-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-research-ui-") as temporary:
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
                        page.set_default_timeout(240_000)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.locator("nav button").filter(has_text="Recherche").click()
                        page.get_by_label("Frage", exact=True).fill(
                            "Was ist das Ziel der NASA-Mission Europa Clipper?")
                        page.get_by_label("Suchanfrage (optional)").fill("Europa Clipper NASA mission")
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.get_by_role("button", name="Recherche starten").click()
                        report = page.get_by_role("region", name="Recherchebericht")
                        page.wait_for_function("""() => document.querySelector('[aria-label="Recherchebericht"]')
                            || document.querySelector('[role="alert"]')""", timeout=240_000)
                        alert = page.get_by_role("alert")
                        if alert.is_visible():
                            raise RuntimeError(f"Recherche-API: {alert.inner_text()}")
                        expect(report).to_be_visible(timeout=240_000)
                        expect(report.get_by_role("heading", name="Gelesene Quellen")).to_be_visible()
                        sources = report.locator("a[href^='https://']")
                        assert sources.count() >= 1
                        assert "Abruf:" in report.inner_text()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        stored = ResearchStore(Database(root / "data" / "jarvis.db")).list()
                        assert len(stored) == 1
                        assert stored[0]["sources"]
                        assert stored[0]["interpretation"], stored[0]["errors"]
                        assert not [error for error in stored[0]["errors"]
                                    if "Modell" in error], stored[0]["errors"]
                        print(json.dumps({"search_provider": stored[0]["search_provider"],
                                          "sources": len(stored[0]["sources"]),
                                          "verified_claims": len(stored[0]["claims"]),
                                          "model_answer": bool(stored[0]["interpretation"]),
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
