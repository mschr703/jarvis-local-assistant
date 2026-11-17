"""Echter Edge/Ollama-Klicktest für gegliederten, editierbaren Essay-Entwurf."""

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
from jarvis.drafts import DraftStore


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Essay-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-essay-ui-") as temporary:
        root = Path(temporary)
        data = root / "data"
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama",
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
                        page.set_default_timeout(900_000)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        page.get_by_label("Stilprofil", exact=True).fill(
                            "Schreibe klar, sachlich und ohne Floskeln. Kurze Absätze.")
                        page.get_by_role("button", name="Profil speichern").click()
                        expect(page.get_by_text("Stilprofil gespeichert.")).to_be_visible()
                        page.get_by_label("Entwurfstyp").select_option("essay")
                        page.get_by_label("Schreibauftrag").fill(
                            "Schreibe einen kurzen Essay über die Planung einer Europa-Mission. "
                            "Kennzeichne unsichere Sachangaben.")
                        page.get_by_label("Essay-Wortziel").fill("300")
                        page.get_by_label("Essay-Gliederung").fill("Einleitung\nSchlussfolgerung")
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.get_by_role("button", name="Entwurf erstellen", exact=True).click()
                        editor = page.get_by_label("Entwurfstext")
                        expect(editor).to_be_visible(timeout=900_000)
                        draft = DraftStore(Database(data / "jarvis.db"), data).drafts()[0]
                        assert draft["kind"] == "essay" and draft["word_target"] == 300
                        assert draft["outline"] == ["Einleitung", "Schlussfolgerung"]
                        assert draft["status"] == "editing", draft["uncertainty"]
                        assert "## Einleitung" in draft["content"]
                        assert "## Schlussfolgerung" in draft["content"]
                        assert draft["actual_words"] > 20
                        assert "begrenzte Ergänzungsaufrufe" in draft["uncertainty"]
                        expect(page.get_by_text("Wortziel 300", exact=False)).to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        editor.fill(editor.input_value() + "\n\nEigene Korrektur.")
                        page.get_by_role("button", name="Änderung speichern").click()
                        expect(page.get_by_text("Änderung gespeichert.")).to_be_visible()
                        page.get_by_role("button", name="Markdown speichern").click()
                        saved = data / "outputs" / f"{draft['id']}.md"
                        expect(page.get_by_text("Gespeichert:", exact=False)).to_be_visible()
                        assert saved.exists()
                        assert "Eigene Korrektur." in saved.read_text(encoding="utf-8")
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        page.get_by_role("heading", name="Gespeicherte Entwürfe").locator("..").get_by_role(
                            "button", name="essay:", exact=False).click()
                        expect(page.get_by_text("Wortziel 300", exact=False)).to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"outline_sections": 2, "actual_words": draft["actual_words"],
                                          "edited_export": True, "reload": True,
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
