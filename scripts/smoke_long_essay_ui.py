"""Echter Edge/Ollama-Klickpfad für einen längeren gegliederten Essay."""

import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
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
    with TemporaryDirectory(prefix="jarvis-long-essay-ui-") as temporary:
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
                        page.set_default_timeout(3_000_000)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        page.get_by_label("Textsorte für Stilprofil").select_option("essay")
                        page.get_by_label("Stilprofil für Textsorte").fill(
                            "Ich schreibe in klaren Absätzen, konkret und ohne Floskeln. "
                            "Jeder Abschnitt erklärt einen Schritt und führt zum nächsten.")
                        page.get_by_role("button", name="Textsorte speichern", exact=True).click()
                        expect(page.get_by_text("Stilprofil für Textsorte gespeichert.")).to_be_visible()
                        page.get_by_label("Entwurfstyp").select_option("essay")
                        page.get_by_label("Schreibauftrag").fill(
                            "Schreibe einen ausführlichen Essay darüber, wie ein selbst gewähltes "
                            "Projekt geplant, umgesetzt und überprüft werden kann. Verwende keine "
                            "externen Fakten oder Zitate; erkläre praktische Entscheidungen und "
                            "kennzeichne offene Annahmen.")
                        page.get_by_label("Essay-Wortziel").fill("1200")
                        page.get_by_label("Essay-Gliederung").fill(
                            "Ausgangspunkt\nZiel und Umfang\nArbeitsphasen\nZusammenarbeit\n"
                            "Prüfen und Anpassen\nSchluss")
                        assert not overflow_nodes(page), overflow_nodes(page)
                        started = time.monotonic()
                        page.get_by_role("button", name="Entwurf erstellen", exact=True).click()
                        editor = page.get_by_label("Entwurfstext")
                        expect(editor).to_be_visible(timeout=3_000_000)
                        elapsed = round(time.monotonic() - started, 1)
                        store = DraftStore(Database(data / "jarvis.db"), data)
                        draft = store.drafts()[0]
                        print(json.dumps({"phase": "model_complete", "actual_words": draft["actual_words"],
                                          "status": draft["status"],
                                          "outline": draft["outline"],
                                          "markdown_headings": [line for line in draft["content"].splitlines()
                                                                if line.startswith("## ")],
                                          "uncertainty": draft["uncertainty"]},
                                         ensure_ascii=False), flush=True)
                        assert draft["kind"] == "essay" and draft["word_target"] == 1200
                        assert len(draft["outline"]) == 6
                        assert all(f"## {heading}\n" in draft["content"] for heading in draft["outline"])
                        assert draft["style_snapshot"].startswith("Ich schreibe in klaren Absätzen")
                        assert draft["status"] == "editing", draft["uncertainty"]
                        assert draft["actual_words"] >= 960, draft["uncertainty"]
                        assert "Vorabschnittstext" in draft["uncertainty"]
                        with closing(sqlite3.connect(data / "jarvis.db")) as connection:
                            calls = connection.execute(
                                "SELECT COUNT(*) FROM model_usage WHERE purpose='draft'").fetchone()[0]
                        assert 6 <= calls <= 8
                        assert f"Modellaufrufe: {calls} von höchstens 8 versucht." in draft["uncertainty"]
                        expect(page.get_by_text("Wortziel 1200", exact=False)).to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        editor.fill(editor.input_value() + "\n\nEigene Schlusskorrektur.")
                        page.get_by_role("button", name="Änderung speichern").click()
                        expect(page.get_by_text("Änderung gespeichert.")).to_be_visible()
                        page.get_by_role("button", name="Markdown speichern").click()
                        expect(page.get_by_text("Gespeichert:", exact=False)).to_be_visible()
                        saved = data / "outputs" / f"{draft['id']}.md"
                        assert "Eigene Schlusskorrektur." in saved.read_text(encoding="utf-8")
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        page.get_by_role("heading", name="Gespeicherte Entwürfe").locator("..").get_by_role(
                            "button", name="essay:", exact=False).click()
                        expect(page.get_by_text("Wortziel 1200", exact=False)).to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"target_words": 1200,
                                          "actual_words": draft["actual_words"],
                                          "model_calls": calls, "elapsed_seconds": elapsed,
                                          "status": draft["status"],
                                          "uncertainty": draft["uncertainty"],
                                          "edited_export": True, "reload": True,
                                          "mobile_width": "fits"}, ensure_ascii=False))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
