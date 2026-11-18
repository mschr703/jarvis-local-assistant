"""A user can inspect and clear local search vectors without deleting sources."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from smoke_code_project_ui import wait_ui
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, stop_test_server

sys.path.insert(0, str(ROOT / "backend"))
from jarvis.db import Database
from jarvis.documents import DocumentStore
from jarvis.semantic_cache import PersistentEmbeddingCache


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-semantic-cache-ui-") as temporary:
        root = Path(temporary)
        db = Database(root / "data" / "jarvis.db")
        DocumentStore(db).add_memory("Private Notiz über den Termin", "Eigene Eingabe",
                                     "local_only")
        cache = PersistentEmbeddingCache(db.path.parent / "semantic-embeddings.sqlite")
        text_hash = hashlib.sha256("Private Notiz über den Termin".encode()).hexdigest()
        cache.write("a" * 64, {text_hash: [1.0, 0.0]})
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(db.path.parent), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="fixture")
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
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Suche", exact=True).click()
                        expect(page.get_by_text("Suchcache: 1 gespeicherte Vektoren",
                                                exact=False)).to_be_visible(timeout=30000)
                        page.get_by_role("button", name="Suchcache leeren").click()
                        expect(page.get_by_text("Suchcache: 0 gespeicherte Vektoren",
                                                exact=False)).to_be_visible(timeout=30000)
                        assert cache.read("a" * 64, [text_hash]) == {}
                        page.get_by_label("Gemeinsame Suche").fill("Private Notiz")
                        page.get_by_role("button", name="Alle lokalen Quellen durchsuchen").click()
                        expect(page.get_by_role("region", name="Suchergebnisse")).to_contain_text(
                            "Private Notiz über den Termin", timeout=30000)
                        assert not overflow_nodes(page), overflow_nodes(page)
                        with db.connect() as connection:
                            assert connection.execute("SELECT COUNT(*) FROM model_usage").fetchone()[0] == 0
                        print(json.dumps({"cleared": True, "source_kept": True,
                                          "mobile_width": "fits", "cloud_calls": 0}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
