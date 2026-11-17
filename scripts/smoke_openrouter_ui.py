"""Isolated Edge acceptance for an explicitly approved OpenRouter chat."""

import getpass
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from jarvis.vault import CredentialVault
from playwright.sync_api import expect, sync_playwright
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, stop_test_server, wait_ready


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten Cloud-Test stoppen")
    key = os.environ.get("JARVIS_OPENROUTER_API_KEY") or getpass.getpass("OpenRouter-Key: ")
    if not key:
        raise SystemExit("OpenRouter-Key fehlt")
    use_vault = "--vault" in sys.argv[1:]

    with TemporaryDirectory(prefix="jarvis-openrouter-ui-") as temporary:
        root = Path(temporary)
        data_dir = root / "profile"
        if use_vault:
            CredentialVault(data_dir).add("openrouter", "default", "api_key", key)
        environment = os.environ.copy()
        environment.update(
            JARVIS_DATA_DIR=str(data_dir), JARVIS_PROVIDER="openrouter",
            JARVIS_OPENROUTER_MODEL="openai/gpt-4o-mini",
            JARVIS_DAILY_BUDGET_USD="0.02",
            JARVIS_MAX_OUTPUT_TOKENS="48",
        )
        if use_vault:
            environment.pop("JARVIS_OPENROUTER_API_KEY", None)
        else:
            environment["JARVIS_OPENROUTER_API_KEY"] = key
        with (root / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "scripts" / "dev.py")], cwd=ROOT,
                env=environment, stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                wait_ready(process, timeout=120)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge", headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 390, "height": 844})
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        page.get_by_label("Nachricht", exact=True).fill(
                            "Antworte kurz auf Deutsch: Was ist zwei plus zwei?")
                        send = page.get_by_role("button", name="Senden", exact=True)
                        expect(send).to_be_disabled()
                        page.get_by_label(
                            "Diese Nachricht und den bisherigen Chatverlauf an OpenRouter senden").check()
                        expect(send).to_be_enabled()
                        send.click()
                        usage = page.locator(".message.assistant .usage")
                        expect(usage).to_have_count(1, timeout=120_000)
                        expect(usage).to_contain_text("openai/gpt-4o-mini")
                        expect(usage).to_contain_text("USD")
                        assert not overflow_nodes(page), overflow_nodes(page)
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        page.locator(".chat-list-item > button").first.click()
                        expect(page.locator(".message.assistant .usage")).to_have_count(1)
                        page.get_by_role("button", name="Übersicht", exact=True).click()
                        receipt = page.locator(".settings .item-row").filter(
                            has_text="Chat · openrouter / openai/gpt-4o-mini")
                        expect(receipt).to_have_count(1)
                        expect(receipt).to_contain_text("abgeschlossen")
                        assert key not in page.content()
                        assert not overflow_nodes(page), overflow_nodes(page)
                        if use_vault:
                            page.get_by_role("button", name="Zugänge", exact=True).click()
                            row = page.locator(".item-row").filter(has_text="openrouter · default")
                            expect(row).to_have_count(1)
                            row.get_by_role("button", name="Entziehen…").click()
                            row.get_by_role("button", name="Lokal entziehen").click()
                            expect(row).to_have_count(0)
                            assert page.request.get("http://127.0.0.1:8765/health").json()[
                                "configured"] is False
                        print(json.dumps({"cloud_chat": True, "consent_required": True,
                                          "receipt_after_reload": True, "edge_390px": True,
                                          "vault_revoke": use_vault}))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
