"""Search confirmed life events by text and linked photo name in real Edge."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image
from playwright.sync_api import expect, sync_playwright
from smoke_code_project_ui import wait_ui
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, stop_test_server

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Lebensereignis-Suchtest stoppen")
    with TemporaryDirectory(prefix="jarvis-life-search-ui-") as temporary:
        root = Path(temporary)
        folder = root / "Fotos"
        folder.mkdir()
        Image.new("RGB", (35, 25), "red").save(folder / "weekend.jpg")
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
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Lebenschronik", exact=True).click()
                        page.get_by_label("Ereignisdatum").fill("2024-08-12")
                        page.get_by_label("Ereignistitel").fill("Café in Lyon")
                        page.get_by_label("Ereignisbeschreibung").fill("Mit Alex am Fluss")
                        page.get_by_role("button", name="Ereignis speichern").click()
                        expect(page.get_by_text("2024-08-12 · Café in Lyon")).to_be_visible()
                        page.get_by_label("Absoluter Fotoordnerpfad").fill(str(folder))
                        page.get_by_role("button", name="Fotoordner freigeben").click()
                        page.get_by_role("button", name="Fotos einlesen").click()
                        photo = page.locator(".photo-item").filter(has_text="weekend.jpg")
                        photo.get_by_label("Ereignis für weekend.jpg").select_option(
                            label="2024-08-12 · Café in Lyon")
                        photo.get_by_role("button", name="Zuordnung speichern").click()
                        expect(page.get_by_role("heading", name="Alben nach Ereignis")).to_be_visible()

                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Quelltyp der gemeinsamen Suche").select_option("life_event")
                        expect(page.get_by_role("button", name="Sinngemäß lokal suchen")).to_be_disabled()
                        page.get_by_label("Gemeinsame Suche").fill("cafe")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        results = page.get_by_role("region", name="Suchergebnisse")
                        expect(results).to_contain_text("Café in Lyon")
                        expect(results).to_contain_text("1 Lebensereignisse")
                        page.get_by_label("Gemeinsame Suche").fill("weekend")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        expect(results).to_contain_text("Foto: weekend.jpg")
                        assert str(folder) not in results.inner_text()
                        results.get_by_role("button", name="Quelle öffnen").click()
                        opened = page.get_by_role("region", name="Geöffnetes Lebensereignis")
                        expect(opened).to_contain_text("Café in Lyon")
                        expect(opened).to_contain_text("Mit Alex am Fluss")
                        assert not overflow_nodes(page), overflow_nodes(page)
                        db = Database(Path(environment["JARVIS_DATA_DIR"]) / "jarvis.db")
                        with db.connect() as connection:
                            assert connection.execute("SELECT count(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"text": "accent_insensitive", "photo_name": "found",
                                          "source": "opened", "model_usage": 0,
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
