"""Echter Edge/Ollama-Klickpfad für das ausdrückliche Schreibprofil Maxim."""

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
        raise RuntimeError("Jarvis vor dem isolierten Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-maxim-ui-") as temporary:
        root = Path(temporary)
        data = root / "data"
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct",
                           JARVIS_TIMEZONE="Europe/Paris",
                           JARVIS_PERSONAL_STYLE_LABEL="Maxim")
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
                        requests = []
                        page.on("request", lambda request: requests.append(request)
                                if request.url.endswith("/drafts/generate") else None)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        store = DraftStore(Database(data / "jarvis.db"), data)
                        expect(page.get_by_label("Schreibprofil für Entwurf")).to_be_visible()
                        page.get_by_label("Schreibprofil Maxim", exact=True).fill(
                            "Ich schreibe knapp, konkret und ohne formelhafte Einleitungen.")
                        page.get_by_role("button", name="Maxim speichern", exact=True).click()
                        expect(page.get_by_text("Schreibprofil Maxim gespeichert.", exact=False)).to_be_visible()
                        assert store.named_profile()["revision"] == 1
                        page.get_by_label("Titel der Schreibprobe").fill("Eigener Mustertext")
                        page.get_by_label("Schreibprobe", exact=True).fill(
                            "Hallo, ich fasse mich kurz. Mir ist dieser Punkt wichtig. Bis bald, Maxim.")
                        page.get_by_role("button", name="Probe speichern", exact=True).click()
                        expect(page.get_by_text("Schreibprobe gespeichert.", exact=True)).to_be_visible()
                        page.locator(".item-row input[type=checkbox]").first.check()
                        page.get_by_label("Schreibprofil für Entwurf").select_option("Maxim")
                        page.get_by_label("Entwurfstyp").select_option("text")
                        page.get_by_label("Schreibauftrag").fill("Schreibe eine kurze Einladung zum Treffen.")
                        page.get_by_role("button", name="Entwurf erstellen", exact=True).click()
                        expect(page.get_by_text("Entwurf erstellt. Prüfe und bearbeite", exact=False)).to_be_visible(
                            timeout=240_000)
                        assert len(requests) == 1
                        assert requests[0].post_data_json["named_style_profile"] == "Maxim"
                        assert len(requests[0].post_data_json["style_sample_ids"]) == 1
                        draft = store.drafts()[0]
                        assert draft["style_snapshot"] == store.named_profile()["description"]
                        assert draft["named_style_profile"]["revision"] == 1
                        expect(page.get_by_text("Schreibprofil Maxim · Fassung 1")).to_be_visible()
                        assert page.get_by_label("Entwurfstext").input_value().strip()
                        page.get_by_label("Entwurfstext").fill(
                            "Es ist wichtig zu beachten, dass wir uns morgen treffen.")
                        page.get_by_role("button", name="Schreibmuster prüfen").click()
                        expect(page.get_by_text("Hinweisfloskel:")).to_be_visible()
                        assert store.get_draft(draft["id"])["content"] == draft["content"]
                        page.get_by_label("Entwurfstext").fill(draft["content"])
                        expect(page.get_by_text("Hinweisfloskel:")).to_have_count(0)
                        page.get_by_role("button", name="Mit Maxim-Stil überarbeiten").click()
                        expect(page.get_by_label("Maxim-Stilvorschlag")).to_be_visible(timeout=240_000)
                        proposal = page.get_by_label("Maxim-Stilvorschlag").input_value()
                        assert proposal.strip()
                        assert store.get_draft(draft["id"])["content"] == draft["content"]
                        page.get_by_role("button", name="In Editor übernehmen").click()
                        expect(page.get_by_label("Entwurfstext")).to_have_value(proposal)
                        if proposal != draft["content"]:
                            page.get_by_role("button", name="Änderung speichern").click()
                            expect(page.get_by_text("Änderung gespeichert.", exact=True)).to_be_visible()
                            assert store.get_draft(draft["id"])["content"] == proposal
                        page.get_by_label("Schreibprofil Maxim", exact=True).fill("Neue, noch ungespeicherte Vorgabe.")
                        expect(page.get_by_text("Ungespeicherte Profiländerungen gelten", exact=False)).to_be_visible()
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        expect(page.get_by_label("Schreibprofil Maxim", exact=True)).to_have_value(
                            "Neue, noch ungespeicherte Vorgabe.")
                        page.get_by_role("button", name="Maxim neu laden").click()
                        expect(page.get_by_label("Schreibprofil Maxim", exact=True)).to_have_value(
                            "Ich schreibe knapp, konkret und ohne formelhafte Einleitungen.")
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"ollama": True, "explicit_choice": True,
                                          "sample": True, "draft_snapshot": True,
                                          "style_polish_preview": True,
                                          "local_pattern_check": True,
                                          "unsaved_form": True, "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
