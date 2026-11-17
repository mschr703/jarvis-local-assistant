# Beitragen

Jarvis Local ist ein Windows-Projekt. Die [README](README.md) erklärt Voraussetzungen, Einrichtung und Start. Für Änderungen eine eigene Branch verwenden und den betroffenen Nutzerablauf nachvollziehbar beschreiben.

## Vor einem Pull Request

```powershell
python -m uv run --project backend pytest backend/tests -q
python -m uv run --project backend ruff check backend scripts
pnpm --dir frontend test
pnpm --dir frontend build
python scripts/audit_public.py
```

Für Änderungen am gesamten lokalen Ablauf zusätzlich `python -m uv run --project backend python scripts/e2e_local.py` ausführen. Die Edge-Klicktests in `scripts/smoke_*.py` brauchen teilweise Ollama, Edge und freie Ports 8765/5173; optionale Integrationen haben eigene Voraussetzungen. In der PR-Beschreibung angeben, welche Tests tatsächlich liefen.

## Daten und Grenzen

Keine echten Zugangsdaten, `.env`, Datenbanken, Backups, Gesprächsverläufe, Exporte oder persönliche Screenshots einreichen. Für Tests synthetische Daten verwenden. Das Projekt bindet nur an Loopback; neue Netz- oder Schreibwerkzeuge brauchen enge Ziele, Laufzeitgrenzen, eine sichtbare Freigabe und Tests für Ablehnungspfade. Das [Sicherheitsdokument](docs/SECURITY.md) beschreibt die aktuellen Vertrauensgrenzen.

Abgeschlossene Funktionen erhalten einen eigenen Commit und Push. Dokumentation und `docs/STATUS.md` mit dem tatsächlich geprüften Stand aktualisieren.
