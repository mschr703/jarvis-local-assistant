"""Exercise selected local photos, manual event links, and stale originals in Edge."""

import io
import json
import os
import subprocess
import sys
import zipfile
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
        raise RuntimeError("Jarvis vor dem isolierten Foto-Test stoppen")
    with TemporaryDirectory(prefix="jarvis-photos-ui-") as temporary:
        root = Path(temporary)
        folder = root / "Fotos"
        folder.mkdir()
        photo = folder / "urlaub.jpg"
        Image.new("RGB", (50, 40), "red").save(photo)
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
                        page = browser.new_page(viewport={"width": 390, "height": 844},
                                                accept_downloads=True)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Lebenschronik", exact=True).click()
                        page.get_by_label("Ereignisdatum").fill("2024-08-12")
                        page.get_by_label("Ereignistitel").fill("Urlaub am See")
                        page.get_by_role("button", name="Ereignis speichern").click()
                        expect(page.get_by_text("2024-08-12 · Urlaub am See")).to_be_visible()
                        page.get_by_label("Absoluter Fotoordnerpfad").fill(str(folder))
                        page.get_by_role("button", name="Fotoordner freigeben").click()
                        expect(page.get_by_role("button", name="Fotos einlesen")).to_be_visible()
                        page.get_by_role("button", name="Fotos einlesen").click()
                        photo_item = page.locator(".photo-item").filter(has_text="urlaub.jpg")
                        expect(photo_item).to_be_visible()
                        select = page.get_by_label("Ereignis für urlaub.jpg")
                        select.select_option(label="2024-08-12 · Urlaub am See")
                        photo_item.get_by_role("button", name="Zuordnung speichern").click()
                        expect(page.get_by_role("heading", name="Alben nach Ereignis")).to_be_visible()
                        expect(page.get_by_alt_text("Vorschau von urlaub.jpg")).to_be_visible()
                        assert page.get_by_alt_text("Vorschau von urlaub.jpg").evaluate(
                            "(img) => img.complete && img.naturalWidth > 0")
                        with page.expect_download() as album_download:
                            page.get_by_role("button", name="Album als ZIP herunterladen").click()
                        download = album_download.value
                        assert download.suggested_filename == "lebensalbum.zip"
                        with zipfile.ZipFile(io.BytesIO(Path(download.path()).read_bytes())) as album:
                            manifest = json.loads(album.read("manifest.json"))
                            assert manifest["event"]["title"] == "Urlaub am See"
                            assert album.read(manifest["photos"][0]["archive_name"]) == photo.read_bytes()
                        Image.new("RGB", (50, 40), "green").save(photo)
                        page.get_by_role("button", name="Album als ZIP herunterladen").click()
                        expect(page.get_by_text("Fotoquelle wurde geändert; Ordner neu einlesen"))\
                            .to_be_visible()
                        page.get_by_role("button", name="Status aktualisieren").click()
                        expect(page.get_by_text("Original geändert")).to_be_visible()
                        page.get_by_role("button", name="Fotos einlesen").click()
                        expect(page.get_by_role("heading", name="Noch nicht zugeordnet")).to_be_visible()
                        select = page.get_by_label("Ereignis für urlaub.jpg")
                        select.select_option(label="2024-08-12 · Urlaub am See")
                        photo_item.get_by_role("button", name="Zuordnung speichern").click()
                        expect(page.get_by_role("heading", name="Alben nach Ereignis")).to_be_visible()
                        page.once("dialog", lambda dialog: dialog.accept())
                        page.get_by_role("button", name="Fotoordnerfreigabe entfernen").click()
                        expect(page.get_by_label("Absoluter Fotoordnerpfad")).to_be_visible()
                        assert photo.exists()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        db = Database(Path(environment["JARVIS_DATA_DIR"]) / "jarvis.db")
                        with db.connect() as connection:
                            assert connection.execute("SELECT count(*) FROM photo_assets").fetchone()[0] == 0
                            assert connection.execute("SELECT count(*) FROM memory_events").fetchone()[0] == 1
                            assert connection.execute("SELECT count(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"folder": "selected", "photo": "linked_reconfirmed",
                                          "album": "downloaded_then_stale_blocked",
                                          "stale": "blocked", "original": "unchanged",
                                          "model_usage": 0, "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
