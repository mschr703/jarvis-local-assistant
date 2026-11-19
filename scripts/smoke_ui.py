"""Isolierter Browser-Klicktest der vier MVP-Abläufe mit echtem Ollama."""

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def port_free(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) != 0


def wait_ready(process: subprocess.Popen, timeout: int = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Jarvis-Testserver wurde vor dem Start beendet")
        try:
            with urlopen("http://127.0.0.1:8765/health", timeout=2) as response:
                health = json.load(response)
            with urlopen("http://127.0.0.1:5173/", timeout=2):
                pass
            if health["configured"]:
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    raise RuntimeError("Jarvis-Testserver oder Ollama-Modell wurde nicht bereit")


def stop_test_server(process: subprocess.Popen) -> None:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell:
        try:
            subprocess.run([shell, "-NoProfile", "-File", str(ROOT / "scripts" / "stop.ps1")],
                           cwd=ROOT, capture_output=True, text=True, check=False, timeout=90)
        except subprocess.TimeoutExpired:
            pass
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    deadline = time.monotonic() + 20
    while (not port_free(8765) or not port_free(5173)) and time.monotonic() < deadline:
        time.sleep(0.2)
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis-Testserver blieb nach dem Stopp auf einem Port erreichbar")


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor dem isolierten UI-Test mit scripts/stop.ps1 stoppen")
    with TemporaryDirectory(prefix="jarvis-ui-smoke-") as temporary:
        root = Path(temporary)
        sources = root / "sources"
        sources.mkdir()
        (sources / "orion.txt").write_text(
            "Projekt Orion verwendet SQLite für die lokale Datenhaltung.", encoding="utf-8")
        data_dir = root / "data"
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(data_dir), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct")
        with (root / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "dev.py")],
                                       cwd=ROOT, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT)
            try:
                wait_ready(process)
                with sync_playwright() as playwright:
                    expect.set_options(timeout=15_000)
                    browser = playwright.chromium.launch(channel="msedge", headless=True)
                    try:
                        page = browser.new_page()
                        page.set_default_timeout(60_000)
                        page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                        try:
                            page.get_by_role("heading", name="Jarvis ist bereit.").wait_for(timeout=90_000)
                        except Exception as exc:
                            raise AssertionError(f"Startoberfläche blieb unbereit: {page.locator('main').inner_text()[:1000]}") from exc

                        settings_timezone = page.locator(
                            ".settings > .form-grid").first.get_by_label("Zeitzone (IANA)")
                        page.get_by_label("Max. Eingabezeichen").fill("4000")
                        page.get_by_role("button", name="Einstellungen speichern").click()
                        page.get_by_text("Gespeichert.", exact=True).wait_for()
                        settings_timezone.fill("Europe/Paris")
                        page.evaluate("""() => {
                          const original = window.fetch.bind(window);
                          window.__settingsOriginalFetch = original;
                          window.__delayedSettingsPatch = {seen: false, release: null, calls: 0};
                          window.fetch = async (input, init) => {
                            const response = await original(input, init);
                            if (String(input).endsWith('/api/settings') && init?.method === 'PATCH') {
                              window.__delayedSettingsPatch.calls += 1;
                              if (!window.__delayedSettingsPatch.seen) {
                                window.__delayedSettingsPatch.seen = true;
                                await new Promise(resolve => { window.__delayedSettingsPatch.release = resolve; });
                              }
                            }
                            return response;
                          };
                        }""")
                        page.get_by_role("button", name="Einstellungen speichern").click()
                        page.wait_for_function("window.__delayedSettingsPatch?.seen")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        page.get_by_role("button", name="Übersicht", exact=True).click()
                        expect(page.get_by_role("button", name="Speichere…")).to_be_disabled()
                        settings_timezone.fill("Europe/Berlin")
                        page.get_by_role("button", name="Speichere…").evaluate(
                            "button => { button.click(); button.click(); }")
                        assert page.evaluate("window.__delayedSettingsPatch.calls") == 1
                        page.evaluate("window.__delayedSettingsPatch.release()")
                        page.get_by_text("Vorherige Werte gespeichert; neuere Eingaben sind noch ungespeichert.").wait_for()
                        expect(settings_timezone).to_have_value("Europe/Berlin")
                        page.get_by_role("button", name="Einstellungen speichern").click()
                        page.get_by_text("Gespeichert.", exact=True).wait_for()
                        assert page.evaluate("window.__delayedSettingsPatch.calls") == 2
                        page.evaluate("window.fetch = window.__settingsOriginalFetch")
                        settings_timezone.fill("Europe/Paris")
                        page.route("**/api/health", lambda route: route.fulfill(
                            status=200, content_type="application/json", body=json.dumps({
                                "status": "degraded", "provider": "ollama",
                                "model": "qwen3:4b-instruct", "configured": True,
                                "message": "UI-Test: Dienststatus geändert",
                            })))
                        page.get_by_role("button", name="Status prüfen").click()
                        page.get_by_role("heading", name="Ein Dienst ist ausgefallen.").wait_for(timeout=20_000)
                        expect(settings_timezone).to_have_value("Europe/Paris")
                        page.unroute("**/api/health")
                        page.get_by_role("button", name="Status prüfen").click()
                        page.get_by_role("heading", name="Jarvis ist bereit.").wait_for(timeout=20_000)
                        page.route("**/api/health", lambda route: route.abort())
                        page.get_by_role("button", name="Status prüfen").click()
                        page.get_by_role("heading", name="Ein Dienst ist ausgefallen.").wait_for()
                        page.get_by_text("Offline", exact=True).wait_for()
                        expect(settings_timezone).to_have_value("Europe/Paris")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        expect(page.get_by_label("Nachricht")).to_be_disabled()
                        page.get_by_role("button", name="Übersicht", exact=True).click()
                        expect(settings_timezone).to_have_value("Europe/Paris")
                        page.unroute("**/api/health")
                        page.get_by_role("button", name="Status prüfen").click()
                        page.get_by_role("heading", name="Jarvis ist bereit.").wait_for()
                        settings_timezone.fill("Europe/Berlin")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        page.get_by_role("button", name="Neues Gespräch").evaluate(
                            "button => { button.click(); button.click(); }")
                        expect(page.locator(".chat-list-item")).to_have_count(1)
                        page.get_by_role("button", name="Umbenennen").click()
                        page.get_by_label("Gesprächstitel").fill("Projekt Orion Gespräch")
                        page.get_by_role("button", name="Speichern", exact=True).click()
                        expect(page.locator(".chat-list-item")).to_contain_text("Projekt Orion Gespräch")
                        page.reload(wait_until="domcontentloaded")
                        page.get_by_role("heading", name="Jarvis ist bereit.").wait_for(timeout=90_000)
                        page.get_by_role("button", name="Chat", exact=True).click()
                        expect(page.locator(".chat-list-item")).to_contain_text("Projekt Orion Gespräch")
                        page.get_by_role("button", name="Projekt Orion Gespräch").click()
                        rejected_message = "A" * 3700
                        page.get_by_label("Nachricht").fill(rejected_message)
                        page.get_by_role("button", name="Senden", exact=True).click()
                        page.get_by_role("alert").wait_for()
                        expect(page.get_by_label("Nachricht")).to_have_value(rejected_message)
                        aborted_message = "Bitte nicht vor dem Senden verlieren"
                        page.evaluate("""() => {
                          const original = window.fetch;
                          window.__jarvisOriginalFetch = original;
                          window.fetch = (input, options) => {
                            if (!String(input).includes('/stream')) return original(input, options);
                            return new Promise((_, reject) => {
                              const signal = options?.signal;
                              const fail = () => reject(new DOMException('abgebrochen', 'AbortError'));
                              if (signal?.aborted) fail();
                              else signal?.addEventListener('abort', fail, { once: true });
                            });
                          };
                        }""")
                        page.get_by_label("Nachricht").fill(aborted_message)
                        page.get_by_role("button", name="Senden", exact=True).click()
                        page.get_by_role("button", name="Stoppen").click()
                        expect(page.get_by_label("Nachricht")).to_have_value(aborted_message, timeout=20_000)
                        page.evaluate("window.fetch = window.__jarvisOriginalFetch")
                        navigation_message = "Beim Seitenwechsel erhalten"
                        page.evaluate("""() => {
                          const original = window.fetch;
                          window.__jarvisOriginalFetch = original;
                          window.__jarvisNavigationAborted = false;
                          window.fetch = (input, options) => {
                            if (!String(input).includes('/stream')) return original(input, options);
                            return new Promise((_, reject) => {
                              const signal = options?.signal;
                              const fail = () => {
                                window.__jarvisNavigationAborted = true;
                                reject(new DOMException('abgebrochen', 'AbortError'));
                              };
                              if (signal?.aborted) fail();
                              else signal?.addEventListener('abort', fail, { once: true });
                            });
                          };
                        }""")
                        page.get_by_label("Nachricht").fill(navigation_message)
                        page.get_by_role("button", name="Senden", exact=True).click()
                        page.get_by_role("button", name="Stoppen").wait_for()
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.wait_for_function("window.__jarvisNavigationAborted === true")
                        page.get_by_role("button", name="Chat", exact=True).click()
                        expect(page.get_by_label("Nachricht")).to_have_value(navigation_message)
                        page.evaluate("window.fetch = window.__jarvisOriginalFetch")
                        page.get_by_role("button", name="Chat-Regeln", exact=True).click()
                        page.get_by_role("heading", name="Aktive Fassung").locator("..").get_by_text(
                            "Version 1 · Revision 1").wait_for()
                        page.get_by_label("Neue Chat-Regel").fill("Antworte auf Deutsch und knapp.")
                        page.get_by_role("button", name="Neue Fassung aktivieren").click()
                        page.get_by_role("heading", name="Aktive Fassung").locator("..").get_by_text(
                            "Version 2 · Revision 2").wait_for()
                        page.get_by_role("button", name="Chat-Skills", exact=True).click()
                        page.get_by_label("Skill-Name").fill("Kurze Projektantwort")
                        page.get_by_label("Skill-Inhalt").fill("Nenne zuerst den nächsten Schritt.")
                        page.get_by_role("button", name="Skill anlegen").click()
                        page.get_by_text("Skill-Fassung gespeichert.", exact=False).wait_for()
                        page.get_by_text("Version 1 aktiv", exact=False).wait_for()
                        page.get_by_role("button", name="Chat", exact=True).click()
                        page.get_by_label("Chat-Skill").select_option(label="Kurze Projektantwort · v1")
                        page.get_by_label("Nachricht").fill("Antworte mit einem kurzen deutschen Satz.")
                        page.get_by_role("button", name="Senden", exact=True).click()
                        expect(page.locator(".message.assistant .usage")).to_have_count(1, timeout=180_000)
                        expect(page.locator(".message.assistant .usage")).to_contain_text("Chat-Regel v2")
                        expect(page.locator(".message.assistant .usage")).to_contain_text("Skill v1")
                        expect(page.locator(".message.assistant .usage")).to_contain_text("Anfrage")
                        expect(page.locator(".message.user")).to_have_count(1)
                        page.get_by_label("Gespräche suchen").fill("kurzen deutschen")
                        page.get_by_role("button", name="Suchen", exact=True).click()
                        page.get_by_role("button", name="Treffer anzeigen").wait_for()
                        page.evaluate("""() => {
                          window.__chatScrollOriginal = Element.prototype.scrollIntoView;
                          window.__chatScrolledTo = null;
                          Element.prototype.scrollIntoView = function(...args) {
                            window.__chatScrolledTo = this.id;
                            return window.__chatScrollOriginal.apply(this, args);
                          };
                        }""")
                        page.get_by_role("button", name="Treffer anzeigen").click()
                        page.wait_for_function("window.__chatScrolledTo?.startsWith('chat-message-')")
                        expect(page.locator(".message.user")).to_contain_text("kurzen deutschen")
                        page.evaluate("() => { Element.prototype.scrollIntoView = window.__chatScrollOriginal; }")
                        page.get_by_role("button", name="Alle", exact=True).click()
                        with page.expect_download() as chat_export_info:
                            page.get_by_role("button", name="Gespräch als Markdown herunterladen").click()
                        chat_export = chat_export_info.value
                        assert chat_export.suggested_filename.startswith("gespraech-")
                        chat_markdown = Path(chat_export.path()).read_text(encoding="utf-8")
                        assert "Antworte mit einem kurzen deutschen Satz." in chat_markdown
                        assert "keine Anhangdateien" in chat_markdown
                        page.get_by_role("button", name="Antwort bewerten").click()
                        page.get_by_label("Erfolg der Antwort").select_option("partial")
                        page.get_by_label("Quellenlage der Antwort").select_option("not_applicable")
                        page.get_by_label("Begründung der Antwortbewertung").fill(
                            "Der Satz beantwortet die Frage, enthält aber keine kurze Begründung.")
                        page.get_by_label("Als Antwort-Testfall sichern").check()
                        page.get_by_label("Erwartete Antwort im Testfall").fill(
                            "Kurzer deutscher Satz mit Begründung.")
                        page.get_by_role("button", name="Antwortbewertung speichern").click()
                        page.get_by_text("Antwortbewertung gespeichert.", exact=False).wait_for()
                        answer_cases = page.get_by_role("heading", name="Gesicherte Antwort-Testfälle").locator("..")
                        answer_cases.get_by_text("Erwartet: Kurzer deutscher Satz mit Begründung.").wait_for()
                        page.get_by_role("button", name="Chat-Regeln", exact=True).click()
                        page.get_by_role("heading", name="Fassungen und Rückkehr").locator("..").locator(
                            ".item-row").filter(has_text="Version 1").get_by_role(
                            "button", name="Diese Fassung aktivieren").click()
                        page.get_by_role("heading", name="Aktive Fassung").locator("..").get_by_text(
                            "Version 1 · Revision 3").wait_for()
                        page.get_by_role("heading", name="Aktivierungsverlauf").locator("..").get_by_text(
                            "Version 2 → 1").wait_for()
                        page.get_by_role("button", name="Chat", exact=True).click()
                        page.get_by_role("heading", name="Gesicherte Antwort-Testfälle").locator("..").get_by_text(
                            "Erwartet: Kurzer deutscher Satz mit Begründung.").wait_for()
                        page.get_by_role("button", name="Übersicht", exact=True).click()
                        page.locator(".settings .item-row").filter(has_text="Chat · ollama").wait_for()
                        page.get_by_label("Max. Eingabezeichen").fill("24000")
                        page.get_by_role("button", name="Einstellungen speichern").click()
                        page.get_by_text("Gespeichert.", exact=True).wait_for()

                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_label("Absoluter Ordnerpfad").fill(str(sources))
                        page.get_by_label("Erinnerung", exact=True).fill("Ungespeicherte Erinnerung")
                        page.get_by_label("Quelle der Erinnerung").fill("Ungespeicherte Quelle")
                        page.get_by_role("button", name="Übersicht", exact=True).click()
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        expect(page.get_by_label("Absoluter Ordnerpfad")).to_have_value(str(sources))
                        expect(page.get_by_label("Erinnerung", exact=True)).to_have_value("Ungespeicherte Erinnerung")
                        expect(page.get_by_label("Quelle der Erinnerung")).to_have_value("Ungespeicherte Quelle")
                        page.get_by_label("Erinnerung", exact=True).fill("")
                        page.get_by_label("Quelle der Erinnerung").fill("")
                        page.get_by_role("button", name="Freigeben", exact=True).click()
                        try:
                            expect(page.get_by_role("button", name="Index aktualisieren")).to_be_visible(timeout=20_000)
                        except AssertionError as exc:
                            notices = page.locator(".section-stack .notice").all_inner_texts()
                            raise AssertionError(f"Ordnerfreigabe ohne sichtbaren Indexknopf: {notices}") from exc
                        page.evaluate("""() => {
                          const original = window.fetch;
                          window.__jarvisOriginalFetch = original;
                          window.__jarvisIndexCalls = 0;
                          window.fetch = (input, options) => {
                            if (!String(input).includes('/index')) return original(input, options);
                            window.__jarvisIndexCalls += 1;
                            return new Promise((_, reject) => { window.__jarvisRejectIndex = reject; });
                          };
                        }""")
                        page.get_by_role("button", name="Index aktualisieren").click()
                        page.get_by_role("button", name="Indexiere…").wait_for()
                        page.get_by_role("button", name="Übersicht", exact=True).click()
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        pending_index_button = page.get_by_role("button", name="Indexiere…")
                        expect(pending_index_button).to_be_disabled()
                        pending_index_button.evaluate("button => { button.click(); button.click(); }")
                        assert page.evaluate("window.__jarvisIndexCalls") == 1
                        page.evaluate("""() => {
                          window.__jarvisRejectIndex(new Error('Indexprobe beendet'));
                          window.fetch = window.__jarvisOriginalFetch;
                        }""")
                        page.get_by_text("Indexprobe beendet").wait_for()
                        page.get_by_role("button", name="Index aktualisieren").wait_for()
                        page.get_by_role("button", name="Index aktualisieren").evaluate(
                            "button => { button.click(); button.click(); }")
                        page.get_by_text("Index aktualisiert:").wait_for()
                        page.locator(".item-row small").filter(has_text="SHA-256").first.wait_for()
                        page.get_by_label("Dokumente durchsuchen").fill("SQLite")
                        page.get_by_role("button", name="Suchen", exact=True).click()
                        page.get_by_role("button", name="Ausschließen", exact=True).click()
                        page.locator(".item-row small").filter(has_text="Ausgeschlossen").wait_for()
                        page.get_by_role("button", name="Suchen", exact=True).click()
                        expect(page.locator(".source-result")).to_have_count(0)
                        page.get_by_role("button", name="Einbeziehen", exact=True).click()
                        pending_index = page.locator(".item-row small").filter(has_text="Index ausstehend")
                        pending_index.wait_for()
                        page.get_by_role("button", name="Index aktualisieren").click()
                        pending_index.wait_for(state="hidden")
                        page.get_by_role("button", name="Suchen", exact=True).click()
                        expect(page.locator(".source-result")).to_have_count(1)
                        page.evaluate("""() => {
                          const original = window.fetch.bind(window);
                          window.__searchOriginalFetch = original;
                          window.__delayedSearch = {seen: false, release: null};
                          window.fetch = async (input, init) => {
                            const response = await original(input, init);
                            if (String(input).includes('/api/search?q=SQLite') && !window.__delayedSearch.seen) {
                              window.__delayedSearch.seen = true;
                              await new Promise(resolve => { window.__delayedSearch.release = resolve; });
                            }
                            return response;
                          };
                        }""")
                        page.get_by_role("button", name="Suchen", exact=True).click()
                        page.wait_for_function("window.__delayedSearch?.seen")
                        page.get_by_label("Dokumente durchsuchen").fill("keintreffer123")
                        page.get_by_role("button", name="Suchen", exact=True).click()
                        expect(page.locator(".source-result")).to_have_count(0)
                        page.evaluate("window.__delayedSearch.release()")
                        page.wait_for_timeout(250)
                        expect(page.locator(".source-result")).to_have_count(0)
                        page.evaluate("window.fetch = window.__searchOriginalFetch")
                        page.get_by_label("Dokumente durchsuchen").fill("SQLite")
                        page.get_by_role("button", name="Suchen", exact=True).click()
                        expect(page.locator(".source-result")).to_have_count(1)
                        page.locator(".source-result input[type=checkbox]").first.check()
                        page.get_by_role("button", name="Index aktualisieren").click()
                        page.get_by_role("button", name="Mit 1 Fundstelle(n) Entwurf erstellen").wait_for(state="hidden")
                        page.get_by_role("button", name="Suchen", exact=True).click()
                        page.locator(".source-result input[type=checkbox]").first.check()
                        page.get_by_role("button", name="Mit 1 Fundstelle(n) Entwurf erstellen").click()
                        expect(page.get_by_role("button", name="Profil speichern")).to_be_enabled()
                        page.get_by_label("Stilprofil", exact=True).fill("Sachlich und knapp")
                        page.evaluate("""() => {
                          const original = window.fetch;
                          window.__profileOriginalFetch = original;
                          window.__profilePutCalls = 0;
                          window.fetch = async (input, options) => {
                            const response = await original(input, options);
                            if (String(input).includes('/api/style/profile') && options?.method === 'PUT') {
                              window.__profilePutCalls += 1;
                              await new Promise(resolve => { window.__releaseProfilePut = resolve; });
                            }
                            return response;
                          };
                        }""")
                        page.get_by_role("button", name="Profil speichern").evaluate(
                            "button => { button.click(); button.click(); }")
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        page.wait_for_function("window.__profilePutCalls === 1")
                        expect(page.get_by_role("button", name="Profil speichern")).to_be_disabled()
                        expect(page.get_by_label("Stilprofil", exact=True)).to_be_disabled()
                        page.evaluate("window.__releaseProfilePut(); window.fetch = window.__profileOriginalFetch")
                        page.get_by_text("Stilprofil gespeichert.").wait_for()
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        expect(page.get_by_label("Dokumente durchsuchen")).to_have_value("SQLite")
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        expect(page.get_by_label("Stilprofil", exact=True)).to_have_value("Sachlich und knapp")
                        page.evaluate("""() => {
                          const original = window.fetch.bind(window);
                          window.__staleProfileOriginalFetch = original;
                          window.__staleProfileGet = {seen: false, release: null, calls: 0};
                          window.fetch = async (input, options) => {
                            const response = await original(input, options);
                            if (String(input).endsWith('/api/style/profile') && (!options?.method || options.method === 'GET')) {
                              window.__staleProfileGet.calls += 1;
                              if (!window.__staleProfileGet.seen) {
                                window.__staleProfileGet.seen = true;
                                await new Promise(resolve => { window.__staleProfileGet.release = resolve; });
                              }
                            }
                            return response;
                          };
                        }""")
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        page.wait_for_function("window.__staleProfileGet?.seen")
                        page.get_by_label("Stilprofil", exact=True).fill("Klar und freundlich")
                        page.get_by_role("button", name="Profil speichern").click()
                        expect(page.get_by_label("Stilprofil", exact=True)).to_have_value("Klar und freundlich")
                        page.wait_for_function("window.__staleProfileGet.calls >= 2")
                        page.evaluate("window.__staleProfileGet.release(); window.fetch = window.__staleProfileOriginalFetch")
                        page.wait_for_timeout(250)
                        expect(page.get_by_label("Stilprofil", exact=True)).to_have_value("Klar und freundlich")
                        page.get_by_label("Stilprofil", exact=True).fill("Ungespeichertes Stilprofil")
                        page.get_by_label("Titel der Schreibprobe").fill("Unfertige Probe")
                        page.get_by_label("Schreibprobe", exact=True).fill("Noch nicht gespeicherter Text")
                        page.get_by_label("Cloud-Freigabe Schreibprobe").select_option("cloud_allowed")
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        expect(page.get_by_label("Stilprofil", exact=True)).to_have_value("Ungespeichertes Stilprofil")
                        expect(page.get_by_label("Titel der Schreibprobe")).to_have_value("Unfertige Probe")
                        expect(page.get_by_label("Schreibprobe", exact=True)).to_have_value("Noch nicht gespeicherter Text")
                        expect(page.get_by_label("Cloud-Freigabe Schreibprobe")).to_have_value("cloud_allowed")
                        page.get_by_label("Stilprofil", exact=True).fill("Sachlich und knapp")
                        page.get_by_role("button", name="Profil speichern").click()
                        page.get_by_text("Stilprofil gespeichert.").wait_for()
                        page.get_by_label("Entwurfstyp").select_option("text")
                        page.get_by_label("Schreibauftrag").fill("Ungespeicherter Schreibauftrag")
                        page.get_by_label("Eingefügter Kontext").fill("Ungespeicherter Kontext")
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        expect(page.get_by_label("Entwurfstyp")).to_have_value("text")
                        expect(page.get_by_label("Schreibauftrag")).to_have_value("Ungespeicherter Schreibauftrag")
                        expect(page.get_by_label("Eingefügter Kontext")).to_have_value("Ungespeicherter Kontext")
                        page.get_by_label("Entwurfstyp").select_option("email")
                        page.get_by_label("Eingefügter Kontext").fill("")
                        page.get_by_label("Titel der Schreibprobe").fill("Löschprobe")
                        page.get_by_label("Schreibprobe", exact=True).fill("Ein kurzer Beispieltext.")
                        page.get_by_label("Cloud-Freigabe Schreibprobe").select_option("local_only")
                        page.evaluate("""() => {
                          const original = window.fetch.bind(window);
                          window.__sampleOriginalFetch = original;
                          window.__delayedSamplePost = {seen: false, release: null, calls: 0};
                          window.fetch = async (input, init) => {
                            const response = await original(input, init);
                            if (String(input).endsWith('/api/style/samples') && init?.method === 'POST') {
                              window.__delayedSamplePost.calls += 1;
                              if (!window.__delayedSamplePost.seen) {
                                window.__delayedSamplePost.seen = true;
                                await new Promise(resolve => { window.__delayedSamplePost.release = resolve; });
                              }
                            }
                            return response;
                          };
                        }""")
                        page.get_by_role("button", name="Probe speichern").click()
                        page.wait_for_function("window.__delayedSamplePost?.seen")
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        expect(page.get_by_role("button", name="Speichere Probe…")).to_be_disabled()
                        page.get_by_role("button", name="Speichere Probe…").evaluate(
                            "button => { button.click(); button.click(); }")
                        assert page.evaluate("window.__delayedSamplePost.calls") == 1
                        page.evaluate("window.__delayedSamplePost.release()")
                        sample_row = page.locator(".item-row").filter(has_text="Löschprobe")
                        sample_row.wait_for()
                        expect(sample_row).to_have_count(1)
                        page.evaluate("window.fetch = window.__sampleOriginalFetch")
                        sample_row.locator("input[type=checkbox]").check()
                        sample_row.get_by_role("button", name="Löschen").click()
                        sample_row.wait_for(state="hidden")
                        page.get_by_label("Schreibauftrag").fill("Bestätige kurz die lokale SQLite-Nutzung")
                        page.get_by_role("button", name="Entwurfsregeln", exact=True).click()
                        page.get_by_role("heading", name="Entwurfsregeln").wait_for()
                        page.get_by_label("Neue Entwurfsregel").fill("Schreibe klar und in kurzen Absätzen.")
                        page.get_by_role("button", name="Neue Fassung aktivieren").click()
                        page.get_by_text("Version 2 · Revision 2").wait_for()
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        expect(page.get_by_label("Schreibauftrag")).to_have_value(
                            "Bestätige kurz die lokale SQLite-Nutzung")
                        page.evaluate("""() => {
                          const original = window.fetch;
                          window.__jarvisOriginalFetch = original;
                          window.__jarvisDraftCalls = 0;
                          window.fetch = (input, options) => {
                            if (!String(input).includes('/drafts/generate')) return original(input, options);
                            window.__jarvisDraftCalls += 1;
                            return new Promise((_, reject) => { window.__jarvisRejectDraft = reject; });
                          };
                        }""")
                        page.get_by_role("button", name="Entwurf erstellen", exact=True).click()
                        page.get_by_role("button", name="Erstelle…").wait_for()
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        pending_draft_button = page.get_by_role("button", name="Erstelle…")
                        expect(pending_draft_button).to_be_disabled()
                        pending_draft_button.evaluate("button => { button.click(); button.click(); }")
                        assert page.evaluate("window.__jarvisDraftCalls") == 1
                        page.evaluate("""() => {
                          window.__jarvisRejectDraft(new Error('Probe beendet'));
                          window.fetch = window.__jarvisOriginalFetch;
                        }""")
                        page.get_by_text("Probe beendet").wait_for()
                        page.get_by_role("button", name="Entwurf erstellen", exact=True).wait_for()
                        page.get_by_role("button", name="Entwurf erstellen", exact=True).click()
                        page.get_by_role("heading", name="Entwurf bearbeiten").wait_for(timeout=180_000)
                        page.get_by_text("Erzeugt mit Entwurfsregel v2").wait_for()
                        page.get_by_role("heading", name="Entwurf bearbeiten").locator("..").get_by_text(
                            "Projekt Orion verwendet SQLite für die lokale Datenhaltung.", exact=True).wait_for()
                        page.get_by_role("button", name="Entwurfsregeln", exact=True).click()
                        page.get_by_role("button", name="Diese Fassung aktivieren").click()
                        page.get_by_text("Version 1 · Revision 3").last.wait_for()
                        page.get_by_text("Version 2 → 1").last.wait_for()
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        page.get_by_text("Erzeugt mit Entwurfsregel v2").wait_for()
                        editor = page.get_by_label("Entwurfstext")
                        edited_draft_text = editor.input_value().rstrip() + "\n\nUI-Edit-Marker"
                        editor.fill(edited_draft_text)
                        assert page.get_by_role("button", name="EML speichern").is_disabled()
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        editor = page.get_by_label("Entwurfstext")
                        expect(editor).to_have_value(edited_draft_text)
                        assert page.get_by_role("button", name="EML speichern").is_disabled()
                        page.get_by_role("button", name="Änderung speichern").click()
                        page.get_by_text("Änderung gespeichert.").wait_for()
                        page.get_by_role("button", name="EML speichern").click()
                        page.get_by_text("Gespeichert:").wait_for()
                        exports = list((data_dir / "outputs").glob("*.eml"))
                        assert len(exports) == 1 and "UI-Edit-Marker" in exports[0].read_text()
                        page.get_by_role("button", name="Weitere Variante erstellen").click()
                        draft_buttons = page.get_by_role("heading", name="Gespeicherte Entwürfe").locator("..").locator("button")
                        expect(draft_buttons).to_have_count(2, timeout=180_000)
                        expect(page.get_by_role("button", name="Auswahl entfernen")).to_be_visible()
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.set_viewport_size({"width": 390, "height": 844})
                        assert page.evaluate("document.documentElement.scrollWidth") <= 390
                        page.set_viewport_size({"width": 1280, "height": 720})
                        page.get_by_role("button", name="Cloud für ausgewählte Ausschnitte erlauben").click()
                        page.get_by_role("button", name="Cloud-Freigabe entfernen").wait_for()
                        page.get_by_role("button", name="Entwürfe", exact=True).click()
                        expect(page.get_by_role("button", name="Auswahl entfernen")).to_have_count(0)
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_role("button", name="Cloud-Freigabe entfernen").click()
                        page.get_by_role("button", name="Cloud für ausgewählte Ausschnitte erlauben").wait_for()

                        page.get_by_role("button", name="Übersicht", exact=True).click()
                        page.get_by_role("button", name="Belege aktualisieren").click()
                        page.locator(".settings .item-row").filter(has_text="Entwurf · ollama").first.wait_for()
                        page.evaluate("""() => {
                          const original = window.fetch;
                          window.__usageOriginalFetch = original;
                          window.__usageCalls = 0;
                          window.fetch = (input, options) => {
                            if (!String(input).includes('/usage')) return original(input, options);
                            window.__usageCalls += 1;
                            if (window.__usageCalls === 1)
                              return new Promise(resolve => { window.__resolveOldUsage = resolve; });
                            return original(input, options);
                          };
                        }""")
                        page.get_by_role("button", name="Belege aktualisieren").click()
                        assert page.evaluate("window.__usageCalls") == 1
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_role("button", name="Übersicht", exact=True).click()
                        page.wait_for_function("window.__usageCalls >= 2")
                        page.locator(".settings .item-row").filter(has_text="Entwurf · ollama").first.wait_for()
                        page.evaluate("""() => {
                          window.__resolveOldUsage(new Response('[]', {
                            status: 200, headers: { 'Content-Type': 'application/json' }
                          }));
                          window.fetch = window.__usageOriginalFetch;
                        }""")
                        page.wait_for_timeout(250)
                        expect(page.locator(".settings .item-row").filter(has_text="Entwurf · ollama").first).to_be_visible()
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_label("Erinnerung", exact=True).fill("Orion nutzt SQLite")
                        page.get_by_label("Quelle der Erinnerung").fill("UI-Testquelle")
                        page.get_by_role("button", name="Erinnerung speichern").evaluate(
                            "button => { button.click(); button.click(); }")
                        memory_row = page.locator(".item-row").filter(has_text="Orion nutzt SQLite")
                        expect(memory_row).to_have_count(1, timeout=15_000)
                        memory_row.get_by_role("button", name="Bearbeiten").click()
                        page.get_by_label("Erinnerung", exact=True).fill("Orion nutzt SQLite dauerhaft")
                        page.get_by_label("Quelle der Erinnerung").fill("UI-Testquelle bearbeitet")
                        page.get_by_role("button", name="Änderung speichern").click()
                        page.get_by_text("Erinnerung geändert.").wait_for()
                        memory_row = page.locator(".item-row").filter(has_text="Orion nutzt SQLite dauerhaft")
                        memory_row.wait_for()
                        page.get_by_label("Erinnerungstext durchsuchen").fill("dauerhaft")
                        page.get_by_role("button", name="Wissen suchen").click()
                        expect(memory_row).to_have_count(1)
                        page.get_by_label("Erinnerungstext durchsuchen").fill("keintreffer123")
                        page.get_by_role("button", name="Wissen suchen").click()
                        page.get_by_text("0 Treffer für „keintreffer123“").wait_for()
                        expect(memory_row).to_have_count(0)
                        page.get_by_role("button", name="Alle anzeigen").click()
                        expect(memory_row).to_have_count(1)
                        reloaded = browser.new_page()
                        try:
                            reloaded.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                            reloaded.get_by_role("button", name="Dokumente", exact=True).click()
                            reloaded.locator(".item-row").filter(
                                has_text="Orion nutzt SQLite dauerhaft").wait_for()
                            reloaded.get_by_label("Erinnerungstext durchsuchen").fill("dauerhaft")
                            reloaded.get_by_role("button", name="Wissen suchen").click()
                            expect(reloaded.locator(".item-row").filter(has_text="Orion nutzt SQLite dauerhaft")).to_have_count(1)
                        finally:
                            reloaded.close()

                        page.get_by_role("button", name="Aufgaben", exact=True).click()
                        page.get_by_label("Aufgabe", exact=True).fill("Vorschauprobe")
                        page.get_by_label("Lokale Fälligkeit").fill("2027-10-31T02:30")
                        page.get_by_label("IANA-Zeitzone").fill("Europe/Paris")
                        try:
                            page.get_by_text("Diese lokale Zeit kommt zweimal vor", exact=False).wait_for()
                        except Exception as exc:
                            raise AssertionError(
                                "Fälligkeitsvorschau blieb aus: "
                                f"Fälligkeit={page.get_by_label('Lokale Fälligkeit').input_value()!r}, "
                                f"Zeitzone={page.get_by_label('IANA-Zeitzone').input_value()!r}, "
                                f"fold={page.get_by_label('Zeitumstellung wählen').input_value()!r}; "
                                f"{page.locator('main').inner_text()[:3000]}"
                            ) from exc
                        expect(page.get_by_role("button", name="Aufgabe speichern")).to_be_disabled()
                        page.get_by_label("Zeitumstellung wählen").select_option("1")
                        page.locator(".preview").filter(has_text="zweites Vorkommen").wait_for()
                        expect(page.get_by_role("button", name="Aufgabe speichern")).to_be_enabled()
                        page.get_by_label("Notizen").fill("Notiz bleibt beim Bereichswechsel")
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_role("button", name="Aufgaben", exact=True).click()
                        expect(page.get_by_label("Aufgabe", exact=True)).to_have_value("Vorschauprobe")
                        expect(page.get_by_label("Notizen")).to_have_value("Notiz bleibt beim Bereichswechsel")
                        expect(page.get_by_label("Lokale Fälligkeit")).to_have_value("2027-10-31T02:30")
                        expect(page.get_by_label("Zeitumstellung wählen")).to_have_value("1")
                        page.locator(".preview").filter(has_text="zweites Vorkommen").wait_for()
                        page.get_by_role("button", name="Aufgabe speichern").click()
                        ambiguous_row = page.locator(".item-row").filter(has_text="Vorschauprobe")
                        ambiguous_row.get_by_text("Notiz bleibt beim Bereichswechsel").wait_for()
                        ambiguous_row.locator("small").filter(has_text="zweites Vorkommen").wait_for()
                        ambiguous_row.locator("small").filter(has_text="Erinnerungsjob: ausstehend").wait_for()
                        ambiguous_row.get_by_role("button", name="Ändern").click()
                        expect(page.get_by_label("Zeitumstellung wählen")).to_have_value("1")
                        page.evaluate("""() => {
                          const original = window.fetch;
                          window.__taskOriginalFetch = original;
                          window.__taskPutCalls = 0;
                          window.fetch = async (input, options) => {
                            const response = await original(input, options);
                            if (String(input).includes('/api/tasks/') && options?.method === 'PUT') {
                              window.__taskPutCalls += 1;
                              await new Promise(resolve => { window.__releaseTaskPut = resolve; });
                            }
                            return response;
                          };
                        }""")
                        page.get_by_role("button", name="Änderung speichern").click()
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_role("button", name="Aufgaben", exact=True).click()
                        page.wait_for_function("window.__taskPutCalls === 1")
                        expect(page.get_by_role("button", name="Speichere…")).to_be_disabled()
                        expect(page.get_by_label("Aufgabe", exact=True)).to_be_disabled()
                        expect(ambiguous_row.get_by_role("button", name="Erledigt")).to_be_disabled()
                        ambiguous_row.get_by_role("button", name="Erledigt").evaluate(
                            "button => { button.click(); button.click(); }")
                        assert page.evaluate("window.__taskPutCalls") == 1
                        page.evaluate("window.__releaseTaskPut(); window.fetch = window.__taskOriginalFetch")
                        page.get_by_text("Aufgabe geändert.").wait_for()
                        with closing(sqlite3.connect(data_dir / "jarvis.db")) as db:
                            reminder_count = db.execute(
                                "SELECT COUNT(*) FROM reminder_jobs j JOIN tasks t ON t.id=j.task_id "
                                "WHERE t.title='Vorschauprobe'").fetchone()[0]
                        assert reminder_count == 1
                        page.evaluate("""() => {
                          const original = window.fetch.bind(window);
                          window.__delayedTaskGet = {seen: false, release: null};
                          window.fetch = async (input, init) => {
                            const response = await original(input, init);
                            if (String(input).endsWith('/api/tasks') && (!init?.method || init.method === 'GET')
                                && !window.__delayedTaskGet.seen) {
                              window.__delayedTaskGet.seen = true;
                              await new Promise(resolve => { window.__delayedTaskGet.release = resolve; });
                            }
                            return response;
                          };
                        }""")
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        page.get_by_role("button", name="Aufgaben", exact=True).click()
                        page.wait_for_function("window.__delayedTaskGet?.seen")
                        ambiguous_row.get_by_role("button", name="Löschen").click()
                        ambiguous_row.wait_for(state="hidden")
                        page.evaluate("window.__delayedTaskGet.release()")
                        page.wait_for_timeout(250)
                        expect(ambiguous_row).to_have_count(0)
                        now = datetime.now(UTC)
                        due = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
                        if (due - now).total_seconds() < 15:
                            due += timedelta(minutes=1)
                        page.get_by_label("Aufgabe", exact=True).fill("UI-Test-Erinnerung")
                        page.get_by_label("Lokale Fälligkeit").fill(due.strftime("%Y-%m-%dT%H:%M"))
                        page.get_by_label("IANA-Zeitzone").fill("UTC")
                        page.get_by_label("Zeitumstellung wählen").select_option("")
                        save_task = page.get_by_role("button", name="Aufgabe speichern")
                        expect(save_task).to_be_enabled()
                        save_task.evaluate(
                            "button => { button.click(); button.click(); }")
                        try:
                            page.get_by_text("Aufgabe gespeichert.").wait_for(timeout=10_000)
                            page.get_by_text("UI-Test-Erinnerung", exact=True).wait_for(timeout=30_000)
                            task_rows = page.get_by_role("heading", name="Alle Aufgaben").locator("..").get_by_text(
                                "UI-Test-Erinnerung", exact=True)
                            expect(task_rows).to_have_count(1)
                        except Exception:
                            print("Aufgabenansicht nach Speichern:\n" + page.locator("main").inner_text()[:2500],
                                  flush=True)
                            raise
                        page.set_viewport_size({"width": 390, "height": 844})
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                        page.set_viewport_size({"width": 1280, "height": 720})

                        page.get_by_role("button", name="Aktivität", exact=True).click()
                        page.get_by_label("Auftragsziel").fill("Ungeplanter Entwurf bleibt erhalten")
                        page.evaluate("""() => {
                          const original = window.fetch.bind(window);
                          window.__delayedRunPost = {seen: false, release: null, calls: 0};
                          window.fetch = async (input, init) => {
                            const response = await original(input, init);
                            if (String(input).endsWith('/api/runs') && init?.method === 'POST') {
                              window.__delayedRunPost.calls += 1;
                              if (!window.__delayedRunPost.seen) {
                                window.__delayedRunPost.seen = true;
                                await new Promise(resolve => { window.__delayedRunPost.release = resolve; });
                              }
                            }
                            return response;
                          };
                        }""")
                        page.get_by_role("button", name="Ausführen", exact=True).click()
                        page.wait_for_function("window.__delayedRunPost?.seen")
                        page.get_by_role("button", name="Übersicht", exact=True).click()
                        page.get_by_role("button", name="Aktivität", exact=True).click()
                        expect(page.get_by_label("Auftragsziel")).to_have_value("Ungeplanter Entwurf bleibt erhalten")
                        expect(page.get_by_role("button", name="Läuft…", exact=True)).to_be_disabled()
                        page.get_by_role("button", name="Läuft…", exact=True).evaluate(
                            "button => { button.click(); button.click(); }")
                        assert page.evaluate("window.__delayedRunPost.calls") == 1
                        page.evaluate("window.__delayedRunPost.release()")
                        try:
                            page.get_by_text("browser_read · succeeded").first.wait_for(timeout=100_000)
                        except Exception as exc:
                            browser_runs = page.evaluate("""async () => {
                              const response = await fetch('/api/runs');
                              return (await response.json()).filter(item => item.tool_name === 'browser_read')
                                .map(item => ({status: item.status, error: item.error}));
                            }""")
                            activity_text = page.get_by_role("heading", name="Aufträge").locator("..").inner_text()[:1200]
                            raise AssertionError(
                                f"Browserlauf ohne Erfolg: API={browser_runs}, UI={activity_text}"
                            ) from exc
                        run_rows = page.get_by_role("heading", name="Aufträge").locator("..").get_by_text(
                            "browser_read · succeeded", exact=True)
                        expect(run_rows).to_have_count(1)
                        browser_row = page.get_by_role("heading", name="Aufträge").locator("..").locator(
                            ".item-row").filter(has_text="browser_read · succeeded")
                        browser_row.get_by_role("button", name="Ergebnis bewerten").click()
                        page.get_by_label("Aufgabenerfolg").select_option("partial")
                        page.get_by_label("Begründung der Bewertung").fill(
                            "Die öffentliche Seite wurde geladen; der gewünschte Abschnitt fehlt.")
                        page.get_by_label("Als Fehler-Testfall sichern").check()
                        page.get_by_label("Erwartetes Ergebnis im Testfall").fill(
                            "Der gewünschte Abschnitt soll als Text erscheinen.")
                        page.get_by_role("button", name="Bewertung speichern").click()
                        page.get_by_text("Bewertung gespeichert.", exact=False).wait_for()
                        case_section = page.get_by_role("heading", name="Gesicherte Fehler-Testfälle").locator("..")
                        case_section.get_by_text("Erwartet: Der gewünschte Abschnitt soll als Text erscheinen.").wait_for()
                        page.get_by_role("button", name="Übersicht", exact=True).click()
                        page.get_by_role("button", name="Aktivität", exact=True).click()
                        case_section.get_by_text("Erwartet: Der gewünschte Abschnitt soll als Text erscheinen.").wait_for()
                        page.get_by_label("Auftragsziel").fill("Öffne die Windows-Rechner-App")
                        page.get_by_role("button", name="Planregeln", exact=True).click()
                        page.get_by_label("Neue Planregel").fill("Bevorzuge kurze, überprüfbare Pläne.")
                        page.get_by_role("button", name="Neue Fassung aktivieren").click()
                        page.get_by_text("Version 2 · Revision 2").last.wait_for()
                        page.get_by_role("button", name="Aktivität", exact=True).click()
                        expect(page.get_by_label("Auftragsziel")).to_have_value(
                            "Öffne die Windows-Rechner-App")
                        page.get_by_role("button", name="Plan vorschlagen").click()
                        proposed_plan = page.locator(".plan-card").filter(
                            has_text="Öffne die Windows-Rechner-App")
                        proposed_plan.get_by_text("Planregel v2", exact=False).wait_for(timeout=180_000)
                        proposed_plan.get_by_text("keine API-Gebühr (lokal)", exact=False).wait_for()
                        assert "Modellvorschlag:" in proposed_plan.inner_text()
                        page.get_by_role("button", name="Planregeln", exact=True).click()
                        page.get_by_role("button", name="Diese Fassung aktivieren").click()
                        page.get_by_text("Version 1 · Revision 3").last.wait_for()
                        page.get_by_role("button", name="Aktivität", exact=True).click()
                        proposed_plan.get_by_text("Planregel v2", exact=False).wait_for()
                        proposed_plan.get_by_role("button", name="Plan stoppen").click()
                        proposed_plan.get_by_text("Status: cancelled", exact=False).wait_for()
                        proposed_plan.get_by_role("button", name="Plan bewerten").click()
                        page.get_by_label("Planerfolg").select_option("failed")
                        page.get_by_label("Plan-Quellenlage").select_option("not_applicable")
                        page.get_by_label("Begründung der Planbewertung").fill(
                            "Der Plan wurde vor der Ausführung bewusst gestoppt.")
                        page.get_by_label("Als Plan-Fehler-Testfall sichern").check()
                        page.get_by_label("Erwartetes Planergebnis").fill(
                            "Rechner nach bewusster Freigabe öffnen.")
                        page.get_by_role("button", name="Planbewertung speichern").click()
                        page.get_by_text("Planbewertung gespeichert.", exact=False).wait_for()
                        plan_case_section = page.get_by_role(
                            "heading", name="Gesicherte Plan-Fehler-Testfälle").locator("..")
                        plan_case_section.get_by_text(
                            "Erwartet: Rechner nach bewusster Freigabe öffnen.").wait_for()
                        page.get_by_role("button", name="Übersicht", exact=True).click()
                        page.get_by_role("button", name="Aktivität", exact=True).click()
                        plan_case_section.get_by_text(
                            "Erwartet: Rechner nach bewusster Freigabe öffnen.").wait_for()
                        page.get_by_label("Werkzeug", exact=True).select_option("save_note")
                        page.get_by_label("Notiztitel").fill("UI-Freigabeprobe")
                        page.get_by_label("Notiztext").fill("Genau einmal speichern")
                        page.get_by_role("button", name="Ausführen", exact=True).click()
                        page.get_by_text("save_note · waiting_approval", exact=True).wait_for(timeout=30_000)
                        page.get_by_role("button", name="Freigeben", exact=True).evaluate(
                            "button => { button.click(); button.click(); }")
                        page.get_by_text("save_note · succeeded", exact=True).wait_for(timeout=30_000)
                        assert len(list((data_dir / "notes").glob("*.txt"))) == 1
                        expect(page.locator(".notice:visible").filter(
                            has_text="Freigabe passt nicht")).to_have_count(0)
                        index_events = page.get_by_role("heading", name="Protokoll").locator("..").get_by_text(
                            "folder_indexed", exact=True)
                        expect(index_events).to_have_count(3)  # Doppelklick plus zwei bewusste Neuindexierungen.
                        page.get_by_role("button", name="Übersicht", exact=True).click()
                        page.get_by_role("button", name="Erinnerungen ansehen").wait_for(timeout=120_000)
                        page.get_by_role("button", name="Erinnerungen ansehen").click()
                        page.get_by_text("Geplant:", exact=False).wait_for()
                        page.get_by_role("button", name="Gesehen").click()
                        page.get_by_role("heading", name="Neue Erinnerungen").wait_for(state="hidden")
                        page.get_by_role("button", name="Übersicht", exact=True).click()
                        expect(page.get_by_role("button", name="Erinnerungen ansehen")).to_have_count(0, timeout=3_000)
                        expect(page.get_by_role("button", name="Aufgaben", exact=True)).to_be_visible(timeout=3_000)
                        page.get_by_role("button", name="Aufgaben", exact=True).click()
                        page.locator(".item-row").filter(has_text="UI-Test-Erinnerung").locator(
                            "small").filter(has_text="Erinnerungsjob: ausgelöst").wait_for()
                        page.get_by_role("button", name="Dokumente", exact=True).click()
                        memory_row.get_by_role("button", name="Löschen").click()
                        memory_row.wait_for(state="hidden")
                        page.get_by_text("Erinnerung gelöscht.").wait_for()
                        page.set_viewport_size({"width": 390, "height": 844})
                        for section in ("Übersicht", "Chat", "Chat-Regeln", "Chat-Skills", "Dokumente", "Entwürfe", "Entwurfsregeln", "Recherche", "Aufgaben", "Zeitmodell", "Abendplanung", "Arbeitsstände", "Aktivität", "Planregeln", "Zugänge"):
                            page.get_by_role("button", name=section, exact=True).click()
                            width = page.evaluate("document.documentElement.scrollWidth")
                            if width > 390:
                                offenders = page.evaluate("""() => [...document.querySelectorAll('main *')]
                                  .filter(node => node.getBoundingClientRect().right > innerWidth + 1
                                    && getComputedStyle(node).display !== 'none')
                                  .map(node => ({
                                    tag: node.tagName, className: node.className,
                                    text: node.textContent?.trim().slice(0, 70),
                                    right: Math.round(node.getBoundingClientRect().right),
                                    width: Math.round(node.getBoundingClientRect().width),
                                    depth: [...(function* (element) { while (element) { yield element; element = element.parentElement; } })(node)].length
                                  })).sort((a, b) => b.depth - a.depth).slice(0, 12)""")
                                raise AssertionError(f"{section} läuft bei 390 Pixeln auf {width} Pixel über: {offenders}")
                        print(json.dumps({"email_export": exports[0].name,
                                          "memory": "edited_then_deleted", "reminder": "shown",
                                          "browser": "succeeded", "note_approval": "once",
                                          "selected_sample_deleted": True,
                                          "sample_navigation_busy": "single_call",
                                          "memory_double_click": "single_entry",
                                          "memory_search": "matches_and_empty",
                                          "new_chat_double_click": "once", "chat": "persisted",
                                          "chat_rename": "persisted_after_reload",
                                          "chat_search_jump": "matched_message",
                                          "chat_export": "markdown_downloaded",
                                          "dst_second_occurrence": "visible_once",
                                          "task_mobile_width": "fits",
                                          "all_mobile_sections": "fit",
                                          "unsaved_settings": "preserved",
                                          "settings_navigation": "preserved",
                                          "settings_save_race": "newer_edits_preserved",
                                          "usage_receipts": "chat_and_draft",
                                          "usage_refresh_race": "stale_ignored",
                                          "early_chat_abort": "draft_preserved",
                                          "navigation_abort": "draft_preserved",
                                          "draft_composer_navigation": "preserved",
                                          "draft_edit_navigation": "preserved",
                                          "draft_navigation_busy": "single_call",
                                          "draft_rule_version": "v2_kept_after_rollback",
                                          "plan_rule_version": "v2_kept_after_rollback",
                                          "plan_evaluation": "usage_and_case_persisted",
                                          "style_form_navigation": "preserved",
                                          "profile_save_navigation": "single_call",
                                          "profile_refresh_race": "stale_ignored",
                                          "task_form_navigation": "preserved",
                                          "task_update_navigation": "single_call",
                                          "document_form_navigation": "preserved",
                                          "index_navigation_busy": "single_call",
                                          "source_policy_invalidation": "cleared",
                                          "document_search_race": "stale_ignored",
                                          "reminder_unread_refresh": "immediate",
                                          "task_refresh_race": "stale_ignored",
                                          "activity_navigation_busy": "single_call",
                                          "reminder_job_status": "pending_then_fired"},
                                         ensure_ascii=False))
                    finally:
                        browser.close()
            finally:
                stop_test_server(process)


if __name__ == "__main__":
    main()
