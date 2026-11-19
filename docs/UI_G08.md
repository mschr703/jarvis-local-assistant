# G08 · Gedächtnisprüfung

## Ausgangslage

Die Gedächtnisprüfung ist ein Arbeitsbildschirm: Nutzer prüfen konkrete, belegte Erinnerungsvorschläge und entscheiden selbst. Die ursprüngliche Ansicht zeigte alle Einträge in gleicher Gewichtung und stellte die optionale Widerspruchsprüfung vor offene Entscheidungen. Auf 390 Pixeln erschien der erste offene Vorschlag erst weit unten. [Mobil vorher](screenshots/g08-memory-before-mobile.png) · [Desktop vorher](screenshots/g08-memory-before-desktop.png).

## Geprüfte Richtungen

1. **Je Vorschlag eine eigene große Karte:** klare Trennung, aber viel wiederholte Fläche und längere Wege auf dem Telefon.
2. **Chronologische Liste mit farbigen Zuständen:** geringer Umbau, doch ein aktueller bestätigter Eintrag könnte eine offene Entscheidung verdrängen.
3. **Ruhige Prüfliste mit offenen Einträgen zuerst:** Statusfilter und eine farblich zurückhaltende Kennzeichnung; Originalstelle als eigener Belegblock; optionale Widerspruchsprüfung nach der Entscheidungsliste. Diese Richtung wurde umgesetzt.

Die Entscheidung folgt der [App-Screen-Richtung von Taste Skill](https://github.com/Leonxlnx/taste-skill/blob/main/skills/taste-skill/references/app-screens.md): Farbe markiert Information statt Dekoration. Die [Impeccable-Kritik- und Audit-Hinweise](https://github.com/pbakaus/impeccable) wurden für Hierarchie, Kontrast, Tastatur und schmale Breite angewendet; keine fremden UI-Dateien wurden übernommen.

## Ergebnis und Prüfung

[Mobil danach](screenshots/g08-memory-after-mobile.png) · [Desktop danach](screenshots/g08-memory-after-desktop.png). Offen, bestätigt und abgeschlossen sind gezählt; die Filter zeigen die einzelnen Zustände. Bei gleicher Auswahl erscheinen offene Einträge zuerst. Die wörtliche Originalstelle bleibt deutlich vom Modellvorschlag und dem eigenen Eingabefeld getrennt. Gültigkeitsdaten, Chat-Link und Entscheidungsverlauf bleiben erreichbar.

`scripts/capture_memory_ui.py before|after` erzeugt reproduzierbare Edge-Aufnahmen mit denselben drei Beispieldaten. Die `after`-Prüfung kontrolliert 390 und 1280 Pixel ohne horizontales Überlaufen, Enter/Leertaste auf den Statusfiltern, sichtbare Ergebniszahlen und mindestens 4,5:1 Kontrast für Status, aktiven Filter und Belegbeschriftung. Die vorhandenen Frontendtests, der Produktionsbuild und der echte Ollama-Klicktest prüfen den Ablauf zusätzlich.
