"""Edge pilot for a trusted local private module with its own UI bundle."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from smoke_code_project_ui import wait_ui
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, stop_test_server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module-source", type=Path, required=True)
    args = parser.parse_args()
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-module-ui-") as temporary:
        root = Path(temporary)
        data = root / "data"
        upgrade_source = root / "source" / "day_notes"
        shutil.copytree(args.module_source, upgrade_source)
        upgrade_manifest = upgrade_source / "jarvis-module.json"
        upgraded = json.loads(upgrade_manifest.read_text(encoding="utf-8"))
        upgraded["version"] = "0.2.0"
        upgrade_manifest.write_text(json.dumps(upgraded), encoding="utf-8")
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama")
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
                        overview = page.locator("section.card").filter(
                            has=page.get_by_role("heading", name="Module", exact=True))
                        expect(overview.get_by_text("Keine optionalen Module installiert.")).to_be_visible()
                        overview.get_by_role("button", name="Module verwalten").click()
                        expect(page.get_by_text("Keine Module installiert.", exact=False)).to_be_visible()
                        page.get_by_label("Absoluter Pfad zum Paketordner").fill(
                            str(args.module_source.absolute()))
                        page.get_by_role("button", name="Installieren").click()
                        expect(page.get_by_role("heading", name="Tagesnotizen")).to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        expect(page.get_by_role("button", name="Tagesnotizen")).to_have_count(0)
                        page.get_by_role("button", name="Aktivieren").click()
                        expect(page.get_by_role("button", name="Tagesnotizen")).to_be_visible()
                        page.get_by_role("button", name="Übersicht", exact=True).click()
                        expect(overview.get_by_text("1 aktiv")).to_be_visible()
                        expect(overview.get_by_text("Tagesnotizen: aktiv")).to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.get_by_role("button", name="Tagesnotizen").click()
                        expect(page.get_by_label("Neue Tagesnotiz")).to_be_visible()
                        page.get_by_label("Neue Tagesnotiz").fill("Privater Modul-Pilot")
                        page.get_by_role("button", name="Notiz speichern").click()
                        expect(page.get_by_text("Privater Modul-Pilot")).to_be_visible()
                        tools = page.context.request.get("http://127.0.0.1:5173/api/tools").json()
                        assert any(item["name"] == "day_notes__list_notes" for item in tools)
                        token = page.context.request.get(
                            "http://127.0.0.1:5173/api/session").json()["csrf"]
                        tool_run = page.context.request.post(
                            "http://127.0.0.1:5173/api/runs",
                            headers={"x-jarvis-csrf": token},
                            data={"tool_name": "day_notes__list_notes", "args": {}})
                        assert tool_run.status == 200
                        assert tool_run.json()["status"] == "succeeded"
                        page.reload()
                        expect(page.get_by_role("button", name="Tagesnotizen")).to_be_visible()
                        page.get_by_role("button", name="Tagesnotizen").click()
                        expect(page.get_by_text("Privater Modul-Pilot")).to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.get_by_role("button", name="Module", exact=True).click()
                        page.get_by_role("button", name="Deaktivieren").click()
                        expect(page.get_by_role("button", name="Tagesnotizen")).to_have_count(0)
                        assert page.context.request.get("http://127.0.0.1:5173/api/modules/day_notes/entries").status == 404
                        assert page.context.request.get("http://127.0.0.1:5173/api/modules/day_notes/ui.js").status == 404
                        tools = page.context.request.get("http://127.0.0.1:5173/api/tools").json()
                        assert all(item["name"] != "day_notes__list_notes" for item in tools)
                        assert page.context.request.post(
                            "http://127.0.0.1:5173/api/runs",
                            headers={"x-jarvis-csrf": token},
                            data={"tool_name": "day_notes__list_notes", "args": {}}).status == 422
                        page.get_by_role("button", name="Aktivieren").click()
                        page.get_by_role("button", name="Tagesnotizen").click()
                        expect(page.get_by_text("Privater Modul-Pilot")).to_be_visible()
                        page.get_by_role("button", name="Module", exact=True).click()
                        page.get_by_label("Absoluter Pfad zum Paketordner").fill(
                            str(upgrade_source.absolute()))
                        page.get_by_role("button", name="Deaktivieren").click()
                        page.get_by_role("button", name="Aktualisieren").click()
                        expect(page.get_by_text("Version 0.2.0", exact=False)).to_be_visible()
                        expect(page.get_by_role("status").get_by_text("Modul aktualisiert.", exact=False)).to_be_visible()
                        page.get_by_role("button", name="Jarvis neu laden").click()
                        page.get_by_role("button", name="Module", exact=True).click()
                        page.get_by_role("button", name="Aktivieren").click()
                        page.get_by_role("button", name="Tagesnotizen").click()
                        expect(page.get_by_text("Privater Modul-Pilot")).to_be_visible()
                        page.get_by_role("button", name="Module", exact=True).click()
                        page.get_by_role("button", name="Deaktivieren").click()
                        page.get_by_role("button", name="Entfernen (Daten behalten)").click()
                        expect(page.get_by_text("Keine Module installiert.", exact=False)).to_be_visible()
                        page.get_by_label("Absoluter Pfad zum Paketordner").fill(
                            str(args.module_source.absolute()))
                        page.get_by_role("button", name="Installieren").click()
                        page.get_by_role("button", name="Aktivieren").click()
                        page.get_by_role("button", name="Tagesnotizen").click()
                        expect(page.get_by_text("Privater Modul-Pilot")).to_be_visible()
                        (data / "modules" / "day_notes" / "ui.js").write_text(
                            "// changed after installation", encoding="utf-8")
                        page.reload()
                        page.get_by_role("button", name="Module", exact=True).click()
                        expect(page.get_by_role("alert").filter(
                            has_text="Paketdateien wurden seit Installation verändert")).to_be_visible()
                        expect(page.get_by_role("button", name="Tagesnotizen")).to_have_count(0)
                        page.get_by_role("button", name="Deaktivieren").click()
                        page.get_by_role("button", name="Entfernen (Daten behalten)").click()
                        page.get_by_label("Absoluter Pfad zum Paketordner").fill(
                            str(args.module_source.absolute()))
                        page.get_by_role("button", name="Installieren").click()
                        page.get_by_role("button", name="Aktivieren").click()
                        page.get_by_role("button", name="Tagesnotizen").click()
                        expect(page.get_by_text("Privater Modul-Pilot")).to_be_visible()
                        print(json.dumps({"module_ui": "private_bundle_loaded",
                                          "activation": "immediate",
                                          "disabled_api_and_ui": "blocked",
                                          "disabled_tool": "blocked",
                                          "data_after_reinstall": "retained",
                                          "package_upgrade": "data_retained",
                                          "modified_package": "blocked_and_recovered",
                                          "mobile_width": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
