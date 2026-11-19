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
                        page.get_by_role("button", name="Kontakte", exact=True).click()
                        for context in ("Lyon privat", "Arbeit"):
                            page.get_by_label("Kontaktname").fill("Alex")
                            page.get_by_label("Unterscheidender Kontext").fill(context)
                            page.get_by_role("button", name="Kontakt anlegen").click()
                            expect(page.get_by_role("heading", name=f"Alex · {context}")).to_be_visible()
                        page.get_by_role("button", name="Lebenschronik", exact=True).click()
                        page.get_by_label("Ereignisdatum").fill("2024-08-12")
                        page.get_by_label("Ereignistitel").fill("Café in Lyon")
                        page.get_by_label("Ereignisbeschreibung").fill("Mit Alex am Fluss")
                        page.get_by_role("button", name="Ereignis speichern").click()
                        expect(page.get_by_text("2024-08-12 · Café in Lyon")).to_be_visible()
                        page.get_by_label("Kontakte für Ereignisse suchen").fill("Lyon privat")
                        page.get_by_role("button", name="Kontakte finden").click()
                        event_row = page.locator(".item-row").filter(has_text="Café in Lyon")
                        expect(event_row.get_by_label("Kontakt für Ereignis Café in Lyon")
                               .locator("option")).to_have_count(2)
                        event_row.get_by_label("Kontakt für Ereignis Café in Lyon").select_option(
                            label="Alex · Lyon privat")
                        event_row.get_by_role("button", name="Kontakt zuordnen").click()
                        expect(event_row).to_contain_text("Alex · Lyon privat · manuell bestätigt")
                        expect(event_row).not_to_contain_text("Alex · Arbeit · manuell bestätigt")
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
                        page.get_by_label("Gemeinsame Suche").fill("cafe")
                        expect(page.get_by_role("button", name="Sinngemäß lokal suchen")).to_be_enabled()
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        results = page.get_by_role("region", name="Suchergebnisse")
                        expect(results).to_contain_text("Café in Lyon")
                        expect(results).to_contain_text("1 Lebensereignisse")
                        page.get_by_label("Gemeinsame Suche").fill("weekend")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        expect(results).to_contain_text("Foto: weekend.jpg")
                        page.get_by_label("Gemeinsame Suche").fill("Lyon privat")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        expect(results).to_contain_text("Kontakt: Alex (Lyon privat)")
                        page.get_by_label("Gemeinsame Suche").fill("Arbeit")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        expect(results).to_contain_text("0 Lebensereignisse")
                        page.get_by_label("Gemeinsame Suche").fill("Lyon privat")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        assert str(folder) not in results.inner_text()
                        results.get_by_role("button", name="Quelle öffnen").click()
                        opened = page.get_by_role("region", name="Geöffnetes Lebensereignis")
                        expect(opened).to_contain_text("Café in Lyon")
                        expect(opened).to_contain_text("Mit Alex am Fluss")
                        expect(opened).to_contain_text("Alex · Lyon privat · manuell bestätigt")
                        opened.get_by_role("button", name="Kontakt Alex (Lyon privat) öffnen").click()
                        expect(page.get_by_role("heading", name="Alex · Lyon privat")).to_be_visible()
                        expect(page.get_by_role("heading", name="Alex · Arbeit")).not_to_be_visible()
                        page.get_by_role("button", name="Lebenschronik", exact=True).click()
                        event_row = page.locator(".item-row").filter(has_text="Café in Lyon")
                        event_row.get_by_role("button", name="Kontakt Alex (Lyon privat) öffnen").click()
                        expect(page.get_by_role("heading", name="Alex · Lyon privat")).to_be_visible()
                        page.get_by_role("button", name="Suche", exact=True).click()
                        page.get_by_label("Quelltyp der gemeinsamen Suche").select_option("contact")
                        page.get_by_label("Gemeinsame Suche").fill("Lyon privat")
                        expect(page.get_by_role("button", name="Sinngemäß lokal suchen")).to_be_disabled()
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        results = page.get_by_role("region", name="Suchergebnisse")
                        expect(results).to_contain_text("1 Kontakt")
                        expect(results).to_contain_text("Kontakt · Alex · Lyon privat")
                        expect(results).not_to_contain_text("Alex · Arbeit")
                        results.get_by_role("button", name="Quelle öffnen").click()
                        expect(page.get_by_role("heading", name="Alex · Lyon privat")).to_be_visible()
                        page.get_by_role("button", name="Kontakte", exact=True).click()
                        contact_list = page.locator("section.card").filter(
                            has=page.get_by_role("heading", name="Kontakt suchen"))
                        contact_list.locator(".item-row").filter(has_text="Lyon privat")\
                            .get_by_role("button", name="Öffnen").click()
                        contact_detail = page.locator("section.card").filter(
                            has=page.get_by_role("heading", name="Alex · Lyon privat"))
                        expect(contact_detail).to_contain_text("Gemeinsame Lebensereignisse (1)")
                        expect(contact_detail).to_contain_text("Café in Lyon")
                        contact_detail.get_by_role("button", name="Ereignis öffnen").click()
                        expect(opened).to_contain_text("Café in Lyon")
                        opened.get_by_role("button", name="Zuordnung zu Alex (Lyon privat) lösen").click()
                        expect(opened).to_contain_text("Bewusst zugeordnete Kontakte: keine")
                        page.get_by_role("button", name="Kontakte", exact=True).click()
                        expect(contact_detail).to_contain_text("Gemeinsame Lebensereignisse (0)")
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Kontakte", exact=True).click()
                        contact_list = page.locator("section.card").filter(
                            has=page.get_by_role("heading", name="Kontakt suchen"))
                        contact_list.locator(".item-row").filter(has_text="Lyon privat")\
                            .get_by_role("button", name="Öffnen").click()
                        expect(page.get_by_role("heading", name="Gemeinsame Lebensereignisse (0)"))\
                            .to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        db = Database(Path(environment["JARVIS_DATA_DIR"]) / "jarvis.db")
                        with db.connect() as connection:
                            assert connection.execute("SELECT count(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"text": "accent_insensitive", "photo_name": "found",
                                          "contact": "linked_opened_searched_unlinked",
                                          "source": "opened", "model_usage": 0,
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
