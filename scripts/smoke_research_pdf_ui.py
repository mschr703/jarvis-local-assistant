"""PDF-Fixture durch echte Edge-Oberfläche, Backend-Extraktion und lokales Ollama prüfen."""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright
from smoke_research_ui import overflow_nodes
from smoke_ui import ROOT, port_free, wait_ready

sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests"))
from jarvis.db import Database
from jarvis.research import ResearchStore
from test_research_pdf import text_pdf


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> None:
    if not port_free(8765) or not port_free(5173):
        raise RuntimeError("Jarvis vor der isolierten PDF-UI-Probe stoppen")
    with TemporaryDirectory(prefix="jarvis-pdf-ui-") as temporary:
        root = Path(temporary)
        fixture = root / "source.pdf"
        fixture.write_bytes(text_pdf(
            "Europa Clipper untersucht den Jupitermond Europa und seine eisige Oberflaeche im Jahr 2024. " * 5))
        environment = os.environ.copy()
        environment.update(JARVIS_DATA_DIR=str(root / "data"), JARVIS_PROVIDER="ollama",
                           JARVIS_OLLAMA_MODEL="qwen3:4b-instruct", JARVIS_TIMEZONE="Europe/Paris",
                           JARVIS_PDF_FIXTURE=str(fixture), PYTHONPATH=str(ROOT / "backend"))
        vite = ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
        commands = [
            [sys.executable, str(ROOT / "scripts" / "research_pdf_fixture_server.py")],
            [shutil.which("node") or "node", str(vite), "frontend", "--host", "127.0.0.1",
             "--config", "frontend/vite.config.ts"],
        ]
        logs = [(root / "backend.log").open("w", encoding="utf-8"),
                (root / "frontend.log").open("w", encoding="utf-8")]
        processes = []
        try:
            for command, log in zip(commands, logs, strict=True):
                processes.append(subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log,
                                                  stderr=subprocess.STDOUT))
            wait_ready(processes[0], timeout=120)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                try:
                    page = browser.new_page(viewport={"width": 390, "height": 844})
                    page.set_default_timeout(240_000)
                    page.goto("http://127.0.0.1:5173/", wait_until="domcontentloaded")
                    page.locator("nav button").filter(has_text="Recherche").click(timeout=10_000)
                    page.get_by_label("Frage", exact=True).fill("Was untersucht Europa Clipper?")
                    page.get_by_role("button", name="Recherche starten").click()
                    report = page.get_by_role("region", name="Recherchebericht")
                    expect(report).to_be_visible(timeout=240_000)
                    expect(report.get_by_text("Text-PDF", exact=False)).to_be_visible()
                    expect(report.get_by_text("Seiten 1–1 von 1", exact=False)).to_be_visible()
                    expect(report.get_by_text("Jupitermond Europa", exact=False).first).to_be_visible()
                    assert not overflow_nodes(page), overflow_nodes(page)
                    stored = ResearchStore(Database(root / "data" / "jarvis.db")).list()
                    assert len(stored) == 1
                    assert stored[0]["sources"][0]["media_type"] == "pdf"
                    assert stored[0]["claims"][0]["evidence"][0]["quote"] in stored[0]["sources"][0]["text"]
                    assert stored[0]["interpretation"], stored[0]["errors"]
                    page.get_by_label("Eigene öffentliche Quellenlinks", exact=False).fill(
                        "https://example.com/report.pdf\nhttps://example.com/overview")
                    expect(page.get_by_label("Suchanfrage (optional)")).to_be_disabled()
                    page.get_by_role("button", name="Recherche starten").click()
                    expect(report.get_by_text("Direkt angegebene Quellen", exact=False)).to_be_visible(
                        timeout=240_000)
                    expect(report.locator("a[href^='https://example.com/']")).to_have_count(4)
                    expect(report.get_by_role("heading", name="Quellen nebeneinander")).to_be_visible()
                    expect(report.locator(".comparison-entry")).to_have_count(2)
                    expect(report.get_by_text("Wörter in beiden Zitaten", exact=False)).to_be_visible()
                    expect(report.get_by_role("heading", name="Zahlen als Prüfhinweis")).to_be_visible()
                    expect(report.get_by_text("Zahlen in beiden Zitaten", exact=False)).to_be_visible()
                    expect(report.get_by_text("Veröffentlichung laut Quelle (ungeprüft): 14.06.2025",
                                              exact=False).first).to_be_visible()
                    assert not overflow_nodes(page), overflow_nodes(page)
                    with page.expect_download() as downloaded:
                        report.get_by_role("button", name="Bericht als Markdown herunterladen").click()
                    download = downloaded.value
                    assert download.suggested_filename.startswith("recherche-")
                    assert download.suggested_filename.endswith(".md")
                    markdown = Path(download.path()).read_text(encoding="utf-8")
                    assert "## Quellen nebeneinander" in markdown
                    assert "## Schlussfolgerung des Modells" in markdown
                    assert "## Unsicherheit und Grenzen" in markdown
                    assert "### Zahlen als Prüfhinweis" in markdown
                    assert "https://example.com/report.pdf" in markdown
                    assert "https://example.com/overview" in markdown
                    assert "Veröffentlichung laut Quelle (ungeprüft): 14\\.06\\.2025" in markdown
                    assert "Jupitermond Europa" in markdown
                    stored_direct = ResearchStore(Database(root / "data" / "jarvis.db")).list()[0]
                    assert stored_direct["search_provider"] == "Direkte URLs"
                    assert [source["media_type"] for source in stored_direct["sources"]] == ["pdf", "web"]
                    assert len(stored_direct["claims"]) == 2
                    assert stored_direct["comparison"]["entries"][0]["media_type"] == "pdf"
                    assert stored_direct["comparison"]["entries"][1]["media_type"] == "web"
                    assert stored_direct["comparison"]["first_only_numbers"] == ["2024"]
                    assert stored_direct["comparison"]["second_only_numbers"] == ["2025"]
                    assert [item["published_at"] for item in stored_direct["sources"]] == [None, "2025-06-14"]
                    assert stored_direct["interpretation"], stored_direct["errors"]
                    page.locator("nav button").filter(has_text="Recherche").click(timeout=10_000)
                    expect(page.get_by_role("textbox", name="Frage", exact=True)).to_be_visible(timeout=10_000)
                    page.get_by_role("textbox", name="Frage", exact=True).fill(
                        "Was bedeutet Europa Clipper für das Projekt Atlas?")
                    project_select = page.get_by_role("combobox", name="Lokaler Arbeitsstand (optional)")
                    expect(project_select.get_by_role("option", name="Atlas · Version 1")).to_be_attached()
                    project_select.select_option(label="Atlas · Version 1")
                    page.get_by_role("button", name="Recherche starten").click()
                    expect(report.get_by_role("heading", name="Lokaler Projektkontext")).to_be_visible(
                        timeout=240_000)
                    expect(report.get_by_text("Entwurf freigegeben", exact=False)).to_be_visible()
                    stored_project = ResearchStore(Database(root / "data" / "jarvis.db")).list()[0]
                    assert stored_project["project_context"]["project"] == "Atlas"
                    assert stored_project["project_context"]["version"] == 1
                    assert len(stored_project["claims"]) == 2
                    assert stored_project["interpretation"], stored_project["errors"]
                    assert not overflow_nodes(page), overflow_nodes(page)
                    page.get_by_label("Eigene öffentliche Quellenlinks", exact=False).fill("")
                    project_select.select_option("")
                    page.get_by_role("textbox", name="Frage", exact=True).fill(
                        "Was untersucht Europa Clipper auf Europa und mit welchen Instrumenten?")
                    page.get_by_role("button", name="Teilfragen mit Ollama vorschlagen").click()
                    first_suggestion = page.get_by_label("Suchanfrage (optional)")
                    second_suggestion = page.get_by_label("Zweite Suchfrage (optional)")
                    expect(first_suggestion).not_to_have_value("", timeout=60_000)
                    expect(second_suggestion).not_to_have_value("", timeout=60_000)
                    assert first_suggestion.input_value() != second_suggestion.input_value()
                    page.get_by_label("Suchanfrage (optional)").fill("Europa Clipper Eis")
                    page.get_by_label("Zweite Suchfrage (optional)").fill("Europa Clipper Instrumente")
                    page.get_by_role("button", name="Recherche starten").click()
                    expect(report.get_by_text("Zwei Suchanfragen", exact=False)).to_be_visible(timeout=240_000)
                    expect(report.get_by_text("Gefunden über PDF-Fixture-Suche", exact=False).first).to_be_visible()
                    expect(report.get_by_text("Gefunden über HTML-Fixture-Suche", exact=False).first).to_be_visible()
                    expect(report.locator(".comparison-entry")).to_have_count(2)
                    with page.expect_download() as downloaded_multi:
                        report.get_by_role("button", name="Bericht als Markdown herunterladen").click()
                    multi_markdown = Path(downloaded_multi.value.path()).read_text(encoding="utf-8")
                    assert "Gefunden über PDF\\-Fixture\\-Suche" in multi_markdown
                    assert "Gefunden über HTML\\-Fixture\\-Suche" in multi_markdown
                    stored_multi = ResearchStore(Database(root / "data" / "jarvis.db")).list()[0]
                    assert stored_multi["search_provider"] == "Zwei Suchanfragen"
                    assert [item["found_by_query"] for item in stored_multi["sources"]] == [
                        "Europa Clipper Eis", "Europa Clipper Instrumente"]
                    assert len(stored_multi["claims"]) == 2
                    assert not overflow_nodes(page), overflow_nodes(page)
                    evaluation = report.get_by_role("region", name="Bericht bewerten")
                    evaluation.get_by_role("combobox", name="Ergebnis", exact=True).select_option("partial")
                    evaluation.get_by_role("combobox", name="Quellenbewertung").select_option("unclear")
                    evaluation.get_by_role("textbox", name="Begründung").fill(
                        "Instrumente sind in den kurzen Zitaten nicht vollständig erklärt.")
                    evaluation.get_by_role("checkbox", name="Als Fehler-Testfall sichern").check()
                    evaluation.get_by_role("textbox", name="Erwartetes Ergebnis").fill(
                        "Instrumente mit passender zweiter Fundstelle genauer nennen.")
                    evaluation.get_by_role("button", name="Bewertung speichern").click()
                    expect(evaluation.get_by_text("Berichtsbewertung gespeichert", exact=False)).to_be_visible()
                    expect(page.get_by_role("heading", name="Gesicherte Recherche-Testfälle")).to_be_visible()
                    stored_evaluated = ResearchStore(Database(root / "data" / "jarvis.db")).get(
                        stored_multi["id"])
                    assert stored_evaluated["evaluation"]["version"] == 1
                    assert stored_evaluated["evaluation"]["regression_case"] == 1
                    page.reload(wait_until="domcontentloaded")
                    page.locator("nav button").filter(has_text="Recherche").click(timeout=10_000)
                    report = page.get_by_role("region", name="Recherchebericht")
                    expect(report.get_by_role("region", name="Bericht bewerten").get_by_role(
                        "textbox", name="Begründung")).to_have_value(
                            "Instrumente sind in den kurzen Zitaten nicht vollständig erklärt.")
                    expect(page.get_by_text("Instrumente mit passender zweiter Fundstelle genauer nennen.",
                                            exact=False).first).to_be_visible()
                    with page.expect_download() as downloaded_cases:
                        page.get_by_role("button", name="Testfälle als JSONL herunterladen").click()
                    case_download = downloaded_cases.value
                    assert case_download.suggested_filename.endswith(".jsonl")
                    lines = Path(case_download.path()).read_text(encoding="utf-8").splitlines()
                    assert len(lines) == 1
                    case_snapshot = json.loads(lines[0])
                    assert case_snapshot["format_version"] == 1
                    assert case_snapshot["evaluation"]["version"] == 1
                    assert case_snapshot["evaluation"]["expected_result"].startswith("Instrumente")
                    assert case_snapshot["report"]["sources"][0]["url"]
                    assert "project_context_json" not in case_snapshot["report"]
                    page.locator("nav button").filter(has_text="Arbeitsstände").click()
                    projects = page.locator("section.card").filter(
                        has=page.get_by_role("heading", name="Gespeicherte Arbeitsstände"))
                    projects.get_by_role("button", name="Öffnen").click()
                    expect(page.get_by_role("heading", name="Recherchen mit diesem Arbeitsstand")).to_be_visible()
                    expect(page.get_by_text("Was bedeutet Europa Clipper für das Projekt Atlas?",
                                            exact=True)).to_be_visible()
                    expect(page.get_by_role("button", name="Bericht öffnen")).to_have_count(1)
                    page.get_by_role("button", name="Bericht öffnen").click()
                    expect(report.get_by_role(
                        "heading", name="Was bedeutet Europa Clipper für das Projekt Atlas?")).to_be_visible()
                    expect(report.get_by_text("Entwurf freigegeben", exact=False)).to_be_visible()
                    assert not overflow_nodes(page), overflow_nodes(page)
                    print(json.dumps({"pdf_pages": 1, "verified_claims": len(stored[0]["claims"]),
                                      "direct_sources": len(stored_direct["sources"]),
                                      "project_snapshot": stored_project["project_context"]["version"],
                                      "two_queries": len(stored_multi["sources"]),
                                      "evaluation_version": stored_evaluated["evaluation"]["version"],
                                      "evaluation_jsonl_lines": len(lines),
                                      "project_linked_reports": 1,
                                      "ollama_answer": bool(stored_project["interpretation"]),
                                      "mobile_width": "fits"}))
                finally:
                    browser.close()
        finally:
            for process in reversed(processes):
                stop_process(process)
            for log in logs:
                log.close()
            deadline = time.monotonic() + 10
            while (not port_free(8765) or not port_free(5173)) and time.monotonic() < deadline:
                time.sleep(0.2)
            if not port_free(8765) or not port_free(5173):
                raise RuntimeError("PDF-UI-Probe ließ einen Testserver laufen")


if __name__ == "__main__":
    main()
