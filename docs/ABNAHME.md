# Abnahme und Testnachweise

Stand: 17.11.2026. Die Trello-Karten M00–M07 beschreiben den Kernumfang. Alle aufgeführten Funktionen wurden lokal auf Windows geprüft; optionale Integrationen haben eigene Voraussetzungen. Diese Tabelle beschreibt den ausführbaren Quellstand, keine Garantie für fremde Installationen.

| Modul | Ablauf | Automatisierter oder manueller Nachweis |
| --- | --- | --- |
| [M00](https://trello.com/c/s9z82JNX) | Windows-Monorepo, Start/Stopp, Health und Oberfläche | `scripts/smoke_stop.py`, Backendtests, Frontend-Build; frischer Checkout geprüft |
| [M01](https://trello.com/c/XqY3Ju8s) | Persistenter SSE-Chat mit Ollama, Abbruch, Token-/Kostenbeleg, optional OpenRouter | `scripts/smoke_ollama.py`, `scripts/smoke_chat_cancel.py`, `backend/tests/test_provider.py`, `backend/tests/test_budget.py`; kleiner OpenRouter-Live-Test am 17.11.2026 |
| [M02](https://trello.com/c/wVhrEuZv) | Begrenzte Mehrschrittpläne mit Werkzeugbeleg und Schreibfreigabe | `scripts/smoke_plan_isolated.py`, `backend/tests/test_engine.py`, `backend/tests/test_approval_flow.py` |
| [M03](https://trello.com/c/eMaDANa8) | Freigegebene TXT-/MD-/Text-PDF-Dateien, Suche, Quellenhash und bewusstes Wissen | `scripts/smoke_draft_ollama.py`, `backend/tests/test_documents.py` |
| [M04](https://trello.com/c/iuGkZcCi) | Stilproben und bearbeitbare, belegte Entwürfe mit versioniertem TXT-/MD-/EML-Export | `scripts/smoke_ui.py`, `backend/tests/test_drafts.py` |
| [M05](https://trello.com/c/nEMCxlpl) | Zeitzonenfeste Aufgaben und einmalige In-App-Erinnerungen | `scripts/smoke_reminder_awake.py`, `scripts/smoke_reminder_restart.py`, `backend/tests/test_tasks.py` |
| [M06](https://trello.com/c/jnwkTUll) | Registrierte Apps, freigegebene Dateien und begrenztes Lesen öffentlicher Webseiten | `scripts/smoke_desktop.py`, `scripts/smoke_open_file.py`, `backend/tests/test_desktop.py` |
| [M07](https://trello.com/c/4cdleyLg) | Die vier Kernabläufe, Backup/Restore und dokumentierter Betrieb | `scripts/e2e_local.py`, `scripts/smoke_ui.py`, `backend/tests/test_backup.py` |

## Zusätzliche geprüfte Abläufe

- **OpenRouter (R02):** `scripts/smoke_openrouter_live.py` erhielt echte gestreamte Textteile und einen Provider-Kostenbeleg; der Beleg blieb nach Neustart erhalten. Ohne Cloud-Freigabe und mit einer `local_only`-Fundstelle blockierte die API vor dem Modellaufruf. `scripts/smoke_openrouter_ui.py --vault` prüfte die bewusste Freigabe und den Windows-Tresor über die Edge-Oberfläche. Der Live-Test verursacht eine Modellgebühr.
- **Persönlicher Stil:** `backend/tests/test_named_style_profile.py`, `backend/tests/test_style_polish.py` und `scripts/smoke_named_style_ui.py` prüfen Version, eigene Schreibproben, Vorschau, bewusste Übernahme und mobile Ansicht. `scripts/smoke_public_profile_ui.py` prüft den allgemeinen Standardnamen, Umbenennung und Reload. Die technische Kennung `Maxim` bleibt aus Kompatibilitätsgründen erhalten.
- **Dateigraph und Obsidian:** `backend/tests/test_file_graph.py` und `scripts/smoke_file_graph_ui.py` prüfen AST/Markdown-Kanten, Pfadvorschau, Export, Hashvergleich und Rücknahme.
- **Öffentliche Recherche:** `backend/tests/test_agent_reach.py`, `scripts/smoke_agent_reach_ui.py` und `scripts/smoke_dynamic_browser_ui.py` prüfen begrenztes Lesen ausgewählter YouTube-/GitHub-Quellen beziehungsweise dynamischer HTTPS-Seiten.
- **Lernen und Gedächtnis:** `backend/tests/test_learning.py`, `backend/tests/test_memory_review.py`, `backend/tests/test_memory_context.py` und die entsprechenden Edge-Proben prüfen Belege, ausdrückliche Bestätigung und Versionen. `frontend/src/learningCards.test.ts` prüft den lokalen CSV-Export einschließlich Duplikaten und Formelzellen. `frontend/src/exam.test.ts` prüft Auswahl, Frist und Auswertung der lokalen Übungsprüfung. `scripts/smoke_learning_ui.py` prüft CSV-Download und Prüfung aus einer echten beantworteten Ollama-Lernrunde in Edge.
- **Sprache und CAD:** `backend/tests/test_voice.py`, `backend/tests/test_speech.py`, `backend/tests/test_cad.py`, `scripts/smoke_voice_ui.py`, `scripts/smoke_speech_real.py` und `scripts/smoke_cad_ui.py` prüfen die optionalen lokalen Integrationen. Für die realen Proben müssen die jeweiligen Dienste beziehungsweise Laufzeiten installiert sein.

## Selbst prüfen

Im Projektordner unter PowerShell:

```powershell
python -m uv sync --project backend --frozen
pnpm --dir frontend install --frozen-lockfile
python -m uv run --project backend pytest backend/tests -q
python -m uv run --project backend ruff check backend scripts
pnpm --dir frontend test
pnpm --dir frontend build
python -m uv run --project backend python scripts/e2e_local.py
```

Der lokale Edge/Ollama-Klicktest ist mit `python -m uv run --project backend python scripts/smoke_ui.py` reproduzierbar, wenn Ollama und Edge bereitstehen und die Standardports frei sind. Die [Startanleitung](../README.md) erklärt die Voraussetzungen. `docs/STATUS.md` dokumentiert den zuletzt tatsächlich ausgeführten Teststand.

## Grenzen

Eine Modellantwort kann Fehler enthalten; Quellenbeleg und Stilvorschau ersetzen keine fachliche Prüfung. Gescannte PDFs werden ohne OCR nicht als Text erfasst. Windows-Erinnerungen erscheinen in der geöffneten App und werden nach einem ausgeschalteten Gerät nachgereicht. Die Browserwerkzeuge sind auf öffentliche Ziele und begrenzte Leseaktionen ausgelegt. Kein Postfach, Kalender, automatischer E-Mail-Versand oder Fernzugriff ist verbunden.
