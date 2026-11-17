"""Real Edge flow: Graphify AST, Markdown evidence, Obsidian export and rollback."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from smoke_research_ui import overflow_nodes
from smoke_speech_ui import wait_http_ready
from smoke_ui import ROOT, port_free, stop_test_server

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database
from jarvis.documents import DocumentStore


def main() -> None:
    if not all(port_free(port) for port in (8765, 5173)):
        raise RuntimeError("Jarvis vor dem isolierten Test stoppen")
    with TemporaryDirectory(prefix="jarvis-graphify-ui-") as temporary:
        root = Path(temporary)
        source = root / "source"
        source.mkdir()
        (source / "a.py").write_text("from b import helper\n", encoding="utf-8")
        (source / "b.py").write_text("def helper():\n    return 42\n", encoding="utf-8")
        (source / "start.md").write_text("Siehe [[next]].\n", encoding="utf-8")
        (source / "next.md").write_text("# Weiter\n", encoding="utf-8")
        vault = root / "vault"
        (vault / ".obsidian").mkdir(parents=True)
        (vault / ".obsidian" / "app.json").write_bytes(b'{"safe":true}')
        (vault / "Eigene Notiz.md").write_bytes(b"bleibt")
        data = root / "data"
        folder = DocumentStore(Database(data / "jarvis.db")).add_folder(
            str(source), "local_only"
        )
        env = os.environ.copy()
        env.update(JARVIS_DATA_DIR=str(data), JARVIS_PROVIDER="ollama",
                   JARVIS_OLLAMA_MODEL="qwen3:4b-instruct")
        with (root / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                                       cwd=ROOT, env=env, stdout=log,
                                       stderr=subprocess.STDOUT)
            try:
                wait_http_ready(process, timeout=120)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge", headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 390, "height": 844})
                        page.set_default_timeout(120_000)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Datei-Graph", exact=True).click()
                        try:
                            expect(page.get_by_label("Freigegebener Ordner")).to_have_value(
                                folder["id"], timeout=30_000)
                        except AssertionError:
                            print(page.locator("body").inner_text()[:3000], file=sys.stderr)
                            raise
                        page.get_by_role("button", name="Graph erstellen").click()
                        expect(page.get_by_text("Kanten mit Beleg")).to_be_visible(timeout=120_000)
                        expect(page.get_by_text("imports_from", exact=False)).to_be_visible()
                        expect(page.get_by_text("links_to", exact=False)).to_be_visible()
                        expect(page.get_by_text("EXTRACTED", exact=False).first).to_be_visible()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.get_by_label("Absoluter Pfad zu einem vorhandenen Obsidian-Vault").fill(
                            str(vault))
                        page.get_by_role("button", name="Pfad prüfen").click()
                        target = vault / "Jarvis Graphify" / ("Graph-" + folder["id"][:12])
                        expect(page.get_by_text(str(target), exact=True)).to_be_visible(
                            timeout=120_000)
                        assert not target.exists()
                        page.get_by_role("button", name="Jetzt exportieren").click()
                        expect(page.get_by_text("Exportiert nach", exact=False)).to_be_visible(
                            timeout=120_000)
                        assert (target / "Graph.md").is_file()
                        assert (target / "manifest.json").is_file()
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Datei-Graph", exact=True).click()
                        expect(page.get_by_text(str(target), exact=True)).to_be_visible(
                            timeout=30_000)
                        page.get_by_role("button", name="Eigene Dateien entfernen").click()
                        expect(page.get_by_text("Keine aktiven Exporte.")).to_be_visible()
                        assert not target.exists()
                        assert (vault / ".obsidian" / "app.json").read_bytes() == b'{"safe":true}'
                        assert (vault / "Eigene Notiz.md").read_bytes() == b"bleibt"
                        assert not overflow_nodes(page), overflow_nodes(page)
                        print(json.dumps({"graphify_ast": True, "markdown_link": True,
                                          "evidence": True, "path_preview": True,
                                          "vault_collision_preserved": True,
                                          "rollback_after_reload": True, "edge_390px": "fits"}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
