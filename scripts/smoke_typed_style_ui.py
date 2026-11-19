"""Echter Edge/Ollama-Klickpfad für Textsortenprofil, Rückfall und Entwurfsbeleg."""

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
        raise RuntimeError("Jarvis vor dem isolierten Textsortenprofil-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-typed-style-ui-") as temporary:
        root = Path(temporary)
        data = root / "data"
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
                        page.set_default_timeout(240_000)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        store = DraftStore(Database(data / "jarvis.db"), data)
                        expect(page.get_by_role("button", name="Profil speichern", exact=True)).to_be_enabled()
                        page.get_by_label("Stilprofil", exact=True).fill("Allgemeine nüchterne Vorgabe.")
                        page.get_by_role("button", name="Profil speichern", exact=True).click()
                        expect(page.get_by_text("Stilprofil gespeichert.", exact=True)).to_be_visible()
                        expect(page.get_by_text("Allgemeines Profil gilt:", exact=False)).to_be_visible()
                        page.get_by_label("Stilprofil für Textsorte").fill("E-Mails freundlich und knapp.")
                        page.get_by_role("button", name="Textsorte speichern", exact=True).click()
                        expect(page.get_by_text("Stilprofil für Textsorte gespeichert.", exact=True)).to_be_visible()
                        assert store.typed_profile("email")["description"] == "E-Mails freundlich und knapp."
                        page.get_by_label("Stilprofil für Textsorte").fill("Noch nicht gespeicherter Sonderstil.")
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        expect(page.get_by_label("Stilprofil für Textsorte")).to_have_value(
                            "Noch nicht gespeicherter Sonderstil.")
                        page.get_by_label("Schreibauftrag").fill("Bestätige einen Termin in zwei kurzen Sätzen.")
                        page.get_by_role("button", name="Entwurf erstellen", exact=True).click()
                        expect(page.get_by_text("Entwurf erstellt. Prüfe und bearbeite", exact=False)).to_be_visible(
                            timeout=240_000)
                        draft = store.drafts()[0]
                        assert draft["kind"] == "email"
                        assert draft["style_snapshot"] == "E-Mails freundlich und knapp."
                        page.get_by_label("Entwurfstext").fill("Hallo, der Termin passt. Viele Grüße")
                        page.get_by_role("button", name="Änderung speichern").click()
                        expect(page.get_by_text("Änderung gespeichert.", exact=True)).to_be_visible()
                        page.get_by_role("button", name="EML speichern").click()
                        expect(page.get_by_text("Gespeichert:", exact=False)).to_be_visible()
                        assert "der Termin passt" in (data / "outputs" / f"{draft['id']}.eml").read_text()
                        page.get_by_role("button", name="Ausnahme entfernen").click()
                        expect(page.get_by_text("Eigene Vorgabe entfernt", exact=False)).to_be_visible()
                        assert not store.typed_profile("email")["override"]
                        assert store.profile_for_kind("email")["description"] == "Allgemeine nüchterne Vorgabe."
                        assert store.get_draft(draft["id"])["style_snapshot"] == "E-Mails freundlich und knapp."
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        expect(page.get_by_text("Allgemeines Profil gilt:", exact=False)).to_be_visible()
                        expect(page.get_by_label("Stilprofil für Textsorte")).to_have_value("")
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"typed_profile_used": True, "unsaved_form_preserved": True,
                                          "draft_snapshot": True, "eml_export": True,
                                          "fallback_after_remove": True, "reload": True,
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
