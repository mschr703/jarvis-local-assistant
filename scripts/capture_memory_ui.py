"""Capture reproducible memory review screenshots in Edge at two widths."""

import os
import re
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
from jarvis.memory_review import MemoryReviewStore


def contrast(first: str, second: str) -> float:
    def luminance(color: str) -> float:
        values = [int(value) / 255 for value in re.findall(r"\d+", color)[:3]]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
                  for value in values]
        return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))
    a, b = sorted((luminance(first), luminance(second)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"before", "after"}:
        raise SystemExit("Usage: capture_memory_ui.py before|after")
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Screenshot-Test stoppen")
    phase = sys.argv[1]
    with TemporaryDirectory(prefix="jarvis-memory-screen-") as temporary:
        root = Path(temporary)
        db = Database(root / "data" / "jarvis.db")
        chat = db.create_chat()
        statements = [
            "Ich wohne in Bremen und besuche dort eine Schule.",
            "Ich mag kurze Antworten mit konkreten Beispielen.",
            "Mein Lieblingsfach ist Biologie und ich lerne es intensiv.",
        ]
        sources = [db.add_message(chat["id"], "user", text) for text in statements]
        snapshot = db.add_message(chat["id"], "assistant", "Verstanden.")
        store = MemoryReviewStore(db)
        items = store.create_batch(chat, snapshot["id"], [
            {"message_id": source["id"], "message_created_at": source["created_at"],
             "source_quote": text, "candidate_text": text}
            for source, text in zip(sources, statements, strict=True)
        ])
        by_text = {item["candidate_text"]: item for item in items}
        accepted = by_text[statements[0]]
        rejected = by_text[statements[1]]
        store.decide(accepted["id"], accepted["version"], "accept", statements[0])
        store.decide(rejected["id"], rejected["version"], "reject")
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(root / "data"), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct")
        with (root / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                                       cwd=ROOT, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT)
            try:
                wait_http_ready(process, timeout=240)
                output = ROOT / "docs" / "screenshots"
                output.mkdir(parents=True, exist_ok=True)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge", headless=True)
                    try:
                        for width, label in ((390, "mobile"), (1280, "desktop")):
                            page = browser.new_page(viewport={"width": width, "height": 900},
                                                    device_scale_factor=1)
                            page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                            page.get_by_role("button", name="Gedächtnis", exact=True).click()
                            expect(page.get_by_label("Gespräch für Erinnerungen")).to_have_value(
                                chat["id"], timeout=30_000)
                            page.screenshot(path=str(output / f"g08-memory-{phase}-{label}.png"),
                                            full_page=True)
                            if phase == "after":
                                assert not overflow_nodes(page), overflow_nodes(page)
                                for selector in (".memory-status-pending", ".memory-status-accepted",
                                                 ".memory-status-rejected", ".memory-filters button[aria-pressed=true]",
                                                 ".memory-source small"):
                                    colors = page.locator(selector).first.evaluate(
                                        "(element) => { const own = getComputedStyle(element); "
                                        "const surface = own.backgroundColor === 'rgba(0, 0, 0, 0)' "
                                        "? getComputedStyle(element.parentElement).backgroundColor "
                                        ": own.backgroundColor; return [own.color, surface]; }")
                                    assert contrast(*colors) >= 4.5, (selector, colors, contrast(*colors))
                                pending = page.get_by_role("group", name="Erinnerungen nach Status filtern").get_by_role(
                                    "button", name="Offen 1")
                                pending.focus()
                                pending.press("Enter")
                                expect(pending).to_have_attribute("aria-pressed", "true")
                                expect(page.locator(".memory-proposal")).to_have_count(1)
                                expect(page.locator(".memory-proposal-pending")).to_have_count(1)
                                all_filter = page.get_by_role("group", name="Erinnerungen nach Status filtern").get_by_role(
                                    "button", name="Alle 3")
                                all_filter.focus()
                                all_filter.press("Space")
                                expect(page.locator(".memory-proposal")).to_have_count(3)
                                if width == 1280:
                                    page.get_by_role("button", name="Bestätigen", exact=True).click()
                                    expect(page.get_by_text("Erinnerung bewusst gespeichert.")).to_be_visible()
                                    expect(page.locator(".memory-proposal-pending")).to_have_count(0)
                                    expect(page.get_by_role("group", name="Erinnerungen nach Status filtern")
                                           .get_by_role("button", name="Bestätigt 2")).to_be_visible()
                            page.close()
                    finally:
                        browser.close()
                print(f"G08 {phase}: mobile and desktop screenshots saved")
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
