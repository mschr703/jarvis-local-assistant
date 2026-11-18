"""Download a verified document package in Edge and inspect its ZIP manifest."""

import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from playwright.sync_api import expect, sync_playwright
from smoke_code_project_ui import wait_ui
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, stop_test_server

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database
from jarvis.documents import DocumentStore


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-document-package-ui-") as temporary:
        root = Path(temporary)
        allowed = root / "allowed"
        (allowed / "a").mkdir(parents=True)
        (allowed / "b").mkdir()
        first, second = allowed / "a" / "Beleg.txt", allowed / "b" / "Beleg.txt"
        first.write_text("Erster Originalbeleg.", encoding="utf-8")
        second.write_text("Zweiter Originalbeleg.", encoding="utf-8")
        for number in range(9):
            (allowed / f"C_Dokument_{number}.md").write_text(
                f"Unterlagenpaket: Dokument Nummer {number}.", encoding="utf-8")
        db = Database(root / "data" / "jarvis.db")
        docs = DocumentStore(db)
        folder = docs.add_folder(str(allowed), "local_only")
        assert docs.index_folder(folder["id"])["indexed"] == 11
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(db.path.parent), JARVIS_PROVIDER="ollama",
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
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        choices = page.get_by_role("checkbox", name=re.compile(
                            "für Unterlagenpaket auswählen"))
                        expect(choices).to_have_count(11)
                        for index in range(10):
                            choices.nth(index).check()
                        expect(choices.nth(10)).to_be_disabled()
                        choices.nth(8).uncheck()
                        expect(choices.nth(10)).to_be_enabled()
                        choices.nth(10).check()
                        expect(page.get_by_text("10 von höchstens zehn Dokumenten ausgewählt"))\
                            .to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        with page.expect_download() as event:
                            page.get_by_role("button", name=re.compile(
                                "Geprüftes Unterlagenpaket herunterladen")).click()
                        download = event.value
                        assert download.suggested_filename == "jarvis-unterlagenpaket.zip"
                        archive_bytes = Path(download.path()).read_bytes()
                        with ZipFile(io.BytesIO(archive_bytes)) as archive:
                            names = archive.namelist()
                            assert len(names) == 11
                            assert names[8:10] == ["09_Beleg.txt", "10_Beleg.txt"]
                            assert archive.read(names[8]) == first.read_bytes()
                            assert archive.read(names[9]) == second.read_bytes()
                            manifest = json.loads(archive.read("manifest.json"))
                            assert len(manifest["files"]) == 10
                            assert [entry["archive_name"] for entry in manifest["files"]] == names[:10]
                            assert str(root) not in archive.read("manifest.json").decode("utf-8")
                        expect(page.get_by_text("10 geprüfte Originale als ZIP heruntergeladen."))\
                            .to_be_visible()
                        expect(page.get_by_text("10 von höchstens zehn Dokumenten ausgewählt"))\
                            .to_have_count(0)
                        choices.nth(9).check()
                        choices.nth(10).check()
                        second.write_text("Inzwischen geändert.", encoding="utf-8")
                        page.get_by_role("button", name=re.compile(
                            "Geprüftes Unterlagenpaket herunterladen")).click()
                        expect(page.get_by_role("status").filter(
                            has_text="Originaldatei weicht vom Index ab")).to_be_visible()
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"package_files": 10, "duplicate_names": "unique",
                                          "manifest": "verified", "absolute_paths": "omitted",
                                          "stale_original": "rejected", "model_calls": 0,
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
