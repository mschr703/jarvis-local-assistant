"""Real Edge and Ollama learning round with a local source and revision."""

import csv
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
from jarvis.learning import LearningStore


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-learning-ui-") as temporary:
        root = Path(temporary)
        sources = root / "sources"
        sources.mkdir()
        (sources / "biologie.txt").write_text(
            "Bei der Photosynthese nehmen Pflanzen Wasser aus dem Boden und "
            "Kohlendioxid aus der Luft auf. Mithilfe von Sonnenlicht entsteht "
            "Zucker. Dabei wird Sauerstoff abgegeben. Der Zucker dient der "
            "Pflanze als Energiequelle.", encoding="utf-8")
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
                        page = browser.new_page(viewport={"width": 390, "height": 844},
                                                accept_downloads=True)
                        page.set_default_timeout(180_000)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_label("Absoluter Ordnerpfad").fill(str(sources))
                        page.get_by_role("button", name="Freigeben", exact=True).click()
                        page.get_by_role("button", name="Index aktualisieren").click()
                        expect(page.get_by_text("Index aktualisiert:", exact=False)).to_be_visible()
                        page.get_by_label("Dokumente durchsuchen").fill("Photosynthese")
                        page.get_by_role("button", name="Suchen", exact=True).click()
                        page.locator(".source-result input[type=checkbox]").first.check()
                        page.get_by_role("button", name="Mit Fundstelle(n) lernen").click()
                        page.get_by_label("Lernthema").fill("Photosynthese")
                        page.get_by_role("button", name="Belegte Frage erstellen").click()
                        expect(page.get_by_text("Lernfrage erstellt.", exact=False)).to_be_visible(
                            timeout=180_000)
                        store = LearningStore(Database(data / "jarvis.db"))
                        item = store.list()[0]
                        assert item["source"]["path"] == str(sources / "biologie.txt")
                        assert item["evidence_quote"] in item["source"]["text"]
                        page.get_by_role("button", name="Frage/Musterantwort korrigieren").click()
                        expect(page.get_by_label("Lernfrage korrigieren")).to_be_visible(timeout=15_000)
                        page.get_by_label("Lernfrage korrigieren").fill(
                            "Was beschreibt die Quelle zur Photosynthese?")
                        page.get_by_role("button", name="Korrektur speichern").click()
                        expect(page.get_by_text("Frage, Musterantwort und Beleg korrigiert.")).to_be_visible()
                        page.get_by_label("Meine Lernantwort").fill(
                            "Pflanzen nutzen Wasser, Kohlendioxid und Sonnenlicht.")
                        page.get_by_role("button", name="Antwort prüfen", exact=True).click()
                        expect(page.get_by_text("Antwort gespeichert.", exact=False)).to_be_visible(
                            timeout=180_000)
                        assert store.get(item["id"])["user_answer"]
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Lernen", exact=True).click()
                        expect(page.get_by_label("Meine Lernantwort")).to_have_value(
                            "Pflanzen nutzen Wasser, Kohlendioxid und Sonnenlicht.")
                        expect(page.get_by_role("button", name="CSV herunterladen")).to_be_disabled()
                        page.get_by_label("Ich habe die Inhalte und Quellen vor dem Export geprüft.").check()
                        with page.expect_download() as download_info:
                            page.get_by_role("button", name="CSV herunterladen").click()
                        download = download_info.value
                        assert download.suggested_filename.startswith("jarvis-lernkarten-")
                        with download.path().open(encoding="utf-8-sig", newline="") as exported:
                            rows = list(csv.DictReader(exported, delimiter=";"))
                        assert len(rows) == 1, rows
                        assert rows[0]["Frage"] == "Was beschreibt die Quelle zur Photosynthese?"
                        assert rows[0]["Quelldatei"] == str(sources / "biologie.txt")
                        assert rows[0]["SHA-256"] == item["source"]["sha256"]
                        assert rows[0]["Quellenzitat"] == item["evidence_quote"]
                        version_before_exam = store.get(item["id"])["version"]
                        page.get_by_role("button", name="Prüfungsmodus öffnen").click()
                        page.get_by_role("button", name="Prüfung starten").click()
                        expect(page.get_by_label("Prüfungsantwort 1")).to_be_visible()
                        assert page.get_by_text("Musterantwort:").count() == 0
                        assert page.get_by_text("Quellenzitat:").count() == 0
                        assert page.get_by_text(item["source"]["sha256"]).count() == 0
                        page.get_by_label("Prüfungsantwort 1").fill("Nur Wasser und Licht.")
                        page.get_by_role("button", name="Prüfung abgeben").click()
                        expect(page.get_by_text("Musterantwort:")).to_be_visible()
                        expect(page.get_by_text("Quellenzitat:")).to_be_visible()
                        page.get_by_label("Selbsteinschätzung 1").select_option("partial")
                        expect(page.get_by_text("Teilweise: 1", exact=False)).to_be_visible()
                        expect(page.get_by_role("heading", name="Wiederholen")).to_be_visible()
                        assert store.get(item["id"])["version"] == version_before_exam
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"ollama": True, "source_quote": True,
                                          "question_correction": True, "answer_reload": True,
                                          "csv_export": True, "exam": True,
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
