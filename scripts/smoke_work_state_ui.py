"""Isolierter Edge-Klicktest für manuelle Arbeitsstände über einen echten Neustart."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from smoke_ui import ROOT, port_free, stop_test_server, wait_ready


def start(root: Path, environment: dict, number: int):
    log = (root / f"server-{number}.log").open("w", encoding="utf-8")
    process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                               cwd=ROOT, env=environment, stdout=log,
                               stderr=subprocess.STDOUT)
    try:
        wait_ready(process, timeout=120)
    except BaseException:
        stop_test_server(process)
        log.close()
        raise
    return process, log


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-work-state-ui-") as temporary:
        root = Path(temporary)
        sources = root / "sources"
        sources.mkdir()
        note = sources / "projekt.txt"
        note.write_text("Bestätigte Quelle", encoding="utf-8")
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(root / "data"), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct")
        process, log = start(root, environment, 1)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                try:
                    page = browser.new_page(viewport={"width": 390, "height": 800})
                    page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                    page.get_by_role("button", name="Dokumente", exact=True).click()
                    page.get_by_label("Absoluter Ordnerpfad").fill(str(sources))
                    page.get_by_role("button", name="Freigeben").click()
                    page.get_by_role("button", name="Index aktualisieren").click()
                    page.get_by_role("button", name="Aufgaben", exact=True).click()
                    page.get_by_label("Aufgabe", exact=True).fill("Angebot prüfen")
                    page.get_by_role("button", name="Aufgabe speichern").click()
                    page.get_by_role("button", name="Arbeitsstände", exact=True).click()
                    page.get_by_label("Projektname").fill("Atlas")
                    page.get_by_label("Bestätigte Ergebnisse").fill("Quelle gelesen")
                    page.get_by_label("Offene Fragen").fill("Budget offen")
                    page.get_by_label("Nächster Schritt").fill("Maria fragen")
                    page.get_by_label(str(note), exact=True).check()
                    page.get_by_label("Angebot prüfen", exact=True).check()
                    page.get_by_label("Arbeitsstand-Links").fill("https://example.com/projekt")
                    page.get_by_role("button", name="Arbeitsstand speichern").click()
                    page.get_by_text("Arbeitsstand gespeichert.", exact=False).wait_for()
                    page.get_by_text("Unverändert", exact=True).wait_for()
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                finally:
                    browser.close()
        finally:
            stop_test_server(process)
            log.close()

        process, log = start(root, environment, 2)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                try:
                    page = browser.new_page(viewport={"width": 390, "height": 800})
                    page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                    page.get_by_role("button", name="Arbeitsstände", exact=True).click()
                    page.get_by_role("button", name="Öffnen").click()
                    page.get_by_text("Quelle gelesen", exact=True).wait_for()
                    page.get_by_text("Budget offen", exact=True).wait_for()
                    expect(page.get_by_role("button", name="Datei öffnen")).to_be_enabled()
                    called = []

                    def tool_request(route):
                        called.append(route.request.post_data_json)
                        route.fulfill(status=200, json={"status": "succeeded", "error": None})

                    page.route("**/api/runs", tool_request)
                    page.get_by_role("button", name="Datei öffnen").click()
                    page.get_by_text("Datei öffnen: erfolgreich.", exact=True).wait_for()
                    assert called[-1]["tool_name"] == "open_file"
                    assert len(called[-1]["args"]["expected_sha256"]) == 64
                    page.get_by_role("button", name="Link prüfen").click()
                    page.get_by_text("Link prüfen: erfolgreich.", exact=True).wait_for()
                    assert called[-1]["tool_name"] == "browser_read"
                    page.unroute("**/api/runs", tool_request)

                    note.write_text("Extern geändert", encoding="utf-8")
                    page.get_by_role("button", name="Status neu prüfen").click()
                    page.get_by_text("Seit dem Sichern geändert", exact=True).wait_for()
                    expect(page.get_by_role("button", name="Datei öffnen")).to_be_disabled()
                    note.unlink()
                    page.get_by_role("button", name="Status neu prüfen").click()
                    page.get_by_text("Datei fehlt", exact=True).wait_for()
                    page.get_by_role("button", name="Bearbeiten").click()
                    page.get_by_label("Nächster Schritt").fill("Datei wiederherstellen")
                    page.get_by_role("button", name="Arbeitsstand speichern").click()
                    page.get_by_text("Datei wiederherstellen", exact=True).wait_for()
                    page.get_by_role("button", name="Löschen…").click()
                    page.get_by_role("button", name="Endgültig löschen").click()
                    page.get_by_text("Arbeitsstand gelöscht.", exact=False).wait_for()
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    print(json.dumps({"restart": "persisted", "file": "changed_then_missing",
                                      "tool_wiring": "open_file_and_browser_read",
                                      "edit": "saved", "delete": "saved", "mobile_width": "fits"}))
                finally:
                    browser.close()
        finally:
            stop_test_server(process)
            log.close()


if __name__ == "__main__":
    main()
