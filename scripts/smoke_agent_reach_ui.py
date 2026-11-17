"""Real Edge/Ollama end-to-end check for public Agent Reach video and GitHub sources."""

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


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-agent-reach-ui-") as temporary:
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
                            "Welche Themen behandeln die beiden angegebenen Quellen?")
                        page.get_by_label("Eigene öffentliche Quellenlinks (optional, eine URL pro Zeile, höchstens zwei)").fill(
                            "https://www.youtube.com/watch?v=aircAruvnKk\n"
                            "https://github.com/Panniantong/Agent-Reach")
                        page.get_by_role("button", name="Recherche starten").click()
                        report = page.get_by_role("region", name="Recherchebericht")
                        expect(report).to_be_visible(timeout=240_000)
                        expect(report.get_by_text("YouTube-Video", exact=False).first).to_be_visible()
                        expect(report.get_by_text("GitHub", exact=False).first).to_be_visible()
                        stored = ResearchStore(Database(root / "data" / "jarvis.db")).list()
                        assert len(stored) == 1
                        sources = stored[0]["sources"]
                        assert len(sources) == 2, stored[0]["errors"]
                        assert {source["media_type"] for source in sources} == {"video", "github"}
                        assert any("Untertitel:" in source["text"] for source in sources)
                        assert all(len(source["text"]) <= 1500 for source in sources)
                        assert all(source["url"].startswith("https://") for source in sources)
                        assert stored[0]["interpretation"], stored[0]["errors"]
                        overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth")
                        assert not overflow, "390px horizontal overflow"
                        print(json.dumps({"real_agent_reach": True, "source_types": [
                            source["media_type"] for source in sources],
                            "caption_text": True, "model_answer": True,
                            "edge_390px": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
