# Projektstatus

Stand: 17.11.2026, 18:19 Uhr Europe/Berlin. Dieses Dokument beschreibt den [öffentlichen Quellstand](https://github.com/mschr703/jarvis-local-assistant). Die technische Abnahme der MVP-Module steht in [ABNAHME.md](ABNAHME.md).

## Nutzbar

- Lokaler Windows-Start mit Ollama, SSE-Chat, bewusst freigegebene Dokumente, belegte Entwürfe, Aufgaben und In-App-Erinnerungen, registrierte PC-/Browserwerkzeuge sowie überprüftes Backup/Restore (M00–M07).
- Zusätzliche lokale Funktionen: versionierte Chat-/Entwurfs-/Planregeln, Schreibproben und Stilprofile, Essays, Lernmodus, bewusst gepflegte Erinnerungen, Projekt-/Dateigraph, Recherche, begrenzte Sprach- und CAD-Integrationen. Einige Integrationen brauchen eine gesonderte Installation; siehe [README](../README.md).
- Optionaler OpenRouter-Chat mit Freigabe je Anfrage, Modellpreisprüfung, Tagesbudget und Kostenbeleg. Ein echter kleiner Modellaufruf und ein Edge-Klickpfad mit Antwort, Reload und Kostenbeleg wurden am 17.11.2026 geprüft. Der Cloud-Schlüssel kann als Prozessvariable oder benutzergebunden verschlüsselt im Windows-Tresor liegen; Standard bleibt Ollama.
- Ein persönliches Schreibprofil mit eigenen Beispielen. Der Anzeigename ist einstellbar und standardmäßig „Mein Stil“. Die technische Kennung `Maxim` bleibt für die Kompatibilität älterer lokaler Daten und API-Aufrufe erhalten.
- Beantwortete, belegte Lernrunden können nach ausdrücklicher Prüfung als lokale UTF-8-CSV-Lernkarten mit Quellenzitat und SHA-256 exportiert werden. Gleiche Frage und gleicher Quellenhash ergeben nur eine Karte; die Ansicht lädt die 50 zuletzt geänderten Runden.
- Eine lokale Übungsprüfung kann aus diesen Fragen mit Zeitlimit gestartet werden. Lösungen und Quellen erscheinen nach Abgabe; die Selbsteinschätzung zeigt Wiederholungsfragen. Der Prüfungsverlauf bleibt im Tab und wird nicht gespeichert.

## Zuletzt geprüft

- Die erste öffentliche Version wurde als elternloser Commit `aa1092c` auf `main` veröffentlicht; der frühere lokale Verlauf wurde nicht übertragen. Das Repository ist ohne Anmeldung als öffentlich abrufbar. Der korrigierte große Edge-Klicktest liegt in Commit `add1611`, ebenfalls gepusht.
- Nach Einführung des einstellbaren Profilnamens und des Windows-Einrichtungshelfers: vollständige Backend-Regression mit 517 bestandenen Tests und zwei Windows-Symlink-Skips, zwölf Frontendtests, Ruff und TypeScript/Vite-Build bestanden. Der Einrichtungshelfer lief erfolgreich mit `-SkipModelDownload`; eine vorhandene lokale `.env` wurde nicht überschrieben. Die erneut ausgeführten 24 Konfigurations-/API-Tests bestanden. `scripts/smoke_named_style_ui.py` und `scripts/smoke_public_profile_ui.py` bestanden mit Edge/Ollama, inklusive 390-Pixel-Ansicht und gespeicherter Umbenennung. `scripts/e2e_local.py` bestand mit Chat, Entwurf, Wissen, Aufgabe, öffentlichem Browserabruf und Schema-52-Restore; `scripts/smoke_stop.py` bestätigte Health/UI und freie Ports nach dem Stopp. Ein erster Stil-Klicktest musste nach Start des noch nicht laufenden Ollama-Dienstes wiederholt werden und bestand dann.
- Der echte Edge-Cloud-Test mit temporärem Tresor prüfte bewusste Freigabe, Antwort, Kostenbeleg, Reload, mobile Breite und lokales Entziehen. Der Schlüssel und Nutzerdaten sind nicht Bestandteil des Repositories.
- Ein frischer GitHub-Clone hatte genau einen Commit und keine `.env` oder Datenbank. `scripts/setup.ps1 -SkipModelDownload` installierte daraus 108 Python- und 103 Frontend-Pakete und erstellte die lokale `.env`. Produktionsbuild, 24 gezielte API-/Konfigurationstests, `scripts/e2e_local.py` mit echtem Ollama/Schema-52-Restore und `scripts/smoke_stop.py` mit freien Ports bestanden. Der große Edge-Klicktest traf zunächst einen veralteten Selektor, der alle Chat-Buttons statt Gesprächseinträge zählte; nach Korrektur bestand er vollständig mit Chat, Dokumenten, Entwurf, Erinnerung, Werkzeugen, Freigaben und Mobilansicht. `scripts/audit_public.py` meldete null Funde in 278 veröffentlichbaren Dateien; der verdeckte Vergleich mit dem tatsächlich verwendeten OpenRouter-Schlüssel ergab ebenfalls null Funde. Die sechs eingecheckten Screenshots wurden als synthetische UI-Beispiele visuell geprüft.

- Der Windows-CI-Workflow prüft öffentliche Pushes und Pull Requests ohne Secrets. GitHub-Lauf [44132746512](https://github.com/mschr703/jarvis-local-assistant/actions/runs/44132746512) ist in Backend und Frontend grün. Der erste Lauf fand eine Ruff-Importsortierung im neuen öffentlichen Audit-Skript; Commit `daf366e` korrigierte sie. P01 steht in Trello auf „Erledigt“.
- A36a: 16 Frontendtests, TypeScript/Vite-Build, Ruff und lokaler Public-Audit (281 Dateien, null Funde) bestanden. `scripts/smoke_learning_ui.py` erzeugte mit echtem Ollama eine belegte Frage, korrigierte und beantwortete sie, lud die CSV in Edge herunter, prüfte Frage/Pfad/Hash/Zitat und bestätigte die 390-Pixel-Ansicht ohne Überlauf. Commit `fd6d287` ist veröffentlicht; GitHub-Lauf [44134315243](https://github.com/mschr703/jarvis-local-assistant/actions/runs/44134315243) ist vollständig grün. Die Trello-Karte ist erledigt.
- A37a: 19 Frontendtests, TypeScript/Vite-Build und echter Edge/Ollama-Pfad mit belegter Frage, abgegebener Prüfung, Selbsteinschätzung und 390-Pixel-Ansicht bestanden. Der Prüfungsablauf änderte die gespeicherte Lernrunde nicht. Der öffentliche CI-Lauf für dieses Feature steht noch aus.

## Offene Arbeiten

1. P00, A79b, P01 und A36a sind mit öffentlichen Commits und Testbelegen in Trello auf „05 · Erledigt“ und `complete=true` gesetzt.
2. A37a separat pushen und den anschließenden Windows-CI-Lauf prüfen; danach die Trello-Karte abschließen.
3. Weitere fachliche Trello-Teilstufen nach Voraussetzungen auswählen und mit Tests und Dokumentation umsetzen.

## Betriebsgrenzen

Die App bindet nur an Loopback und ist nicht für Fernzugriff eingerichtet. Freigegebene Dateien bleiben außerhalb des Backups. Gescannte PDFs brauchen OCR außerhalb der App. Erinnerungen werden bei ausgeschaltetem Gerät nach dem nächsten Start nachgereicht und erscheinen in der geöffneten App. Modellantworten, Zitate, Stil und CAD-Maße vor Verwendung prüfen. Details zu Datenflüssen und Netzwerkgrenzen stehen in [SECURITY.md](SECURITY.md).
