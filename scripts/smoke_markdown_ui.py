"""Echter Edge-Klicktest für Vorschau, Konfliktschutz und Rückgängig von Markdown."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from smoke_ui import ROOT, port_free, stop_test_server, wait_ready


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-markdown-ui-") as temporary:
        root = Path(temporary)
        sources = root / "quellen"
        sources.mkdir()
        note = sources / "projekt.md"
        original = b"# Projekt\r\nAlte Entscheidung\r\n"
        note.write_bytes(original)
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(root / "data"), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct")
        with (root / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                                       cwd=ROOT, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT)
            try:
                wait_ready(process)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge", headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 390, "height": 800})
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_label("Absoluter Ordnerpfad").fill(str(sources))
                        page.get_by_role("button", name="Freigeben").click()
                        page.get_by_role("button", name="Index aktualisieren").click()
                        page.get_by_role("button", name="Markdown bearbeiten").click()
                        editor = page.get_by_label("Markdown-Notiztext")
                        expect(editor).to_have_value("# Projekt\nAlte Entscheidung\n")
                        editor.fill("# Projekt\nNeue Entscheidung\n")
                        page.get_by_role("button", name="Änderungsvorschau erstellen").click()
                        page.locator(".markdown-diff").get_by_text("+Neue Entscheidung").wait_for()
                        assert note.read_bytes() == original
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                        page.get_by_role("button", name="Gezeigte Änderung speichern").click()
                        page.get_by_text("Markdown-Datei gespeichert und Suchindex aktualisiert.", exact=False).wait_for()
                        assert note.read_bytes() == b"# Projekt\r\nNeue Entscheidung\r\n"
                        page.get_by_role("button", name="Letzte Änderung rückgängig").click()
                        page.get_by_text("Änderung rückgängig gemacht und Suchindex aktualisiert.", exact=True).wait_for()
                        assert note.read_bytes() == original

                        editor.fill("# Projekt\nDritte Entscheidung\n")
                        page.get_by_role("button", name="Änderungsvorschau erstellen").click()
                        page.get_by_role("button", name="Gezeigte Änderung speichern").wait_for()
                        note.write_bytes(b"Extern bearbeitet\r\n")
                        page.get_by_role("button", name="Gezeigte Änderung speichern").click()
                        page.get_by_text("Datei wurde seit der Vorschau geändert", exact=False).wait_for()
                        assert note.read_bytes() == b"Extern bearbeitet\r\n"
                        page.get_by_role("button", name="Schließen", exact=True).click()
                        page.get_by_role("button", name="Ausschließen").click()
                        expect(page.get_by_role("button", name="Markdown bearbeiten")).to_have_count(0)
                        print(json.dumps({"preview": "visible", "save": "indexed",
                                          "undo": "restored", "conflict": "external_file_preserved",
                                          "excluded": "blocked", "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
