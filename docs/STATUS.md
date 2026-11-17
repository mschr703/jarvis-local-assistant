# Projektstatus

Stand: 17.11.2026. Dieses Dokument beschreibt den veröffentlichten Quellstand. Die technische Abnahme der MVP-Module steht in [ABNAHME.md](ABNAHME.md).

## Nutzbar

- Lokaler Windows-Start mit Ollama, SSE-Chat, bewusst freigegebene Dokumente, belegte Entwürfe, Aufgaben und In-App-Erinnerungen, registrierte PC-/Browserwerkzeuge sowie überprüftes Backup/Restore (M00–M07).
- Zusätzliche lokale Funktionen: versionierte Chat-/Entwurfs-/Planregeln, Schreibproben und Stilprofile, Essays, Lernmodus, bewusst gepflegte Erinnerungen, Projekt-/Dateigraph, Recherche, begrenzte Sprach- und CAD-Integrationen. Einige Integrationen brauchen eine gesonderte Installation; siehe [README](../README.md).
- Optionaler OpenRouter-Chat mit Freigabe je Anfrage, Modellpreisprüfung, Tagesbudget und Kostenbeleg. Ein echter kleiner Modellaufruf und ein Edge-Klickpfad mit Antwort, Reload und Kostenbeleg wurden am 17.11.2026 geprüft. Der Cloud-Schlüssel kann als Prozessvariable oder benutzergebunden verschlüsselt im Windows-Tresor liegen; Standard bleibt Ollama.
- Ein persönliches Schreibprofil mit eigenen Beispielen. Der Anzeigename ist einstellbar und standardmäßig „Mein Stil“. Die technische Kennung `Maxim` bleibt für die Kompatibilität älterer lokaler Daten und API-Aufrufe erhalten.

## Zuletzt geprüft

- Nach der OpenRouter-Tresoranbindung: 516 Backendtests bestanden, zwei Windows-Symlink-Tests übersprungen; zwölf Frontendtests, TypeScript/Vite-Build und Ruff bestanden.
- Nach Einführung des einstellbaren Profilnamens und des Windows-Einrichtungshelfers: vollständige Backend-Regression mit 517 bestandenen Tests und zwei Windows-Symlink-Skips, zwölf Frontendtests, Ruff und TypeScript/Vite-Build bestanden. Der Einrichtungshelfer lief erfolgreich mit `-SkipModelDownload`; eine vorhandene lokale `.env` wurde nicht überschrieben. Die erneut ausgeführten 24 Konfigurations-/API-Tests bestanden. `scripts/smoke_named_style_ui.py` und `scripts/smoke_public_profile_ui.py` bestanden mit Edge/Ollama, inklusive 390-Pixel-Ansicht und gespeicherter Umbenennung. `scripts/e2e_local.py` bestand mit Chat, Entwurf, Wissen, Aufgabe, öffentlichem Browserabruf und Schema-52-Restore; `scripts/smoke_stop.py` bestätigte Health/UI und freie Ports nach dem Stopp. Ein erster Stil-Klicktest musste nach Start des noch nicht laufenden Ollama-Dienstes wiederholt werden und bestand dann.
- Der echte Edge-Cloud-Test mit temporärem Tresor prüfte bewusste Freigabe, Antwort, Kostenbeleg, Reload, mobile Breite und lokales Entziehen. Der Schlüssel und Nutzerdaten sind nicht Bestandteil des Repositories.

## Offene Arbeiten

1. Die öffentliche Quellen- und Dokumentationsprüfung abschließen. `scripts/audit_public.py` meldet aktuell null Funde in 276 veröffentlichbaren Dateien; der verdeckte Vergleich mit dem tatsächlich verwendeten Schlüssel ergab ebenfalls null Funde. Ein neuer Git-Startcommit ohne vorherige lokale Historie wird vorbereitet.
2. Nach dem ersten Push einen frischen Remote-Clone mit Einrichtung, Start/Stopp und wichtigem Ollama-Klickpfad prüfen. Der Cloud-Klickpfad wurde vor der öffentlichen Bereinigung mit temporärem Tresor erfolgreich geprüft; bei Bedarf nach dem Clone wiederholen.
3. Öffentliche GitHub-Veröffentlichung und danach ein Push pro abgeschlossenem Feature. Die Trello-Karten P00 (öffentliche Version) und A79b (Tresoranbindung) danach mit dem verifizierten Link aktualisieren.
4. Weitere Trello-Funktionen anhand der offenen Karten umsetzen und nach jeder Funktion testen, dokumentieren und pushen.

## Betriebsgrenzen

Die App bindet nur an Loopback und ist nicht für Fernzugriff eingerichtet. Freigegebene Dateien bleiben außerhalb des Backups. Gescannte PDFs brauchen OCR außerhalb der App. Erinnerungen werden bei ausgeschaltetem Gerät nach dem nächsten Start nachgereicht und erscheinen in der geöffneten App. Modellantworten, Zitate, Stil und CAD-Maße vor Verwendung prüfen. Details zu Datenflüssen und Netzwerkgrenzen stehen in [SECURITY.md](SECURITY.md).
