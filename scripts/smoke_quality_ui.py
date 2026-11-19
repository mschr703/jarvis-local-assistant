"""Isolated Edge click path for the quality overview and evidence links."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright
from smoke_ui import ROOT, port_free, stop_test_server

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database, utcnow
from jarvis.research import ResearchStore


def seed(data: Path) -> dict[str, str]:
    db = Database(data / "jarvis.db")
    chat_id = db.create_chat()["id"]
    db.add_message(chat_id, "user", "Prüfe die Qualitätsübersicht")
    answer = db.add_message(chat_id, "assistant", "Testantwort")
    db.evaluate_chat_answer(chat_id, answer["id"], "partial", "unsupported",
                            "Chatbeleg fehlt", True, "Chatbeleg nennen", 0)
    with db.connect() as conn:
        conn.execute("""INSERT INTO plans(id,objective,steps_json,status,provider,model,
            created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
                     ("plan-quality", "Qualitätsplan prüfen", "[]", "succeeded", "ollama",
                      "fixture", utcnow(), utcnow()))
    db.evaluate_plan("plan-quality", "failed", "unclear", "Plan unvollständig", True,
                     "Plan vollständig", 0)
    run = db.create_run("open_file", "{}", "quality-hash", "read")
    db.set_run(run["id"], "failed", error="Datei nicht gefunden")
    db.evaluate_run(run["id"], "failed", "Falsche Datei", True,
                    "Richtige Datei", 0)
    research = ResearchStore(db)
    report = research.save("Welche Quelle für Qualität?", "Qualität", "fixture", [], [],
                           "", "", [], "ollama", "fixture")
    research.evaluate(report["id"], "partial", "unsupported",
                      "Recherchequelle fehlt", True, "Quelle nennen", 0)
    return {"chat_id": chat_id, "run_id": run["id"], "report_id": report["id"]}


def wait_ui(process: subprocess.Popen, timeout: int = 120) -> None:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Jarvis-Testserver wurde vor dem Start beendet")
        try:
            with urlopen("http://127.0.0.1:8765/session", timeout=2):
                pass
            with urlopen("http://127.0.0.1:5173/", timeout=2):
                return
        except OSError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(.5)
    raise RuntimeError(f"Jarvis-Testserver oder Oberfläche wurde nicht bereit: {last_error}")


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-quality-ui-") as temporary:
        root = Path(temporary)
        data = root / "data"
        ids = seed(data)
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct")
        with (root / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                                       cwd=ROOT, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT)
            try:
                wait_ui(process)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge", headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 390, "height": 844})
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded",
                                  timeout=120000)
                        page.get_by_role("button", name="Aktivität", exact=True).click()
                        quality = page.get_by_role("region", name="Qualitätsübersicht")
                        expect(quality.get_by_text("Chatantworten: 1 von 1 bewertet")).to_be_visible(timeout=30000)
                        expect(quality.get_by_text("Pläne: 1 von 1 bewertet")).to_be_visible()
                        expect(quality.get_by_text("Werkzeugläufe: 1 von 1 bewertet")).to_be_visible()
                        expect(quality.get_by_text("Recherchen: 1 von 1 bewertet")).to_be_visible()

                        def open_case(reason: str) -> None:
                            row = quality.get_by_text(reason, exact=False).first.locator(
                                "xpath=ancestor::*[contains(@class,'item-row')][1]")
                            row.get_by_role("button", name="Originalbeleg öffnen").click()

                        open_case("Falsche Datei")
                        expect(page.get_by_role("region", name="Originalbeleg").get_by_text(
                            ids["run_id"], exact=False).first).to_be_visible()
                        page.get_by_role("region", name="Originalbeleg").get_by_role(
                            "button", name="Schließen").click()
                        open_case("Plan unvollständig")
                        expect(page.get_by_role("region", name="Originalbeleg").get_by_text(
                            "Qualitätsplan prüfen", exact=False).first).to_be_visible()
                        page.get_by_role("region", name="Originalbeleg").get_by_role(
                            "button", name="Schließen").click()
                        open_case("Chatbeleg fehlt")
                        expect(page.get_by_text("Testantwort", exact=True)).to_be_visible()
                        page.get_by_role("button", name="Aktivität", exact=True).click()
                        open_case("Recherchequelle fehlt")
                        expect(page.get_by_text("Welche Quelle für Qualität?", exact=False).first).to_be_visible()
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                        print(json.dumps({"counts": "four_kinds", "evidence": "all_opened",
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
