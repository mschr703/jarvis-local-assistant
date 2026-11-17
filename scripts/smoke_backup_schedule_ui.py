"""Isolierter Edge-Klicktest für geplante Sicherung und sichtbaren Fehler."""

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
from jarvis.scheduled_backup import ScheduledBackup


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Backup-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-backup-ui-") as temporary:
        root = Path(temporary)
        data, destination = root / "data", root / "backups"
        DocumentStore(Database(data / "jarvis.db")).add_memory(
            "Backup-Browserprobe", "UI", "local_only")
        ScheduledBackup(data).configure(destination, "00:00", "Europe/Paris", 2,
                                        "lange lokale Testpassphrase")
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
                        page = browser.new_page(viewport={"width": 390, "height": 844})
                        page.set_default_timeout(60_000)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        section = page.get_by_role("heading", name="Geplante verschlüsselte Backups").locator("..")
                        expect(section).to_contain_text("täglich 00:00 Europe/Paris")
                        button = section.get_by_role("button", name="Jetzt sichern und prüfen")
                        button.evaluate("node => { node.click(); node.click(); }")
                        expect(section).to_contain_text("Letzter Lauf: succeeded", timeout=60_000)
                        archives = list(destination.glob("jarvis-auto-*.jbackup"))
                        assert len(archives) == 1
                        moved = root / "temporarily-unavailable"
                        destination.rename(moved)
                        button.click()
                        expect(section).to_contain_text("Backupfehler: Backup-Zielordner fehlt")
                        moved.rename(destination)
                        button.click()
                        expect(section).to_contain_text("Letzter Lauf: succeeded", timeout=60_000)
                        assert len(list(destination.glob("jarvis-auto-*.jbackup"))) == 2
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                        print(json.dumps({"first_archive": archives[0].name,
                                          "failure_visible": True, "retry": "succeeded",
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
