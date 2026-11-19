"""Echter Edge/Ollama-Klickpfad für einen geprüften Stilprofil-Vorschlag."""

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
        raise RuntimeError("Jarvis vor dem isolierten Stilprofil-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-style-suggestion-ui-") as temporary:
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
                        page.set_default_timeout(300_000)
                        requests = []
                        page.on("request", lambda request: requests.append(request)
                                if request.url.endswith("/style/profile/suggest") else None)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        store = DraftStore(Database(data / "jarvis.db"), data)
                        assert store.profile()["description"] == ""
                        page.get_by_label("Titel der Schreibprobe").fill("Eigener Bericht")
                        page.get_by_label("Schreibprobe", exact=True).fill(
                            "Ich komme direkt zum Punkt. Kurze Sätze machen den Gedanken klar. "
                            "Ein Absatz behandelt genau eine Frage. Dann folgt der nächste Schritt. " * 5)
                        page.get_by_role("button", name="Probe speichern", exact=True).click()
                        expect(page.get_by_text("Schreibprobe gespeichert.", exact=True)).to_be_visible()
                        page.locator(".item-row input[type=checkbox]").first.check()
                        expect(page.get_by_role("button", name="Stilprofil vorschlagen", exact=True)).to_be_disabled()
                        page.get_by_label("Ich habe die ausgewählten Schreibproben selbst verfasst.").check()
                        page.get_by_role("button", name="Stilprofil vorschlagen", exact=True).click()
                        expect(page.locator(".item-row input[type=checkbox]").first).to_be_disabled()
                        expect(page.get_by_label("Stilprofil-Vorschlag")).to_be_visible(timeout=300_000)
                        assert len(requests) == 1
                        assert len(requests[0].post_data_json["style_sample_ids"]) == 1
                        assert requests[0].post_data_json["confirm_own_text"] is True
                        assert store.profile()["description"] == ""
                        preview = page.get_by_label("Stilprofil-Vorschlag").input_value()
                        assert len(preview) >= 30
                        assert "SHA-256" in page.get_by_label("Stilprofil-Vorschlag").locator("..").inner_text()
                        page.get_by_label("Stilprofil-Vorschlag").fill(
                            "Klarer Ton, konkrete Wortwahl, kurze Sätze und ein Gedanke pro Absatz.")
                        page.get_by_role("button", name="Ins Stilprofil übernehmen").click()
                        expected = "Klarer Ton, konkrete Wortwahl, kurze Sätze und ein Gedanke pro Absatz."
                        assert page.get_by_label("Stilprofil", exact=True).input_value() == expected
                        assert page.get_by_label("Cloud-Freigabe Stilprofil").input_value() == "local_only"
                        assert store.profile()["description"] == ""
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.get_by_role("button", name="Profil speichern", exact=True).click()
                        expect(page.get_by_text("Stilprofil gespeichert.", exact=True)).to_be_visible()
                        assert store.profile()["description"] == expected
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        expect(page.get_by_label("Stilprofil", exact=True)).to_have_value(expected)
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"local_ollama": True, "one_request": True,
                                          "preview_review_save": True, "reload": True,
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
