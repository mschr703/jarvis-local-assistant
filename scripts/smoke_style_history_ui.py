"""Echter Edge/Ollama-Klickpfad für Profilfassungen, Konflikt und Rückkehr."""

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
        raise RuntimeError("Jarvis vor dem isolierten Stilhistorien-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-style-history-ui-") as temporary:
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
                        page.get_by_label("Stilprofil", exact=True).fill("Allgemein knapp und klar.")
                        page.get_by_role("button", name="Profil speichern", exact=True).click()
                        expect(page.get_by_text("Stilprofil gespeichert.", exact=True)).to_be_visible()
                        expect(page.get_by_text("Gespeicherte Revision 1", exact=True).first).to_be_visible()

                        stale = browser.new_page(viewport={"width": 390, "height": 844})
                        stale.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        stale.get_by_role("button", name="Entwürfe", exact=True).click()
                        expect(stale.get_by_label("Stilprofil", exact=True)).to_have_value(
                            "Allgemein knapp und klar.")
                        page.get_by_label("Stilprofil", exact=True).fill("Allgemein freundlich und konkret.")
                        page.get_by_role("button", name="Profil speichern", exact=True).click()
                        expect(page.get_by_text("Gespeicherte Revision 2", exact=True).first).to_be_visible()
                        stale.get_by_label("Stilprofil", exact=True).fill("Veraltete Fensterfassung.")
                        stale.get_by_role("button", name="Profil speichern", exact=True).click()
                        expect(stale.get_by_text("Stilprofil wurde zwischenzeitlich geändert",
                                                 exact=True)).to_be_visible()
                        stale.get_by_role("button", name="Allgemeines Profil neu laden").click()
                        expect(stale.get_by_label("Stilprofil", exact=True)).to_have_value(
                            "Allgemein freundlich und konkret.")
                        stale.close()

                        page.get_by_text("Allgemeines Profil: Fassungen und Rückkehr", exact=False).click()
                        page.get_by_role("button", name="Allgemeines Profil Fassung 1 wiederherstellen").click()
                        expect(page.get_by_label("Stilprofil", exact=True)).to_have_value(
                            "Allgemein knapp und klar.")
                        assert store.profile()["revision"] == 3
                        assert store.profile_history("general")["versions"][0]["restored_from"] == 1

                        page.get_by_label("Stilprofil für Textsorte").fill("E-Mails kurz und sachlich.")
                        page.get_by_role("button", name="Textsorte speichern", exact=True).click()
                        expect(page.get_by_text("Stilprofil für Textsorte gespeichert.",
                                                exact=True)).to_be_visible()
                        page.get_by_label("Stilprofil für Textsorte").fill("E-Mails freundlich.")
                        page.get_by_label("Cloud-Freigabe Textsortenprofil").select_option("cloud_allowed")
                        page.get_by_role("button", name="Textsorte speichern", exact=True).click()
                        expect(page.get_by_text("Gespeicherte Revision 2", exact=True).last).to_be_visible()
                        page.get_by_role("button", name="Ausnahme entfernen").click()
                        expect(page.get_by_text("Eigene Vorgabe entfernt", exact=False)).to_be_visible()
                        assert not store.typed_profile("email")["override"]
                        page.get_by_text("email: Fassungen und Rückkehr", exact=False).click()
                        page.get_by_role("button", name="email Fassung 1 wiederherstellen").click()
                        expect(page.get_by_label("Stilprofil für Textsorte")).to_have_value(
                            "E-Mails kurz und sachlich.")
                        assert store.typed_profile("email")["revision"] == 4
                        page.get_by_label("Schreibauftrag").fill(
                            "Bestätige freundlich in zwei Sätzen einen Termin.")
                        page.get_by_role("button", name="Entwurf erstellen", exact=True).click()
                        expect(page.get_by_text("Entwurf erstellt. Prüfe und bearbeite", exact=False)).to_be_visible(
                            timeout=240_000)
                        draft = store.drafts()[0]
                        assert draft["kind"] == "email"
                        assert draft["style_snapshot"] == "E-Mails kurz und sachlich."
                        page.get_by_role("button", name="email Fassung 2 wiederherstellen").click()
                        expect(page.get_by_label("Cloud-Freigabe Textsortenprofil")).to_have_value(
                            "cloud_allowed")
                        assert store.get_draft(draft["id"])["style_snapshot"] == (
                            "E-Mails kurz und sachlich.")
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        expect(page.get_by_label("Stilprofil für Textsorte")).to_have_value(
                            "E-Mails freundlich.")
                        assert store.profile_history("email")["revision"] == 5
                        page.get_by_label("Textsorte für Stilprofil").select_option("essay")
                        page.get_by_label("Stilprofil für Textsorte").fill(
                            "Essays mit klarer These und kurzen Absätzen.")
                        page.get_by_role("button", name="Textsorte speichern", exact=True).click()
                        expect(page.get_by_text("Gespeicherte Revision 1", exact=True).last).to_be_visible()
                        page.get_by_label("Stilprofil für Textsorte").fill(
                            "Essays mit Beispielen und längeren Absätzen.")
                        page.get_by_role("button", name="Textsorte speichern", exact=True).click()
                        page.get_by_text("essay: Fassungen und Rückkehr", exact=False).click()
                        page.get_by_role("button", name="essay Fassung 1 wiederherstellen").click()
                        expect(page.get_by_label("Stilprofil für Textsorte")).to_have_value(
                            "Essays mit klarer These und kurzen Absätzen.")
                        assert store.profile_history("essay")["revision"] == 3
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"general_restore": True, "two_window_conflict": True,
                                          "typed_fallback_restore": True, "essay_restore": True,
                                          "cloud_policy_restored": True,
                                          "old_draft_snapshot": True,
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
