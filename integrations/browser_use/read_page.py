"""Deterministic JavaScript page read with the isolated browser-use Browser runtime."""

import asyncio
import base64
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from pilot import chromium_executable, public_host, public_ip


def request_allowed(url: str, host: str) -> bool:
    try:
        requested = urlsplit(url)
        return (requested.scheme == "https" and requested.hostname == host
                and requested.port in (None, 443) and not requested.username
                and not requested.password)
    except ValueError:
        return False


async def read(url: str, screenshot_path: Path) -> dict:
    host = public_host(url)
    os.environ["ANONYMIZED_TELEMETRY"] = "false"
    os.environ.pop("BROWSER_USE_API_KEY", None)
    from browser_use import Browser
    from playwright.async_api import async_playwright

    pinned_ip = public_ip(host)

    with tempfile.TemporaryDirectory(
        prefix="jarvis-browser-read-", ignore_cleanup_errors=True,
    ) as temporary:
        browser = Browser(
            executable_path=chromium_executable(), headless=True,
            user_data_dir=Path(temporary) / "profile",
            allowed_domains=[host], enable_default_extensions=False,
            accept_downloads=False, keep_alive=False,
            args=["--no-first-run", "--no-default-browser-check",
                  "--disable-background-networking",
                  "--disable-features=ServiceWorker",
                  f"--host-resolver-rules=MAP {host} {pinned_ip}"],
        )
        playwright = None
        try:
            await browser.start()
            playwright = await async_playwright().start()
            cdp_browser = await playwright.chromium.connect_over_cdp(browser.cdp_url)

            async def only_selected_host(route):
                if request_allowed(route.request.url, host):
                    await route.continue_()
                else:
                    await route.abort()

            for context in cdp_browser.contexts:
                await context.add_init_script(
                    "Object.defineProperty(navigator, 'serviceWorker', { value: undefined });")
                await context.route("**/*", only_selected_host)
            await browser.navigate_to(url)
            await asyncio.sleep(2)
            final_url = await browser.get_current_page_url()
            if not request_allowed(final_url, host):
                raise ValueError("Browser wurde auf eine andere Domain geleitet")
            state = await browser.get_browser_state_summary()
            text = state.dom_state.llm_representation()[:12_000]
            if not text.strip():
                raise ValueError("Dynamische Seite enthält keinen lesbaren Text")
            screenshot = state.screenshot
            if not screenshot:
                raise ValueError("Browser lieferte keinen Screenshot")
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            screenshot_path.write_bytes(base64.b64decode(screenshot))
            return {"url": final_url, "title": state.title[:300], "text": text,
                    "screenshot_path": str(screenshot_path),
                    "status": "read", "steps": 1, "actions": ["navigate", "read"]}
        finally:
            if playwright is not None:
                await playwright.stop()
            await browser.stop()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: read_page.py URL SCREENSHOT_PATH")
    try:
        result = asyncio.run(asyncio.wait_for(read(sys.argv[1], Path(sys.argv[2])), 90))
        print("__JARVIS_JSON__" + json.dumps(result, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 - Worker reports failures through its JSON line.
        print("__JARVIS_JSON__" + json.dumps({"error": str(exc)[:500]}, ensure_ascii=False))
        raise SystemExit(1) from None
