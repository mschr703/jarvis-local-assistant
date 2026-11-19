"""Echter Edge/Ollama-Klicktest für einen getrennten begrenzten Nachlauf."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, stop_test_server, wait_ready

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database
from jarvis.research import ResearchStore


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-research-follow-up-") as temporary:
        root = Path(temporary)
        data = root / "data"
        store = ResearchStore(Database(data / "jarvis.db"))
        original = store.save("Welche Instrumente hat Europa Clipper?", "Europa Clipper",
                              "Fixture", [{"id": 1, "url": "https://example.org/ursprung",
                                           "title": "Ursprungsquelle", "text": "Die Mission untersucht Europa. " * 6,
                                           "retrieved_at": "2026-10-27T08:00:00Z"}], [],
                              "Die Mission untersucht Europa.", "Instrumente sind noch offen.", [],
                              "ollama", "qwen3:4b-instruct")
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct", JARVIS_TIMEZONE="Europe/Paris")
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
                        report = page.get_by_role("region", name="Recherchebericht")
                        expect(report.get_by_role("heading", name=original["question"])).to_be_visible()
                        follow_up = page.get_by_role("region", name="Begrenzte Nachrecherche")
                        assert follow_up.count() == 1
                        assert follow_up.locator("textarea").count() == 1
                        follow_up.locator("textarea").fill(
                            "Welche Kamera trägt Europa Clipper für die Untersuchung von Europa?")
                        assert follow_up.locator("input").count() == 1
                        follow_up.locator("input").fill(
                            "Europa Clipper camera instrument NASA")
                        assert not overflow_nodes(page), overflow_nodes(page)
                        follow_up.get_by_role("button", name="Nachrecherche starten").click()
                        expect(report.get_by_role("heading", name="Welche Kamera trägt Europa Clipper für die Untersuchung von Europa?")).to_be_visible(timeout=240_000)
                        saved = store.list()[0]
                        assert saved["parent_report_id"] == original["id"]
                        assert len(saved["sources"]) == 1, saved["errors"]
                        assert saved["interpretation"], saved["errors"]
                        assert store.get(original["id"]) == original
                        assert not overflow_nodes(page), overflow_nodes(page)
                        report.get_by_role("button", name="Ursprungsbericht öffnen").click()
                        expect(report.get_by_role("heading", name=original["question"])).to_be_visible()
                        trail = report.get_by_role("region", name="Nachrechercheverlauf")
                        expect(trail.get_by_role("button", name="Nachlauf öffnen")).to_have_count(1)
                        assert "Europa Clipper camera instrument NASA" in trail.inner_text()
                        trail.get_by_role("button", name="Nachlauf öffnen").click()
                        expect(report.get_by_role("heading", name="Welche Kamera trägt Europa Clipper für die Untersuchung von Europa?")).to_be_visible()
                        report.get_by_role("button", name="Ursprungsbericht öffnen").click()
                        expect(report.get_by_role("heading", name=original["question"])).to_be_visible()
                        page.reload(wait_until="domcontentloaded")
                        page.locator("nav button").filter(has_text="Recherche").click()
                        expect(page.get_by_role("button", name="Welche Kamera trägt Europa Clipper", exact=False)).to_be_visible()
                        print(json.dumps({"source_count": len(saved["sources"]),
                                          "model_answer": bool(saved["interpretation"]),
                                          "parent_unchanged": True, "navigation": "both_directions_passed",
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
