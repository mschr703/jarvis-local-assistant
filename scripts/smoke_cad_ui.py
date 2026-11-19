"""Real local Ollama, CAD engine, approval, download and narrow Edge viewport."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, stop_test_server, wait_ready


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten CAD-UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-cad-ui-") as temporary:
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
                    expect.set_options(timeout=300_000)
                    browser = playwright.chromium.launch(channel="msedge", headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 390, "height": 844},
                                                accept_downloads=True)
                        page.set_default_timeout(300_000)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Aktivität", exact=True).click()
                        page.get_by_label("Werkzeug", exact=True).select_option("cad_holder_build")
                        page.get_by_role("button", name="Maße aus Beschreibung vorschlagen").click()
                        expect(page.get_by_text("Lokaler Ollama-Vorschlag", exact=False)).to_be_visible()
                        print("Ollama-Maßvorschlag bestätigt", flush=True)
                        expect(page.get_by_label("CAD-Breite")).to_have_value("60")
                        expect(page.get_by_label("CAD-Bohrungsabstand")).to_have_value("40")
                        page.get_by_role("button", name="CAD-Vorschau erstellen").click()
                        expect(page.get_by_alt_text("CAD-Vorschau des Halters")).to_be_visible()
                        print("CAD-Vorschau sichtbar", flush=True)
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.get_by_role("button", name="STEP exportieren").click()
                        expect(page.get_by_text("cad_holder_export · waiting_approval")).to_be_visible()
                        assert not (list((data / "cad-models").glob("*/holder.step")))
                        page.get_by_role("button", name="Freigeben", exact=True).click()
                        link = page.get_by_role("link", name="Freigegebene CAD-Datei herunterladen")
                        expect(link).to_be_visible()
                        print("STEP-Freigabe und Export bestätigt", flush=True)
                        with page.expect_download() as downloaded:
                            link.click()
                        item = downloaded.value
                        assert item.suggested_filename.endswith(".step")
                        model = next((data / "cad-models").iterdir())
                        assert (model / "holder.step").stat().st_size > 100
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"ollama_proposal": True, "preview": True,
                                          "separate_approval": True, "step_download": True,
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
