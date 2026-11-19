"""Exercise granting, indexing, searching and refreshing a DOCX in Edge."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from playwright.sync_api import expect, sync_playwright
from smoke_code_project_ui import wait_ui
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, stop_test_server


def write_docx(path: Path, text: str) -> None:
    xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/'
           'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
           f'{text}</w:t></w:r></w:p></w:body></w:document>')
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", xml)


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-docx-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "Freigabe"
        allowed.mkdir()
        file = allowed / "Wohnung.docx"
        write_docx(file, "Kaution für die Wohnung in München")
        data = root / "data"
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama",
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
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded",
                                  timeout=120000)
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_label("Absoluter Ordnerpfad").fill(str(allowed))
                        page.get_by_role("button", name="Freigeben", exact=True).click()
                        index = page.get_by_role("button", name="Index aktualisieren")
                        expect(index).to_be_visible(timeout=20000)
                        index.click()
                        expect(page.get_by_text("Index aktualisiert:", exact=False)).to_be_visible(
                            timeout=30000)
                        expect(page.get_by_text("Wohnung.docx", exact=False).first).to_be_visible()
                        page.get_by_label("Dokumente durchsuchen").fill("Kaution")
                        page.get_by_role("button", name="Suchen", exact=True).click()
                        expect(page.locator(".source-result")).to_have_count(1, timeout=30000)
                        assert "Wohnung.docx" in page.locator(".source-result").inner_text()
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Gemeinsame Suche").fill("München")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        results = page.get_by_role("region", name="Suchergebnisse")
                        expect(results.get_by_text("1 Dokumente · 0 Erinnerungen · 0 Arbeitsstände")).to_be_visible(
                            timeout=30000)
                        assert not overflow_nodes(page), overflow_nodes(page)

                        write_docx(file, "Nebenkosten für die Wohnung in Berlin")
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_role("button", name="Index aktualisieren").click()
                        expect(page.get_by_text("Index aktualisiert:", exact=False)).to_be_visible(
                            timeout=30000)
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Gemeinsame Suche").fill("München")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        expect(results.get_by_text("Keine passenden gespeicherten Quellen gefunden.")).to_be_visible(
                            timeout=30000)
                        page.get_by_label("Gemeinsame Suche").fill("Berlin")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        expect(results.get_by_text("1 Dokumente · 0 Erinnerungen · 0 Arbeitsstände")).to_be_visible(
                            timeout=30000)
                        print(json.dumps({"docx": "indexed", "refresh": "current",
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
