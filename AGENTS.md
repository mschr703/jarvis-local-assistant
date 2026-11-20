# Hinweise für Jarvis-Agenten

Lies vor jeder Änderung [docs/PROJECT_BRIEF.md](docs/PROJECT_BRIEF.md), die konkrete Aufgabenkarte und [docs/STATUS.md](docs/STATUS.md). Der Brief beschreibt das Ziel; README und Status beschreiben den tatsächlich vorhandenen Stand. Behaupte keine Funktion allein wegen einer Idee oder einer Trello-Karte.

- Bearbeite die konkrete Karte. Prüfe zuerst, ob die Funktion schon existiert, welche Module betroffen sind und welcher überprüfbare Nutzerablauf am Ende funktionieren soll.
- Baue Gesprächsverständnis und Werkzeugnutzung so, dass sie auch für neue Formulierungen und Kombinationen von Fragen funktionieren. Benutze echte aktuelle Quellen und Werkzeugergebnisse. Erfinde keine Termine, Nachrichten, Kontakte oder Geräteaktionen.
- Halte allgemeine Schnittstellen und wiederverwendbare Funktionen im öffentlichen Kern. Persönliche Integrationen und noch nicht zur Veröffentlichung bestimmte Module bleiben in getrennten privaten Paketen. Ein öffentlicher Commit enthält nur allgemein nutzbaren Code, synthetische Testdaten und öffentliche Dokumentation.
- Externe Datenzugriffe, schreibende Aktionen und Versand brauchen die im Produkt vorgesehenen Einstellungen und konkreten Freigaben. Eine Modellantwort ersetzt keine Berechtigungsprüfung und keinen Nachweis über eine ausgeführte Aktion.
- Teste den betroffenen API- und UI-Pfad. Prüfe bei Werkzeugen auch Fehler, veraltete Quellen und Ablehnung. Aktualisiere README und `docs/STATUS.md` mit dem wirklich erreichten Ergebnis und offenen Grenzen.
- Vor einem öffentlichen Push: Diff sichten, `scripts/push_public.ps1 -CheckOnly` ausführen und den versionierten Pre-Push-Hook mit `scripts/install_public_push_guard.ps1` aktivieren. Niemals Schlüssel, `.env`, Kangentic-Dateien, Browserprofile oder echte Nutzerinhalte committen. Für den Push den geprüften Helfer verwenden.

Wenn eine Aufgabenkarte ein privates Modul betrifft, gelten dessen eigene Tests und Veröffentlichungsgrenzen zusätzlich. Bei fehlender Freigabe oder unklaren echten Nutzeraktionen die Karte mit einer konkreten Frage anhalten, während unabhängige Arbeit weitergehen kann.
