"""Real Edge/Ollama draft with an explicitly chosen personal recipient."""

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
from jarvis.contacts import ContactStore
from jarvis.db import Database
from jarvis.drafts import DraftStore
from jarvis.memory_events import MemoryEventStore


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Empfänger-Test stoppen")
    with TemporaryDirectory(prefix="jarvis-recipient-ui-") as temporary:
        root = Path(temporary)
        data = root / "data"
        db = Database(data / "jarvis.db")
        contacts = ContactStore(db)
        friend = contacts.create("Alex", "München", "Freund")
        colleague = contacts.create("Alex", "Arbeit", "Kollege")
        chosen = contacts.add_note(friend["id"], "Mag Museen")
        contacts.add_note(friend["id"], "Privates Geheimnis")
        contacts.add_note(colleague["id"], "Laborprojekt")
        events = MemoryEventStore(db)
        shared = events.create("2024-08-12", "Café in Lyon", "Am Fluss", "Lyon")
        events.link_contact(shared["id"], friend["id"], 1)
        foreign = events.create("2024-08-13", "Bürogespräch")
        events.link_contact(foreign["id"], colleague["id"], 1)
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
                        requests = []
                        page.on("request", lambda request: requests.append(request)
                                if request.url.endswith("/drafts/generate") else None)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        choice = page.get_by_label("Empfänger auswählen")
                        expect(choice.locator(f"option[value='{friend['id']}']")).to_be_attached()
                        choice.select_option(friend["id"])
                        note_checkbox = page.locator("label").filter(has_text="Mag Museen")\
                            .get_by_role("checkbox")
                        note_checkbox.check()
                        page.get_by_label("Ereignis 2024-08-12 · Café in Lyon · Lyon für Entwurf auswählen").check()
                        expect(page.get_by_label("Ereignis 2024-08-13 · Bürogespräch für Entwurf auswählen"))\
                            .to_have_count(0)
                        page.get_by_label("Schreibauftrag").fill(
                            "Schreibe Alex eine kurze Einladung ins Museum.")
                        contacts.update_note(friend["id"], chosen["id"], 1, "Mag Kunstmuseen")
                        page.get_by_role("button", name="Entwurf erstellen", exact=True).click()
                        expect(page.get_by_text("Kontakt wurde inzwischen geändert", exact=False))\
                            .to_be_visible()
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                        page.get_by_role("button", name="Kontakte", exact=True).click()
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        page.get_by_role("button", name="Aktuellen Kontaktstand übernehmen").click()
                        page.locator("label").filter(has_text="Mag Kunstmuseen")\
                            .get_by_role("checkbox").check()
                        page.get_by_label("Ereignis 2024-08-12 · Café in Lyon · Lyon für Entwurf auswählen").check()
                        events.update(shared["id"], 2, "2024-08-12", "Café in Lyon",
                                      "Später am Fluss", "Lyon")
                        page.get_by_role("button", name="Entwurf erstellen", exact=True).click()
                        expect(page.get_by_text("Ereignis wurde inzwischen geändert", exact=False))\
                            .to_be_visible()
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                        page.get_by_role("button", name="Kontakt und Ereignisse neu laden").click()
                        page.get_by_label("Ereignis 2024-08-12 · Café in Lyon · Lyon für Entwurf auswählen").check()
                        page.get_by_role("button", name="Entwurf erstellen", exact=True).click()
                        expect(page.get_by_text("Entwurf erstellt. Prüfe und bearbeite", exact=False))\
                            .to_be_visible(timeout=240_000)
                        assert len(requests) == 3
                        assert requests[2].post_data_json["recipient_contact_id"] == friend["id"]
                        assert requests[2].post_data_json["recipient_note_ids"] == [chosen["id"]]
                        assert requests[2].post_data_json["recipient_events"] == [
                            {"event_id": shared["id"], "expected_version": 3}]
                        draft = DraftStore(db, data).drafts()[0]
                        snapshot = draft["recipient_snapshot"]
                        assert snapshot["context"] == "München"
                        assert [note["content"] for note in snapshot["notes"]] == ["Mag Kunstmuseen"]
                        assert [(event["title"], event["version"], event["description"])
                                for event in snapshot["events"]] == [
                                    ("Café in Lyon", 3, "Später am Fluss")]
                        assert "Privates Geheimnis" not in str(snapshot)
                        assert "Laborprojekt" not in str(snapshot)
                        assert "Bürogespräch" not in str(snapshot)
                        expect(page.get_by_text("Empfänger beim Erstellen: Alex · München"))\
                            .to_be_visible()
                        expect(page.get_by_role("list", name="Ausgewählte gemeinsame Erlebnisse im Entwurf"))\
                            .to_contain_text("Café in Lyon")
                        assert page.get_by_label("Entwurfstext").input_value().strip()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage "
                                                      "WHERE purpose='draft'").fetchone()[0] == 1
                        print(json.dumps({"ollama": True, "recipient": "selected",
                                          "stale": "contact_and_event_refused_before_model",
                                          "snapshot": "verified", "cloud": False,
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
