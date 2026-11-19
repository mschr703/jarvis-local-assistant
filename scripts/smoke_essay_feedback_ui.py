"""Echter Edge/Ollama-Klickpfad für bewusstes Essay-Stilfeedback."""

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
        raise RuntimeError("Jarvis vor dem isolierten Stilfeedback-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-essay-feedback-ui-") as temporary:
        root = Path(temporary)
        data = root / "data"
        store = DraftStore(Database(data / "jarvis.db"), data)
        first = store.create_draft("essay", "", "Vorheriger Essay",
                                   "## Einleitung\n\nEin erster bearbeitbarer Text.\n\n## Schluss\n\nEin Ende.",
                                   [], "Fakten prüfen.", "klar und sachlich", word_target=300,
                                   outline=["Einleitung", "Schluss"])
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
                        page.set_default_timeout(900_000)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        page.get_by_label("Stilprofil", exact=True).fill("Klar, sachlich, kurze Sätze.")
                        page.get_by_role("button", name="Profil speichern").click()
                        expect(page.get_by_text("Stilprofil gespeichert.")).to_be_visible(timeout=120_000)
                        page.get_by_role("heading", name="Gespeicherte Entwürfe").locator(
                            "..").get_by_role("button", name="essay:", exact=False).click()
                        note = "Bitte kurze Sätze ohne allgemeine Einleitung schreiben."
                        page.get_by_label("Essay-Stilhinweis").fill(note)
                        page.get_by_role("button", name="Stilhinweis speichern").click()
                        expect(page.get_by_text("Stilhinweis für künftige lokale Essays gespeichert.")).to_be_visible(
                            timeout=120_000)
                        feedback = store.essay_feedback()
                        assert len(feedback) == 1 and feedback[0]["draft_id"] == first["id"]
                        assert feedback[0]["note"] == note
                        page.get_by_label("Entwurfstyp").select_option("essay")
                        page.get_by_label("Schreibauftrag").fill(
                            "Schreibe einen kurzen Essay über eine Europa-Mission. Keine erfundenen Fakten.")
                        page.get_by_label("Essay-Wortziel").fill("300")
                        page.get_by_label("Essay-Gliederung").fill("Einleitung\nSchluss")
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.get_by_role("button", name="Entwurf erstellen", exact=True).click()
                        expect(page.get_by_text("Entwurf erstellt. Prüfe und bearbeite den Text vor dem Export.")).to_be_visible(
                            timeout=900_000)
                        newer = store.drafts()[0]
                        assert newer["id"] != first["id"] and newer["kind"] == "essay"
                        assert newer["style_feedback"] == [{"id": feedback[0]["id"], "note": note}]
                        expect(page.get_by_text("Bei diesem Entwurf verwendet:", exact=False)).to_be_visible()
                        page.get_by_role("heading", name="Bestätigte Essay-Stilhinweise").locator(
                            "..").get_by_role("button", name="Löschen").click()
                        expect(page.get_by_text("Noch keine bestätigten Hinweise.")).to_be_visible(timeout=120_000)
                        assert store.essay_feedback() == []
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        page.get_by_role("heading", name="Gespeicherte Entwürfe").locator(
                            "..").get_by_role("button", name="essay:", exact=False).first.click()
                        expect(page.get_by_text("Bei diesem Entwurf verwendet:", exact=False)).to_be_visible()
                        assert store.get_draft(newer["id"])["style_feedback"][0]["note"] == note
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"real_ollama_essay": True, "feedback_saved": True,
                                          "used_snapshot": True, "deleted_for_future": True,
                                          "reload": True, "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
