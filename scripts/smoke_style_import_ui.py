"""Echter Edge/Ollama-Klickpfad: eigene indexierte Datei als lokale Stilprobe."""

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
        raise RuntimeError("Jarvis vor dem isolierten Stilimport-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-style-import-ui-") as temporary:
        root = Path(temporary)
        data = root / "data"
        source = root / "eigene-texte"
        source.mkdir()
        path = source / "Mein Stil.md"
        original = "Ich schreibe kurz und direkt. Jeder Absatz verfolgt einen Gedanken. " * 5
        path.write_text(original, encoding="utf-8")
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
                        page.set_default_timeout(180_000)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_label("Absoluter Ordnerpfad").fill(str(source))
                        page.get_by_role("button", name="Freigeben", exact=True).click()
                        expect(page.get_by_text("Ordner freigegeben.", exact=False)).to_be_visible(
                            timeout=120_000)
                        page.get_by_role("button", name="Index aktualisieren").click()
                        expect(page.get_by_text("Index aktualisiert:", exact=False)).to_be_visible(
                            timeout=120_000)
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        selector = page.get_by_label("Datei für Schreibprobe")
                        expect(selector.locator("option")).to_have_count(2, timeout=120_000)
                        selector.select_option(label=str(path))
                        assert page.get_by_label("Titel der importierten Schreibprobe").input_value() == "Mein Stil"
                        page.get_by_label("Ich habe diesen Text selbst verfasst", exact=False).check()
                        updated = original + "Ein zusätzlicher eigener Satz."
                        path.write_text(updated, encoding="utf-8")
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_role("button", name="Index aktualisieren").click()
                        expect(page.get_by_text("Index aktualisiert:", exact=False)).to_be_visible(
                            timeout=120_000)
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        expect(page.get_by_text("hat sich seit der Auswahl geändert", exact=False)).to_be_visible(
                            timeout=120_000)
                        expect(page.get_by_role("button", name="Eigene Datei übernehmen")).to_be_disabled()
                        selector = page.get_by_label("Datei für Schreibprobe")
                        selector.select_option(value="")
                        selector.select_option(label=str(path))
                        page.get_by_label("Ich habe diesen Text selbst verfasst", exact=False).check()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.get_by_role("button", name="Eigene Datei übernehmen").click()
                        expect(page.get_by_text("Eigene Datei als lokale Schreibprobe übernommen.",
                                                exact=False)).to_be_visible(timeout=120_000)
                        store = DraftStore(Database(data / "jarvis.db"), data)
                        samples = store.samples()
                        assert len(samples) == 1 and samples[0]["content"] == updated
                        assert samples[0]["policy"] == "local_only"
                        assert samples[0]["source_path"] == str(path)
                        assert path.read_text(encoding="utf-8") == updated
                        page.get_by_label("Schreibauftrag").fill("Schreibe zwei kurze Sätze über gute Planung.")
                        with page.expect_request(lambda request: request.url.endswith("/drafts/generate"),
                                                 timeout=180_000) as posted:
                            page.get_by_role("button", name="Entwurf erstellen", exact=True).click()
                        assert samples[0]["id"] in posted.value.post_data_json["style_sample_ids"]
                        expect(page.get_by_text("Entwurf erstellt. Prüfe und bearbeite", exact=False)).to_be_visible(
                            timeout=180_000)
                        draft = store.drafts()[0]
                        assert draft["kind"] == "email" and draft["content"].strip()
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        expect(page.get_by_text("SHA-256", exact=False)).to_be_visible(timeout=120_000)
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"indexed_file_imported": True, "stale_selection_blocked": True,
                                          "local_sample_used": True,
                                          "reload": True, "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
