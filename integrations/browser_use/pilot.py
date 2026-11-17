"""Bounded local browser-use read pilot; no login, click, typing, file or cloud tools."""

import asyncio
import base64
import ipaddress
import json
import os
import socket
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

READ_ACTIONS = {"navigate", "extract", "screenshot", "done"}
BLOCKED_ACTIONS = [
    "search", "go_back", "wait", "click", "input", "upload_file", "switch",
    "close", "search_page", "find_elements", "scroll", "send_keys", "find_text",
    "save_as_pdf", "dropdown_options", "select_dropdown", "write_file",
    "replace_file", "read_file", "evaluate",
]


def public_host(url: str) -> str:
    parts = urlsplit(url)
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.fragment or parts.port not in (None, 443)):
        raise ValueError("Nur öffentliche HTTPS-Seiten ohne Zugangsdaten oder Fragment")
    host = parts.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith((".local", ".localhost", ".internal")):
        raise ValueError("Lokale Seite gesperrt")
    public_ip(host)
    return host


def public_ip(host: str) -> str:
    addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("Private oder reservierte Zieladresse gesperrt")
    ipv4 = [item for item in addresses if ":" not in item]
    return min(ipv4 or addresses)


def chromium_executable() -> Path:
    override = os.environ.get("JARVIS_BROWSER_EXECUTABLE")
    if override:
        candidate = Path(override)
        if candidate.is_file():
            return candidate
    root = Path.home() / "AppData" / "Local" / "ms-playwright"
    candidates = sorted(root.glob("chromium-*/chrome-win64/chrome.exe"), reverse=True)
    if not candidates:
        raise RuntimeError("Lokales Playwright-Chromium fehlt")
    return candidates[0]


async def read(url: str, model: str, screenshot_path: Path) -> dict:
    host = public_host(url)
    os.environ["ANONYMIZED_TELEMETRY"] = "false"
    os.environ.pop("BROWSER_USE_API_KEY", None)
    from browser_use import Agent, Browser, ChatOllama, Tools

    tools = Tools(exclude_actions=BLOCKED_ACTIONS)
    actions = set(tools.registry.registry.actions)
    if not actions <= READ_ACTIONS or not {"navigate", "done"} <= actions:
        raise RuntimeError(f"Unerwartete Browseraktionen: {sorted(actions)}")
    with tempfile.TemporaryDirectory(
        prefix="jarvis-browser-use-", ignore_cleanup_errors=True,
    ) as temporary:
        browser = Browser(
            executable_path=chromium_executable(), headless=True,
            user_data_dir=Path(temporary) / "profile",
            allowed_domains=[host], enable_default_extensions=False,
            accept_downloads=False, keep_alive=False,
            args=["--no-first-run", "--no-default-browser-check"],
        )
        agent = Agent(
            task=(f"Öffne ausschließlich {url}. Lies den sichtbaren Seiteninhalt und gib "
                  "einen kurzen wörtlichen Ausschnitt wieder. Keine weiteren Seiten öffnen."),
            llm=ChatOllama(model=model, ollama_options={"num_ctx": 8192, "temperature": 0}),
            browser=browser, tools=tools,
            use_vision=False, use_judge=False, enable_planning=False,
            max_actions_per_step=1, max_failures=1, llm_timeout=60, step_timeout=75,
        )
        try:
            history = await asyncio.wait_for(agent.run(max_steps=4), timeout=150)
            urls = [item for item in history.urls() if item]
            if any(urlsplit(item).hostname != host for item in urls):
                raise ValueError("Browser wurde auf eine andere Domain geleitet")
            screenshot = next((item for item in reversed(history.screenshots()) if item), None)
            if screenshot:
                screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                screenshot_path.write_bytes(base64.b64decode(screenshot))
            return {
                "url": url, "host": host, "visited_urls": urls,
                "actions": history.action_names(), "steps": history.number_of_steps(),
                "is_done": history.is_done(), "is_successful": history.is_successful(),
                "text": (history.final_result() or "")[:12_000],
                "errors": [item for item in history.errors() if item],
                "screenshot_path": str(screenshot_path) if screenshot else None,
            }
        finally:
            await browser.stop()


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("Usage: pilot.py URL OLLAMA_MODEL SCREENSHOT_PATH")
    try:
        result = asyncio.run(read(sys.argv[1], sys.argv[2], Path(sys.argv[3])))
        print("__JARVIS_JSON__" + json.dumps(result, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 - Worker reports failures through its JSON line.
        print("__JARVIS_JSON__" + json.dumps({"error": str(exc)[:500]}, ensure_ascii=False))
        raise SystemExit(1) from None
