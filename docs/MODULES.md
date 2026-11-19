# Lokale Jarvis-Module

Jarvis kann optionale, lokal installierte Feature-Pakete sofort aktivieren und deaktivieren.
Ein aktives Paket registriert seine eigene API und eine eigene Oberfläche in der normalen
Jarvis-Navigation. Ohne Paket startet der Kern weiter. Die privaten Piloten `day_notes`
(Tagesnotizen) und `reading_list` (lokale Leseliste) liegen ausschließlich im privaten
Modul-Repository. Die Leseliste prüft auch den gleichzeitigen Betrieb zweier Pakete und
ein schreibendes Werkzeug mit Freigabe. Bestehende ältere Bereiche des Jarvis-Monolithen sind noch nicht in
solche Pakete ausgelagert; [P04 in Trello](https://trello.com/c/BcttXfbZ) verfolgt diese
Migration.

## Installation und Start unter Windows

Die normale [Jarvis-Einrichtung](../README.md#einmalige-einrichtung) zuerst ausführen.
Für private Module braucht der GitHub-Nutzer Zugriff auf das private Repository.
In PowerShell **im Jarvis-Hauptordner**:

```powershell
git clone https://github.com/mschr703CODEX/jarvis-private-modules.git ..\jarvis-private-modules
.\backend\.venv\Scripts\python.exe scripts\module_package.py install ..\jarvis-private-modules\day_notes
.\backend\.venv\Scripts\python.exe scripts\module_package.py install ..\jarvis-private-modules\reading_list
python scripts\dev.py
```

Dann `http://127.0.0.1:5173` öffnen, unter **Module** „Tagesnotizen“ und „Leseliste“
aktivieren und die neuen Navigationspunkte öffnen. Eine Notiz oder einen HTTP(S)-Link
speichern, die Seite neu laden und den Eintrag wiederfinden. Unter **Module** eines
deaktivieren: Sein Navigationspunkt, seine API und sein ausgelieferter UI-Code sind
sofort gesperrt; das andere Modul bleibt nutzbar. Beim erneuten Aktivieren sind die
Daten wieder da. Beide Module brauchen weder OpenRouter noch Ollama für ihre Einträge.

Alternativ lässt sich das Paket direkt in **Module → Lokales Paket installieren**
hinzufügen. Dort den absoluten Pfad zum Ordner `day_notes` eintragen. Mit PowerShell
liefert `(Resolve-Path ..\jarvis-private-modules\day_notes).Path` den passenden Wert.
Die Oberfläche zeigt die deklarierte Version, Datenversion und Abhängigkeiten an.

## Paket aktualisieren

Für ein neues Paket desselben Moduls zuerst **Module → Deaktivieren** wählen. Den
absoluten Pfad zum neuen Paketordner in das Feld oben eintragen und beim betroffenen
Modul **Aktualisieren** klicken. Danach das Modul erneut aktivieren. Es muss nicht
vorher entfernt werden; vorhandene Daten bleiben. In PowerShell geht derselbe
Austausch mit:

```powershell
.\backend\.venv\Scripts\python.exe scripts\module_package.py upgrade ..\jarvis-private-modules\day_notes
```

Jarvis vergleicht die drei kopierten Dateien mit der Quelle, speichert den neuen
SHA-256-Beleg und stellt das bisherige Paket wieder her, wenn der Austausch oder
die Belegspeicherung scheitert. Eine niedrigere Datenversion als die bereits
gespeicherte wird abgewiesen. Die Datenmigration läuft erst bei der anschließenden
Aktivierung; scheitert sie, wird ihre Datenbanktransaktion zurückgenommen und das
Modul bleibt inaktiv. Nach einem UI-Code-Update den Browser neu laden.
Liegen im installierten Paketordner unerwartete Dateien, stoppt das Update und
belässt diese Dateien dort. Erst nach eigener Prüfung entfernen oder verschieben.

Liegt das private Repository schon neben dem Hauptordner, den `git clone`-Schritt
überspringen. `module_package.py` liest wie Jarvis den Datenordner aus `.env`
oder `JARVIS_DATA_DIR`; ohne eigene Einstellung nutzt es
`%LOCALAPPDATA%\JarvisLocal`. `--data-dir` überschreibt dies für isolierte Tests.

## Entfernen und Daten

Zum Entfernen zuerst in **Module** deaktivieren, dann:

```powershell
.\backend\.venv\Scripts\python.exe scripts\module_package.py remove day_notes
```

Der Befehl entfernt nur die drei installierten Paketdateien und ihren Python-Cache. Die Daten bleiben in
`jarvis.db`. Ein später neu installiertes Modul kann sie wieder verwenden. Das
reguläre Jarvis-Backup sichert die Datenbank einschließlich Modultabellen, aber
**nicht** den Modulcode. Nach einem Restore private Module aus einer vertrauten
Quelle erneut installieren. War ein Modul vor dem Restore aktiv, zeigt **Module**
das fehlende Paket mit einem Fehler an. Nach der Neuinstallation lässt es sich dort
wieder aktivieren; seine Daten bleiben erhalten. Eine automatische Datenlöschung
gibt es nicht.
Der Knopf **Entfernen (Daten behalten)** in der Modulansicht führt denselben
Vorgang aus; er ist erst nach Deaktivierung nutzbar.

## Paketvertrag (Modul-API 1)

Ein Paketordner enthält genau `jarvis-module.json`, `module.py` und `ui.js`.
Das Manifest nennt `id`, `name`, Semver-`version`, `api: 1`, `data_version`,
`dependencies`, `permissions`, `navigation`, `view: "web_component_v1"` und
`element` (einen `jarvis-…`-Custom-Element-Namen). Die ID muss zum Ordner passen.
Die Installation kopiert diese drei lokalen Dateien in `<JARVIS_DATA_DIR>/modules/<id>`.
Es gibt keinen automatischen Download oder Hot-Update von GitHub.
Jarvis speichert ihre gemeinsame SHA-256-Prüfsumme lokal in `jarvis.db`. Ändert sich
eine installierte Datei danach, sperrt Jarvis das Modul samt API, UI und Werkzeugen.
In **Module** erst deaktivieren, dann das Paket aus einer vertrauten Quelle
aktualisieren oder entfernen und neu installieren. Eine aktualisierte Quelle kann
mit höherer `data_version` migrieren; eine ältere Datenversion wird abgewiesen.

`module.py` stellt `migrate(connection, from_version)` und `create_app(db)` bereit.
Eine Migration läuft nur beim Schritt zu einer höheren `data_version`; die
Version wird in derselben SQLite-Transaktion gespeichert. Ein Daten-Downgrade
ist gesperrt. `create_app` liefert eine FastAPI-App, deren Pfade unter
`/api/modules/<id>/…` erreichbar sind. Deaktivieren sperrt neue Aufrufe; bereits
laufende Anfragen können noch abschließen. Abhängigkeiten müssen aktiv sein,
bevor ein Modul aktiviert wird. Die API- und UI-Routen werden bei Deaktivierung
mit HTTP 404 gesperrt.
Bei einem Neustart aktiviert Jarvis gespeicherte Module in Abhängigkeitsreihenfolge.
Zyklen und fehlende Abhängigkeiten bleiben inaktiv mit sichtbarem Fehler; der Kern
startet weiter. Wird ein benötigtes Paket während des Betriebs entfernt, werden
seine aktiven abhängigen Pakete ebenfalls gesperrt, ohne deren Daten zu löschen.
Zwei aktive Module dürfen nicht denselben Custom-Element-Namen im Manifest belegen;
das zweite bleibt mit sichtbarem Fehler inaktiv.

Optional stellt `module.py` `tool_definitions(db)` bereit und liefert höchstens
16 `ToolDefinition`-Einträge. Jarvis stellt den Modulnamen voran
(`day_notes__list_notes` im Pilot). Das Werkzeug erscheint nur bei aktivem
Modul unter **Aktivität**; neue Läufe werden nach Deaktivierung abgewiesen.
Die normale Freigabe für schreibende Werkzeuge bleibt bestehen. Ein bereits
begonnener Lauf kann abschließen; beim späteren Start wird der Aktivierungsstatus
erneut geprüft.
Jarvis prüft alle Werkzeugdefinitionen vor der Registrierung. Scheitert ein Paket
an einem ungültigen oder kollidierenden Werkzeug, werden bereits registrierte
Werkzeuge dieses Aktivierungsversuchs zurückgenommen; der Kern bleibt nutzbar.

`ui.js` registriert das im Manifest genannte Custom Element. Der Kern lädt es
erst, wenn das Modul aktiv und sein Navigationspunkt geöffnet ist. Er übergibt
`element.jarvisApi` (dieselbe lokale, CSRF-geschützte API-Funktion wie in den
eingebauten Ansichten) und `element.moduleId`. Der Modulcode kann die üblichen
Jarvis-CSS-Klassen nutzen. Nach einem Code-Update den Browser neu laden.

**Vertrauensgrenze:** Installierter Python- und JavaScript-Code läuft mit den
Rechten des lokalen Jarvis-Prozesses beziehungsweise Browsers. Das Manifest
deklariert Nutzungsbereiche, stellt aber noch keine Betriebssystem-Sandbox dar.
Nur selbst geprüfte Pakete installieren; Geheimnisse und persönliche Daten
gehören in den lokalen Datenordner, nicht in den Git-Checkout. Unbekannte
Modul-API-Versionen, Links/Junctions, ungültige Manifestfelder und nicht
vorhandene Abhängigkeiten werden abgewiesen. Fehler eines optionalen Pakets
werden im Modulstatus gezeigt, während der Kern weiterläuft.

## Prüfung

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend\tests\test_modules.py -q
.\backend\.venv\Scripts\python.exe scripts\smoke_modules_ui.py --module-source ..\jarvis-private-modules\day_notes
.\backend\.venv\Scripts\python.exe -m pytest ..\jarvis-private-modules\tests\test_reading_list.py -q
.\backend\.venv\Scripts\python.exe ..\jarvis-private-modules\tests\smoke_reading_list_ui.py --core .
```

Der zweite Befehl startet einen isolierten Edge-Klicktest mit temporärem
Datenprofil und benötigt freie Ports 8765/5173. Er prüft Installation,
Aktivierung, privates UI-Bundle, Speichern, Reload, Deaktivierung, gesperrte
API/UI-Routen und Werkzeuge, Reaktivierung, direkten Paketwechsel,
Entfernen/Wiederinstallieren mit erhaltenen Daten und 390-Pixel-Ansicht. Die beiden letzten Befehle benötigen den privaten
Checkout und prüfen Leselisten-Daten, die Freigabe des schreibenden Werkzeugs sowie
beide Module gleichzeitig im echten Edge-Browser.
