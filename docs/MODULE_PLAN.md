# Jarvis-Module · Umsetzungsplan

Stand: 19.11.2026. Die Trello-Karten bleiben die fachliche Quelle. Der Nutzer hat `mschr703CODEX` als neues Konto gewählt: [öffentliche Basis](https://github.com/mschr703CODEX/jarvis-local-assistant), [private Module](https://github.com/mschr703CODEX/jarvis-private-modules). Das bisherige Repository bleibt Archiv. **Alle optionalen Module bleiben vorerst privat.** Über eine spätere Veröffentlichung einzelner Module wird erst nach deren Prüfung entschieden.

## Technischer Schnitt

Der bisherige Code ist noch überwiegend eine gemeinsame FastAPI-/React-Anwendung. Die Paketgrenze, zwei private Piloten `day_notes` und `reading_list` sowie der ausgelagerte Bereich `evening_plans` sind in [MODULES.md](MODULES.md) dokumentiert. Viele andere bestehende Bereiche lassen sich noch **nicht** durch bloßes Entfernen eines Verzeichnisses sicher deinstallieren. [P04](https://trello.com/c/BcttXfbZ) führt die Migration fort:

1. Ein kleines Basisprogramm behält Konfiguration, Ollama/OpenRouter-Adapter, Freigaben, Werkzeuglauf, Datenbank-/Backup-Grundlagen und eine Modul-Registry.
2. Ein Modul besitzt eine eindeutige ID, Version, kompatible Kernversion, Abhängigkeiten und angeforderte Rechte. Es registriert eigene API-Routen, Werkzeuge, UI-Navigation und Datenmigrationen. Fehlende/ausgeschaltete Module erscheinen weder als ausführbare Werkzeuge noch in der Navigation.
3. Aktivieren, Deaktivieren und Löschen von Moduldaten sind drei getrennte Vorgänge. Deaktivieren behält Daten und verbietet neue Jobs; Entfernen von Daten verlangt eine eigene Bestätigung und ein Backup. Fehler eines optionalen Moduls dürfen den Kern nicht am Start hindern.
4. Pakete werden aus einem geprüften lokalen Verzeichnis installiert; kein automatisches Ausführen beliebiger GitHub-Repos. Public-Audit, Lizenz- und Abhängigkeitsprüfung gelten je Paket. Geheimnisse und persönliche Daten liegen ausschließlich im lokalen Datenordner.
5. Ein Pilot belegt: Kern ohne Modul starten, Modul aktivieren, Funktion nutzen, deaktivieren, Routen/Werkzeuge/UI sind weg, reaktivieren und Daten wiederfinden. Erst danach weitere Features aus dem Monolithen herauslösen.

## Funktionsgruppen für die gemeinsame Auswahl

| Bereich | Konkrete nächste Fähigkeiten | Trello | Veröffentlichungsentscheidung |
| --- | --- | --- | --- |
| Kernqualität | Modul-Registry; Schutz vor manipulierten Quellen; nachvollziehbare Ergebnisprüfung; Skills/Regeln mit Version und Rückkehr; Restore-Probelauf | [P04](https://trello.com/c/BcttXfbZ), [A81](https://trello.com/c/VQxCdWOk), [A47](https://trello.com/c/N7CUKBM6), [A77](https://trello.com/c/shxRkjhK), [A78](https://trello.com/c/3SFHxP2V) | Kern öffentlich; optionale Qualitätsmodule privat |
| Projekt & Wissen | Projekt weiterführen mit belegten Schritten; Sitzungsnotizen wiederfinden/zusammenfassen; Wissensnetz mit bewusst gepflegten Beziehungen; Folgeaktionen vorschlagen | [J5](https://trello.com/c/tdsxnyaK), [J12](https://trello.com/c/Q64q1GT2), [A03](https://trello.com/c/vZ8DUxE4), [A09](https://trello.com/c/aSvwzJTg) | Optionales Paket vorerst privat |
| Texte & Alltag | Texte im Schreibprofil Maxim; Mailantwort als Entwurf; Kalender/TickTick; Zeit-/Abendplanung; Benachrichtigungen; Kontakte und Lebenschronik | [Schreiben](https://trello.com/c/1DMBlL9Z), [C00](https://trello.com/c/lOM1h7DK), [C01](https://trello.com/c/xwtCntzf), [A15](https://trello.com/c/ASzJhO8e), [A22](https://trello.com/c/w3SFuV49), [N6](https://trello.com/c/hSQ02opd), [N24](https://trello.com/c/v4B8U57n) | Optionales Paket vorerst privat; Profile/Daten immer lokal |
| Recherche & Browser | Mehr Quellen lesen und belegen; dynamische Browseraktionen mit Freigabe; Browser-Erweiterung; Obsidian-Export; geprüfte MCP-Werkzeuge | [J4](https://trello.com/c/RTmX6paB), [G03](https://trello.com/c/3GlpHtQT), [G04](https://trello.com/c/6DXoFfgL), [S4](https://trello.com/c/ZDXnsQak), [S3](https://trello.com/c/pqauOYaZ), [A69](https://trello.com/c/nblNW22B) | Optionales Paket vorerst privat |
| Sprache | Lokales Vorlesen mit VoiceStudio, Sprachmodus mit Unterbrechen als LiveKit-Pilot; beide nur bei installiertem Dienst | [G01](https://trello.com/c/VCzo3qot), [G02](https://trello.com/c/XS2hZPNj) | Optionales Paket vorerst privat |
| Entwicklung & Werkstatt | Codeprojekt verstehen/testen; GitHub-/Coding-Agent mit Freigaben; UI-Qualitäts-Skills; parametrisierter CAD-Pilot | [A40](https://trello.com/c/lUMWDdFd), [A41](https://trello.com/c/VpINtjg6), [A44](https://trello.com/c/gKEGRWym), [G07](https://trello.com/c/zKnKR6u6) | Optionales Paket vorerst privat |
| Später | Lernkarten/Prüfungsmodus derzeit nachrangig; Schulplattform; Smartphone/Fernzugriff; Heimserver, Smart Home und Schreibtischsteuerung erst mit realer Hardware | [A36](https://trello.com/c/qxRt1J8e), [A37](https://trello.com/c/wPlALKnq), [S6](https://trello.com/c/Mg8ATk8X), [A63](https://trello.com/c/OHeCnn6f), [H00](https://trello.com/c/TavQRxih) | Nachrangig, falls gebaut privat |

Die vom Nutzer ausgewählten GitHub-Ideen sind auf [G00](https://trello.com/c/jnNiKZI5) festgehalten: VoiceStudio, LiveKit, Agent Reach, browser-use, DeepTutor als Inspiration, Graphify/Obsidian, text-to-cad, Taste Skill/Impeccable und Letta als Inspirationsquelle. AnythingLLM, CrewAI, Hermes Agent, Superpowers, Firecrawl als Komplettstack und open-design-studio wurden nicht als Integration gewählt. Die einzelnen G-Karten belegen nur ihren jeweiligen umgesetzten Teilumfang, nicht den Vollausbau aller genannten Projekte.

## Veröffentlichungsstand

Die privaten Pakete sind `day_notes`, `reading_list` und `evening_plans`. Die Entscheidung über eine spätere Veröffentlichung einzelner Module bleibt offen; bis dahin sind optionale Pakete privat. Die bereits veröffentlichte bisherige Codebasis wird nicht rückwirkend als modular dargestellt. Die gemeinsame Featureliste mit Sichtbarkeitsvorschlägen liegt bis zur Entscheidung ausschließlich im privaten Modul-Repository.

## Erster Monolith-Schnitt: Abendplanung

Der zuvor feste Bereich ist nun als `evening_plans` im privaten Repository umgesetzt.
Planlogik, API und Web-Component-Oberfläche liegen dort; der öffentliche Kern enthält
keine feste Route, Navigation oder Abendplan-Quellklasse mehr. Die vorhandene Tabelle
`evening_plans` stammt aus der historischen Kernmigration 15 und bleibt für alte
Backups erhalten. Das Modul übernimmt bestehende IDs, Eingaben, Ergebnisse und
Versionen unverändert und nutzt `TaskStore` sowie `TimeModelStore` des Kerns.
Der alte Abendplan-Code war bereits Teil früherer öffentlicher Commits. Das aktuelle
Auslagern entfernt ihn aus dem neuesten öffentlichen Quellstand, nicht aus der
erreichbaren Git-Historie. Eine spätere Entscheidung über die Veröffentlichung des
neuen Pakets ändert diesen historischen Umstand nicht.

Sieben private Tests prüfen Planung, Fristen, Zeitumstellung, Versionskonflikte,
Altbestand, Backup/Restore, Reinstallation und die neue API. Ein echter Edge-Pfad
prüft bei 390 Pixel Installation, Vorschau, Speichern, veralteten Plan, Neuberechnung,
Reload, Löschen und Deaktivieren. Die alten `/evening-plans`-Routen sind entfernt;
die Paketroute liegt unter `/api/modules/evening_plans/`.

Als nächstes folgen die Härtung von Installation und Upgrade sowie weitere
Monolith-Bereiche. Die Reihenfolge der nächsten Fähigkeiten steht im privaten
Planungsdokument; öffentlich/private Zielpakete sind bis zur Entscheidung des
Nutzers Vorschläge.
