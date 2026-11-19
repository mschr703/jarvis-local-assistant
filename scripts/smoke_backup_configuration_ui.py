"""Edge-Ende-zu-Ende-Test für Backup-Einrichtung, Download und Deaktivierung."""

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
from jarvis.documents import DocumentStore
from jarvis.encrypted_backup import restore_encrypted_backup


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Backup-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-backup-config-ui-") as temporary:
        root = Path(temporary)
        data, destination = root / "data", root / "backups"
        memory = DocumentStore(Database(data / "jarvis.db")).add_memory(
            "Einrichtung im Browser", "synthetischer Test", "local_only")
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct")
        with (root / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                                       cwd=ROOT, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT)
            try:
                wait_ready(process, timeout=120)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge", headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 390, "height": 844},
                                                accept_downloads=True)
                        page.set_default_timeout(60_000)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        section = page.get_by_role("heading", name="Geplante verschlüsselte Backups").locator("..")
                        expect(section).to_contain_text("Keine automatische Sicherung eingerichtet")
                        form = section.locator(".backup-schedule-form")
                        form.get_by_label("Backup-Zielordner (absoluter Pfad)").fill(str(destination))
                        form.get_by_label("Tageszeit").fill("00:00")
                        form.get_by_label("Archive behalten").fill("2")
                        password = form.get_by_label("Backup-Passphrase (mindestens 12 Zeichen)")
                        confirmation = form.get_by_label("Passphrase wiederholen")
                        password.fill("lange lokale Testpassphrase")
                        confirmation.fill("andere lange Passphrase")
                        form.get_by_role("button", name="Backup-Zeitplan einrichten").click()
                        expect(section).to_contain_text("Backup-Passwörter stimmen nicht überein")
                        assert password.input_value() == confirmation.input_value() == ""
                        assert not (data / "backup_schedule.json").exists()
                        password.fill("lange lokale Testpassphrase")
                        confirmation.fill("lange lokale Testpassphrase")
                        form.get_by_role("button", name="Backup-Zeitplan einrichten").click()
                        expect(section).to_contain_text("Aktiv · täglich 00:00 Europe/Paris")
                        assert password.input_value() == confirmation.input_value() == ""
                        assert "lange lokale Testpassphrase" not in (data / "backup_schedule.json").read_text(encoding="utf-8")
                        section.get_by_role("button", name="Jetzt sichern und prüfen").click()
                        expect(section).to_contain_text("Letzter Lauf: succeeded")
                        with page.expect_download() as captured:
                            section.get_by_role("button", name="Geprüftes Archiv herunterladen").click()
                        downloaded = root / "browser-konfiguriert.jbackup"
                        captured.value.save_as(downloaded)
                        restored = root / "browser-restore"
                        restore_encrypted_backup(downloaded, restored,
                                                 "lange lokale Testpassphrase")
                        assert (DocumentStore(Database(restored / "jarvis.db"))
                                .get_memory(memory["id"])["text"] == memory["text"])
                        page.reload(wait_until="domcontentloaded")
                        section = page.get_by_role("heading", name="Geplante verschlüsselte Backups").locator("..")
                        expect(section).to_contain_text("Aktiv · täglich 00:00 Europe/Paris")
                        assert section.get_by_label("Backup-Passphrase (mindestens 12 Zeichen)").input_value() == ""
                        section.get_by_role("button", name="Zeitplan deaktivieren").click()
                        expect(section).to_contain_text("vorhandene Archive bleiben erhalten")
                        section.get_by_role("button", name="Abbrechen").click()
                        section.get_by_role("button", name="Zeitplan deaktivieren").click()
                        section.get_by_role("button", name="Ja, Zeitplan deaktivieren").click()
                        expect(section).to_contain_text("Deaktiviert · täglich 00:00 Europe/Paris")
                        assert downloaded.exists() and len(list(destination.glob("*.jbackup"))) == 1
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                        print(json.dumps({"configured": True, "download_restore": "succeeded",
                                          "disabled_after_confirmation": True,
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
