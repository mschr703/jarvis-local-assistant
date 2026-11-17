"""Edge-Klicktest für J5b: Zeitplan, Bericht, Pause und echten Neustart."""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from playwright.sync_api import expect, sync_playwright
from smoke_ui import ROOT, port_free, stop_test_server, wait_ready

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.project_checks import ProjectCheckStore
from jarvis.project_reviews import ProjectReviewStore
from jarvis.tasks import TaskStore
from jarvis.work_states import WorkStateStore


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Projekttest mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-project-review-ui-") as temporary:
        root = Path(temporary)
        data = root / "data"
        source = root / "sources"
        source.mkdir()
        file = source / "plan.md"
        file.write_text("# Atlas\nErste Fassung.\n", encoding="utf-8")
        db = Database(data / "jarvis.db")
        documents = DocumentStore(db)
        folder = documents.add_folder(str(source), "local_only")
        documents.index_folder(folder["id"])
        task_store = TaskStore(db)
        task = task_store.create("Vorarbeit", "", None, "Europe/Paris")
        states = WorkStateStore(db, documents, task_store)
        state = states.create("Atlas", "Fassung geprüft", "Budget offen", "Maria fragen",
                              [documents.documents()[0]["id"]], [task["id"]], [])
        reviews = ProjectReviewStore(db, ProjectCheckStore(db, states, task_store))
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
                        page.set_default_timeout(30_000)

                        def open_state():
                            page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                            page.locator("nav button").filter(has_text="Arbeitsstände").click()
                            page.get_by_role("button", name="Öffnen").click()
                            expect(page.get_by_role("heading", name="Atlas", exact=True)).to_be_visible()

                        def restart():
                            nonlocal process
                            stop_test_server(process)
                            process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                                                       cwd=ROOT, env=environment, stdout=log,
                                                       stderr=subprocess.STDOUT)
                            wait_ready(process, timeout=120)

                        open_state()
                        page.get_by_label("Prüfintervall in Tagen").fill("2")
                        page.get_by_label("Projektprüfzeit").fill("08:00")
                        page.get_by_role("button", name="Prüfplan speichern").click()
                        expect(page.get_by_text("Nächste Prüfung:", exact=False)).to_be_visible()
                        page.get_by_role("button", name="Jetzt als Bericht prüfen").dblclick()
                        expect(page.get_by_text("Projektprüfung als Bericht gespeichert.")).to_be_visible()
                        assert len(reviews.reports(state["id"])) == 1
                        file.write_text("# Atlas\nNeue Fassung.\n", encoding="utf-8")
                        task_store.complete(task["id"])
                        page.get_by_role("button", name="Jetzt als Bericht prüfen").click()
                        deadline = time.monotonic() + 10
                        while time.monotonic() < deadline:
                            newest = reviews.reports(state["id"])[0]
                            if newest["summary"] and newest["summary"]["blockers"]:
                                break
                            time.sleep(0.1)
                        expect(page.get_by_text("Stand geändert")).to_be_visible()
                        expect(page.get_by_text("0 offen · 1 erledigt", exact=False)).to_be_visible()
                        assert len(reviews.reports(state["id"])) == 2
                        assert "geaendert" in reviews.reports(state["id"])[0]["summary"]["blockers"][0]
                        page.get_by_label("Projektprüfung pausieren").check()
                        expect(page.get_by_label("Projektprüfung pausieren")).to_be_checked()
                        page.get_by_role("button", name="Prüfplan speichern").click()
                        expect(page.get_by_text("Regelmäßige Projektprüfung pausiert.")).to_be_visible()
                        yesterday = (datetime.now(ZoneInfo("Europe/Paris")).date()
                                     - timedelta(days=1)).isoformat()
                        with db.connect() as connection:
                            connection.execute("""UPDATE project_review_schedules SET next_local_date=?
                                WHERE state_id=?""", (yesterday, state["id"]))
                        restart()
                        assert len(reviews.reports(state["id"])) == 2
                        open_state()
                        expect(page.get_by_text("Pausiert", exact=False).first).to_be_visible()
                        page.get_by_label("Projektprüfung pausieren").uncheck()
                        page.get_by_role("button", name="Prüfplan speichern").click()
                        expect(page.get_by_text("Regelmäßige Projektprüfung gespeichert.")).to_be_visible()
                        with db.connect() as connection:
                            connection.execute("""UPDATE project_review_schedules SET next_local_date=?
                                WHERE state_id=?""", (yesterday, state["id"]))
                        restart()
                        deadline = time.monotonic() + 10
                        while len(reviews.reports(state["id"])) < 3 and time.monotonic() < deadline:
                            time.sleep(0.1)
                        assert len(reviews.reports(state["id"])) == 3, {
                            "status": reviews.status(state["id"]),
                            "reports": [(item["local_date"], item["kind"], item["status"])
                                        for item in reviews.reports(state["id"])],
                            "server_log": (root / "server.log").read_text(encoding="utf-8", errors="replace")[-2000:],
                        }
                        open_state()
                        expect(page.locator("main strong").filter(has_text="· Geplant ·")).to_be_visible()
                        overflow = page.evaluate("""() => ({
                          viewport: innerWidth, html: document.documentElement.scrollWidth,
                          body: document.body.scrollWidth,
                          suspects: [...document.querySelectorAll('*')]
                            .filter(el => getComputedStyle(el).display !== 'none'
                              && el.scrollWidth > el.clientWidth + 1)
                            .map(el => ({tag: el.tagName, class: el.className,
                              text: el.textContent?.slice(0, 80),
                              scroll: el.scrollWidth, client: el.clientWidth,
                              right: el.getBoundingClientRect().right}))
                            .slice(0, 20)})""")
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), overflow
                        restart()
                        assert len(reviews.reports(state["id"])) == 3
                        print(json.dumps({"manual": 2, "scheduled": 1, "paused_catchup": False,
                                          "restart_duplicate": False, "source_change": True,
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
