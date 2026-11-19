"""Isolierter Edge-Klicktest für den lokalen DPAPI-Kontotresor."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from smoke_ui import ROOT, port_free, stop_test_server, wait_ready

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.vault import CredentialVault


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Tresortest mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-vault-ui-") as temporary:
        root = Path(temporary)
        data = root / "data"
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

                        def open_vault():
                            page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                            page.locator("nav button").filter(has_text="Zugänge").click()
                            expect(page.get_by_role("heading", name="Zugänge", exact=True)).to_be_visible()

                        open_vault()
                        page.get_by_label("Anbieterkennung").fill("outlook")
                        page.get_by_label("Kontokennung").fill("maxim@example.test")
                        page.get_by_label("Geheimnis", exact=True).fill("synthetic-ui-secret-01")
                        page.get_by_role("button", name="Geschützt speichern").dblclick()
                        expect(page.get_by_text("Zugangsdaten geschützt gespeichert.", exact=False)).to_be_visible()
                        expect(page.get_by_text("outlook · maxim@example.test")).to_be_visible()
                        assert page.get_by_label("Geheimnis", exact=True).input_value() == ""
                        assert "synthetic-ui-secret-01" not in page.locator("main").inner_text()
                        vault = CredentialVault(data)
                        entries = vault.list()
                        assert len(entries) == 1 and entries[0]["usable"]
                        assert vault.secret_for(entries[0]["id"], "outlook",
                                                "maxim@example.test", "api_key") == "synthetic-ui-secret-01"
                        assert "synthetic-ui-secret-01" not in vault.path.read_text(encoding="utf-8")
                        page.get_by_label("Geheimnis", exact=True).fill("unsaved-secret")
                        page.locator("nav button").filter(has_text="Chat").click()
                        page.locator("nav button").filter(has_text="Zugänge").click()
                        assert page.get_by_label("Geheimnis", exact=True).input_value() == ""

                        stop_test_server(process)
                        process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                                                   cwd=ROOT, env=environment, stdout=log,
                                                   stderr=subprocess.STDOUT)
                        wait_ready(process, timeout=120)
                        open_vault()
                        expect(page.get_by_text("Version 1", exact=False)).to_be_visible()
                        page.get_by_role("button", name="Ersetzen").click()
                        page.get_by_label("Neues Geheimnis für maxim@example.test").fill("synthetic-ui-secret-02")
                        page.get_by_role("button", name="Neuen Wert speichern").dblclick()
                        expect(page.get_by_text("Zugangsdaten ersetzt.", exact=False)).to_be_visible()
                        expect(page.get_by_text("Version 2", exact=False)).to_be_visible()
                        assert "synthetic-ui-secret-02" not in page.locator("main").inner_text()
                        assert vault.secret_for(entries[0]["id"], "outlook",
                                                "maxim@example.test", "api_key") == "synthetic-ui-secret-02"
                        page.get_by_role("button", name="Entziehen…").click()
                        expect(page.get_by_text("nicht beim Anbieter widerrufen", exact=False)).to_be_visible()
                        page.get_by_role("button", name="Lokal entziehen").click()
                        expect(page.get_by_text("Zugangsdaten entzogen.")).to_be_visible()
                        expect(page.get_by_text("Keine Zugangsdaten gespeichert.")).to_be_visible()
                        assert vault.list() == []
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                        print(json.dumps({"created": 1, "rotated_version": 2, "revoked": True,
                                          "secret_in_ui": False, "secret_in_file": False,
                                          "restart": "passed", "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
