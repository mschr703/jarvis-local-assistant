# Jarvis Local

Deutschsprachiger Assistent für einen Windows-Laptop. Chat, Dokumentensuche, Entwürfe, Aufgaben, Erinnerungen, Zeitmodell, Abendplanung, manuelle Arbeitsstände und begrenzte PC-/Browseraktionen laufen über eine lokale Oberfläche. Ohne OpenRouter-Key verwendet Jarvis das lokale Ollama-Modell `qwen3:4b-instruct`. Daten liegen standardmäßig in `%LOCALAPPDATA%\JarvisLocal`, außerhalb des Repositories. Ein eigener `JARVIS_DATA_DIR` muss ein absoluter Pfad sein.

## Voraussetzungen

- Windows 11, [Git](https://git-scm.com/download/win), Python 3.13, Node.js 24, pnpm 11 und [uv](https://docs.astral.sh/uv/getting-started/installation/).
- [Ollama für Windows](https://ollama.com/download/windows) und rund 3 GB freier Platz für `qwen3:4b-instruct`.
- Microsoft Edge für das Playwright-Browserwerkzeug. Jarvis verwendet dafür ein eigenes Profil unter `%LOCALAPPDATA%\JarvisLocal\browser-profile`.

## Einmalige Einrichtung

PowerShell öffnen und den Quellcode laden:

```powershell
git clone https://github.com/mschr703/jarvis-local-assistant.git
Set-Location jarvis-local-assistant
```

Der Einrichtungshelfer funktioniert mit Windows PowerShell 5.1 und PowerShell 7. Er prüft die benötigten Programme und Versionen, installiert die gesperrten Python-/Frontend-Abhängigkeiten, erstellt bei Bedarf eine lokale `.env` und lädt das Ollama-Modell. Eine bestehende `.env` wird nicht überschrieben:

```powershell
.\scripts\setup.ps1
```

Wenn das Modell schon vorhanden ist, kann `-SkipModelDownload` verwendet werden. Fehlt ein Programm, nennt der Helfer dessen Installationsseite. Alternativ sind die Einzelschritte:

```powershell
Copy-Item .env.example .env
python -m uv sync --project backend
pnpm --dir frontend install --frozen-lockfile
ollama pull qwen3:4b-instruct
```

Falls `uv` als eigenständiges Programm statt als Python-Modul installiert wurde, in den Befehlen dieser Anleitung `python -m uv` durch `uv` ersetzen.

Falls `ollama` im aktuellen Terminal noch nicht im `PATH` liegt, ein neues Terminal öffnen oder `& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" pull qwen3:4b-instruct` ausführen. Ollama muss laufen; die Windows-App startet üblicherweise einen lokalen Dienst auf `127.0.0.1:11434`. In `.env` bleibt `JARVIS_PROVIDER=ollama` und es wird kein API-Key benötigt.

## Start und Stopp

```powershell
python scripts/dev.py
```

Danach [http://127.0.0.1:5173](http://127.0.0.1:5173) öffnen. Die API bindet ausschließlich an `127.0.0.1:8765`; [der Healthcheck](http://127.0.0.1:8765/health) zeigt Provider und Modellbereitschaft. Beim ersten Start kann die Oberfläche kurz „Prüfe…“ anzeigen, während Backend und Ollama starten. In der Übersicht kann der Modellstatus jederzeit mit „Status prüfen“ neu abgefragt werden.

Zum Stoppen im Startterminal `Ctrl+C` drücken. Falls ein Terminal unerwartet endet oder Prozesse weiterlaufen, im Repository-Ordner ausführen:

```powershell
.\scripts\stop.ps1
```

Das Skript stoppt nur die verifizierten Jarvis-Prozesse, die tatsächlich auf den erwarteten localhost-Ports 8765 und 5173 lauschen. Eine fehlende oder veraltete PID-Datei kann dadurch keinen anderen Jarvis-Prozess auf einem abweichenden Port beenden. Ein zweiter Start meldet belegte Ports, bevor neue Prozesse angelegt werden.

## Die vier MVP-Abläufe

1. **Dokument → Entwurf:** Unter „Dokumente“ einen absoluten Ordnerpfad freigeben, „Index aktualisieren“ wählen, nach einem Begriff suchen und Fundstellen auswählen. Suche, Fundstellenauswahl und angefangene Eingaben bleiben beim Wechsel in einen anderen Bereich innerhalb der geöffneten Oberfläche erhalten. Ein laufender Indexauftrag bleibt gesperrt, bis er endet. Unter „Entwürfe“ Schreibauftrag, optional eine eingefügte Nachricht, Stilprofil und Schreibproben angeben. Angaben zum neuen Entwurf und ungespeicherte Bearbeitungen eines erzeugten Entwurfs bleiben beim Wechsel zwischen den Bereichen innerhalb der geöffneten Oberfläche erhalten. Eine laufende Erzeugung bleibt nach dem Seitenwechsel gesperrt, bis sie endet. Mit denselben Eingaben lassen sich weitere Varianten als getrennte Entwürfe erzeugen; ausgewählte Fundstellen bleiben bis zum Entfernen oder einer Index-/Freigabeänderung sichtbar. Der Entwurf zeigt seine verwendeten Quellen mit Pfad, Seite, Ausschnitt und Datei-Hash. Den Entwurf prüfen, Änderungen zuerst speichern und dann als TXT, Markdown oder bei E-Mail als EML exportieren. Der Export verlangt die angezeigte Entwurfsversion, damit keine unbemerkt geänderte Fassung gespeichert wird. Es wird keine Nachricht versendet.
2. **Bewusstes Wissen:** Unter „Dokumente“ eine Erinnerung mit Quelle speichern. Im Abschnitt „Bewusst gespeichertes Wissen“ den Erinnerungstext suchen oder mit „Alle anzeigen“ zur vollständigen Liste zurückkehren. Erinnerungen bleiben nach einem Neustart durchsuchbar und können geändert oder gelöscht werden. Die Suche über indizierte Dateien und explizite Erinnerungen ist getrennt.
3. **Aufgabe → Erinnerung:** Unter „Aufgaben“ Titel, lokale Fälligkeit und IANA-Zeitzone angeben. Eine begonnene Aufgabe bleibt beim Wechsel in einen anderen Bereich innerhalb der geöffneten Oberfläche erhalten. Die API verlangt eine lokale Fälligkeit im Format `JJJJ-MM-TTTHH:MM` ohne Offset; ein Datum ohne Uhrzeit wird nicht stillschweigend auf Mitternacht gesetzt. Vor dem Speichern zeigt Jarvis die aufgelöste Fälligkeit samt UTC-Offset. Bei der doppelt vorkommenden Stunde der Winterzeit das erste oder zweite Vorkommen wählen; die Aufgabenliste zeigt diese Wahl mit Offset auch nach dem Speichern, und die Bearbeitung behält sie bei. Nicht existierende Zeiten werden vor dem Speichern erklärt. Die Liste zeigt pro Fälligkeit die Job-ID und „ausstehend“, „ausgelöst“ oder „abgebrochen“; offene Termine stehen vor Aufgaben ohne Termin und erledigten Aufgaben. Die Erstmeldung wird einmalig in der App angelegt, auch wenn Jarvis erst nach dem Termin wieder gestartet wird. Der Hinweis zeigt den geplanten und den tatsächlichen Anzeigezeitpunkt in der Laptop-Zeitzone. Bei ausgeschaltetem oder schlafendem Laptop kann Jarvis nicht pünktlich auslösen; nach dem Aufwachen oder Neustart wird der Termin nachgereicht. Jarvis zeigt einen In-App-Hinweis, keine Windows-Systembenachrichtigung; dafür muss die Oberfläche geöffnet sein.
4. **PC/Browser:** Unter „Aktivität“ eine registrierte Anwendung, eine indizierte freigegebene Datei oder eine öffentliche HTTP(S)-Seite auswählen. Angefangene Eingaben und ein laufender, gesperrter Auftrag bleiben beim Wechsel in einen anderen Bereich innerhalb der geöffneten Oberfläche erhalten. Der Auftrag zeigt Status und Ergebnis. Private Netzadressen und Weiterleitungen dorthin werden gesperrt. Das einfache Werkzeug `browser_read` verbindet sich direkt mit einer geprüften öffentlichen IP und rendert die geladene Textseite in Edge ohne JavaScript und weitere Netzwerkanfragen. Für HTTPS verwendet der Abruf die Windows-Zertifikatsprüfung; ein nicht verifizierbares Zertifikat wird als Fehler angezeigt. Der HTTP-Abruf hat ein Gesamtlaufzeitlimit von 20 Sekunden; Gzip-komprimierte Textseiten werden bis 2 MB nach Entpacken gelesen. Für JavaScript-Seiten gibt es das unten beschriebene gesonderte Werkzeug `browser_dynamic_read`.

### Dynamische öffentliche Seiten lesen (G04)

Einmalig die getrennte [Browser-Use-Bibliothek](https://github.com/browser-use/browser-use) installieren und das lokale Playwright-Chromium bereitstellen:

```powershell
python -m uv sync --project integrations/browser_use
& .\integrations\browser_use\.venv\Scripts\python.exe -m playwright install chromium
```

Jarvis wie oben mit `python scripts/dev.py` starten. Unter „Aktivität → Werkzeug ausführen“ `browser_dynamic_read · public_web · read` auswählen, eine vollständige öffentliche **HTTPS**-Seite eintragen und die angezeigte Domain sowie Grenzen prüfen. Erst „Im Browser lesen“ startet den Auftrag. Die Ansicht zeigt Laufstatus, erreichte Quellen-URL, Titel, sichtbaren DOM-Ausschnitt und Screenshot; „Stoppen“ beendet den laufenden Browserprozess. Der statische Abruf prüft die Adresse und blockiert Weiterleitungen auf andere Domains oder private Ziele vor dem Browserstart. Chromium verwendet ein neues temporäres Profil, eine geprüfte öffentliche IP und einen Netzfilter, der nur HTTPS-Anfragen an die gewählte Domain zulässt. Es gibt keine Klicks, Texteingaben, Logins oder Downloads. Höchstens 12.000 Textzeichen, 2 MB Screenshot, 95 Sekunden Browserprozess und 120 Sekunden Werkzeuglauf. Screenshots gehören zum normalen Jarvis-Backup und bleiben nach Restore über die Lauf-ID erreichbar. Die Bibliotheks-Telemetrie ist für diesen Aufruf deaktiviert. Der feste Lesepfad verwendet kein Modell und braucht weder OpenRouter-Key noch Credits. Ein separater Qwen3:4b-Agent-Pilot scheiterte hier erst an einem 4096-Token-Kontext und danach am lokalen 60-Sekunden-Modellzeitlimit; seine autonome Browsersteuerung wird daher nicht als zuverlässige Funktion angeboten.

### Lokalen Zwei-Loch-Halter als CAD-Modell bauen (G07)

Einmalig die lokale CAD-Laufzeit vorbereiten und ihre Telemetrie ausschalten:

```powershell
python -m uv tool run --no-config --managed-python --python 3.14 --from 'cad-server==0.10.3' cad telemetry off
```

Jarvis mit `python scripts/dev.py` starten und unter **Aktivität → Werkzeug ausführen** `cad_holder_build` wählen. Einen Halter mit Breite, Tiefe, Dicke, Bohrungsdurchmesser und Mittenabstand jeweils in mm beschreiben. **Maße aus Beschreibung vorschlagen** nutzt nur lokales Ollama und überträgt ausschließlich ausdrücklich genannte Werte in editierbare Felder. Die Werte prüfen, dann **CAD-Vorschau erstellen**. Der Auftrag zeigt Maße, Bild, Skriptversion und Quelltext. Nach Sichtprüfung **STEP exportieren** oder **STL exportieren**; der eigene Exportauftrag wartet auf **Freigeben** und bietet danach die geprüfte Datei zum Download. Jeder Bau verwendet eine eigene lokale CAD-Engine; „Stoppen“ beendet sie. Es wird weder gedruckt noch eine Maschine gesteuert. OpenRouter-Key und Credits werden nicht gebraucht. Die vollständige [CAD-Startanleitung](integrations/cad/README.md) erklärt den Ablauf und die Prüfungen.

### Lokaler Sprachmodus im Chat (G02)

Für die Spracheingabe einmalig die getrennte lokale Laufzeit und das deutsche Whisper-Modell installieren:

```powershell
python -m uv sync --project integrations/livekit --python 3.13
& .\integrations\livekit\.venv\Scripts\python.exe scripts\setup_voice.py
```

Das Modell liegt unter `%LOCALAPPDATA%\JarvisLocal\voice-models` und wird nur bei dieser Einrichtung heruntergeladen. Wer `JARVIS_DATA_DIR` in `.env` geändert hat, setzt denselben absoluten Pfad vor `setup_voice.py` auch als PowerShell-Umgebungsvariable. Alternativ lässt sich mit `JARVIS_VOICE_MODEL_DIR` ein vorhandener absoluter Modellordner angeben. Die laufende Transkription verwendet das Modell danach offline und speichert die Aufnahme nicht im Chat. Für hörbare Antworten zusätzlich die [VoiceStudio-Einrichtung](#fertige-chatantworten-lokal-vorlesen) abschließen und VoiceStudio starten. Es wird kein OpenRouter-Key benötigt.

Jarvis wie üblich mit `python scripts/dev.py` starten, **Chat → Sprachmodus starten → Mikrofon starten** wählen, sprechen und **Aufnahme beenden** klicken. Der erkannte Text erscheint im Nachrichtenfeld und kann korrigiert werden. **Transkript senden und vorlesen** fragt lokales Ollama und danach VoiceStudio; der Antworttext bleibt sichtbar, während Audio erzeugt wird. **Mikrofon starten / Antwort unterbrechen** stoppt laufende Wiedergabe oder eine noch laufende Antwort und beginnt eine neue Aufnahme. **Sprachmodus beenden** schließt Mikrofon, Aufnahme und Player. Der Browser fragt bei der ersten Aufnahme nach Mikrofonzugriff. Dieser Ablauf arbeitet in getrennten Sprachrunden; die LiveKit-Server/Agents-Laufzeit wurde lokal geprüft, ist aber noch nicht der Transport der Chatoberfläche. Die gemessene CPU-Latenz und die Grenzen stehen in [G02-Notizen](integrations/livekit/README.md).

### Lokal mit eigenen Dokumenten lernen

Unter „Dokumente“ einen Ordner freigeben und indexieren, einen Begriff suchen und eine bis drei Fundstellen auswählen. „Mit Fundstelle(n) lernen“ öffnet den Lernmodus. Dort ein Thema eingeben und „Belegte Frage erstellen“ wählen. Das lokale Ollama-Modell schlägt eine Frage samt Musterantwort und wörtlichem Quellenzitat vor. Jarvis akzeptiert das Zitat nur, wenn es in der gewählten Fundstelle steht. Frage und Musterantwort selbst prüfen; bei Bedarf vor der eigenen Antwort mit „Frage/Musterantwort korrigieren“ ändern. Danach die eigene Antwort eingeben und „Antwort prüfen“ wählen. Die Rückmeldung nennt einen Quellenbeleg oder kennzeichnet sich als nicht sicher bewertbar. Eine korrigierte eigene Antwort kann erneut geprüft werden.

Lernrunden bleiben nach Neustart in der lokalen Datenbank und im normalen Backup erhalten. Der angezeigte Dateipfad, die Fundstellen-ID und der SHA-256-Hash halten die Quellenfassung fest; nach einer späteren Dateiänderung ist dieser Beleg ein Snapshot und muss gegen die aktuelle Datei geprüft werden. „Lernrunde löschen“ entfernt Frage und Antwort nach Bestätigung, nicht die Quelldatei. Der Lernmodus verwendet ausschließlich Ollama auf diesem Rechner und benötigt weder OpenRouter-Key noch Credits. Modellfragen und Bewertungen können sachlich falsch sein; die Quellenprüfung sichert nur das wörtliche Zitat, nicht die gesamte Aussage.

Unter „Lernkarten exportieren“ beantwortete Runden samt Quelle prüfen, das Kontrollkästchen markieren und „CSV herunterladen“ wählen. Die Datei enthält Frage, Musterantwort, Quellenzitat, Pfad, Seite, SHA-256 und Fundstellen-ID. Gleiche Fragen zur selben Quellenfassung werden nur einmal ausgegeben; offene Runden bleiben außen vor. Der Export umfasst die 50 zuletzt geänderten, in der Ansicht geladenen Runden und läuft ohne neuen Modellaufruf. Die UTF-8-Datei mit Semikolontrennung lässt sich in Tabellenprogrammen öffnen; vor einem Import oder einer Weitergabe bitte den persönlichen Dateipfad und die Inhalte prüfen.

Für weitere Fragen zum selben Thema dieselben Fundstellen ausgewählt lassen und erneut „Belegte Frage erstellen“ wählen. Jarvis nennt dem lokalen Modell die bisherigen Fragen zur Quellenfassung und prüft anschließend selbst, ob Frage und Quellenhash bereits vorkommen. Bei einem Duplikat versucht es einmal eine andere Frage. Gelingt das nicht, erscheint ein Hinweis; Thema oder Fundstelle ändern und erneut versuchen. Jede neue Frage bleibt einzeln prüfbar und korrigierbar. Die automatische Wiederholung kostet höchstens einen zusätzlichen lokalen Modellaufruf und benötigt keine Cloud.

„Prüfungsmodus öffnen“ startet aus diesen beantworteten Fragen eine Übungsprüfung mit wählbarer Fragenzahl und Zeitlimit. Während der Bearbeitung bleiben Musterantwort und Quelle verborgen. „Prüfung abgeben“ oder ein abgelaufenes Zeitlimit sperrt die Eingaben und zeigt Musterantwort, Zitat, Fundstelle und Hash. Jede Antwort wird selbst als richtig, teilweise richtig oder falsch eingestuft; leere Antworten zählen als falsch. Die Auswertung nennt Fragen zum Wiederholen. Diese erste Prüfungsstufe läuft nur im aktuellen Tab, speichert keinen Prüfungsverlauf und ruft kein Modell auf.

Nach der Abgabe lässt sich mit „Prüfungsprotokoll als CSV herunterladen“ das aktuelle Ergebnis sichern. Die Datei enthält die eigenen Antworten und Selbsteinschätzungen sowie Musterlösungen und Quellenbelege. Leere Antworten werden als falsch und noch nicht bewertete als solche gekennzeichnet. Der Download bleibt lokal; die Datei kann persönliche Pfade enthalten.

Für eine direkte Wiederholung zuerst das Protokoll der alten Runde bei Bedarf herunterladen, dann „Frage(n) erneut üben“ wählen. Jarvis übernimmt nur falsch oder teilweise bewertete und leere Antworten, setzt Eingaben und Bewertungen zurück und startet ein neues wählbares Zeitlimit. Lösungen und Quellen bleiben bis zur erneuten Abgabe verborgen. Noch nicht bewertete Antworten werden nicht als Fehler übernommen; bei Bedarf zuerst bewerten. Der neue Versuch bleibt ebenso nur in diesem Tab.

### Erinnerungen aus Chats bewusst prüfen

Unter „Gedächtnis“ ein nicht archiviertes Gespräch wählen und „Erinnerungen vorschlagen“ klicken. Jarvis gibt höchstens sechs jüngste fertige **eigene** Nachrichten an lokales Ollama und zeigt höchstens drei kurze Vorschläge. Jeder Vorschlag enthält einen wörtlichen Ausschnitt, Nachrichtenzeit und einen Sprung zur Originalstelle im Chat. Der Modelltext ist zunächst nur ein Vorschlag und wird nicht für spätere Antworten verwendet. Bei einem identischen vorhandenen Erinnerungstext erscheint ein Duplikathinweis.

Den Text prüfen und bei Bedarf im Feld korrigieren. „Bestätigen“ speichert genau diesen Text als lokale, bewusst gepflegte Erinnerung mit Chat- und Nachrichtenbeleg; „Verwerfen“ speichert keine Erinnerung. Optional „Gültig ab“ und „Gültig bis“ als Datum setzen. Der Endtag zählt noch als gültig; davor beziehungsweise danach ist die Erinnerung sichtbar, kann aber nicht für eine Chatantwort gewählt werden. Die Gültigkeit kann später zusammen mit dem Text korrigiert oder durch Leeren eines Datumsfelds aufgehoben werden. Eine bestätigte Erinnerung lässt sich hier später korrigieren oder nach Bestätigung löschen. Der Entscheidungsverlauf mit den damaligen Gültigkeitsdaten und die Originalstelle bleiben auch nach dem Löschen der Erinnerung im normalen Jarvis-Backup erhalten. Wird die Erinnerung an anderer Stelle geändert oder gelöscht, zeigt die Prüfung einen Konflikt und überschreibt sie nicht. Das Löschen des gesamten Chats entfernt die ursprüngliche Nachricht; der bereits gespeicherte Quellausschnitt bleibt als gekennzeichneter Herkunftsbeleg.

Für eine neue lokale Chatnachricht können unter „Chat → Erinnerungen für diese Antwort“ bis zu drei **bestätigte, aktuell gültige** Erinnerungen angekreuzt werden. Nur diese Auswahl geht als Daten in die nächste Ollama-Anfrage und wird danach abgewählt. Die fertige Antwort zeigt den tatsächlich verwendeten Text und seine ursprüngliche Stelle; die Gedächtnisprüfung zeigt den letzten Einsatz samt damaliger Fassung. Eine spätere Korrektur ändert alte Antwortbelege nicht. Verworfenes, Gelöschtes, Abgelaufenes oder an anderer Stelle Geändertes kann nicht ausgewählt werden. Unter „Gedächtnis → Widersprüche prüfen“ vergleicht lokales Ollama auf Wunsch bis zu 20 bestätigte, aktuell gültige Erinnerungen und zeigt höchstens fünf mögliche Widersprüche mit wörtlichen Stellen beider Texte. Das ist eine Prüfhilfe; der Nutzer entscheidet über Korrektur oder Löschung, und ein leerer Befund garantiert keine Widerspruchsfreiheit. OpenRouter nimmt diese lokalen Erinnerungen nicht an. Es gibt weiterhin keine automatische Übernahme in den Chatkontext. Es wird kein OpenRouter-Key benötigt.

### Freigegebene Dateien als Graph prüfen und nach Obsidian exportieren

Unter „Dokumente“ einen vorhandenen absoluten Code-/Notizordner freigeben. Unter „Datei-Graph“ diesen Ordner auswählen und „Graph erstellen“ klicken. Die gepinnte lokale Version `graphifyy==0.9.101` liest Code als AST; Jarvis ergänzt direkte Verweise zwischen Markdown-Dateien. Die Kanten zeigen Typ, `EXTRACTED` oder `INFERRED` sowie Datei und Zeile. Der Datei-Graph bleibt vom persönlichen Projektgraphen getrennt. Höchstens 100 unterstützte Dateien mit insgesamt 5 MiB werden gelesen; versteckte Pfade und Datei-/Ordnerverknüpfungen werden ausgelassen. Graphify läuft hier mit `GRAPHIFY_BACKEND=none`, `--code-only` und ohne Cloud-Schlüssel. Die normale Einrichtungszeile `uv sync --project backend` installiert die gepinnte Version.

Für den Export einen **vorhandenen Obsidian-Vault** als absoluten Pfad angeben. „Pfad prüfen“ zeigt den genauen neuen Zielunterordner `Jarvis Graphify/Graph-…` und die Dateien; erst „Jetzt exportieren“ schreibt `Graph.md`, verlinkte Knoten-Notizen und `manifest.json`. Ein vorhandener Zielordner wird nie überschrieben, die Vault-Einstellungen unter `.obsidian` bleiben unangetastet. Unter „Eigene Exporte zurücknehmen“ entfernt Jarvis nur die im Manifest verzeichneten Dateien, sofern ihre SHA-256-Hashes unverändert sind. Die Liste bleibt nach einem Jarvis-Neustart erhalten. Der Export ist ein lokaler Obsidian-Dateigraph mit Belegen; er legt keine fremden Notizen um und benötigt keinen OpenRouter-Key.

### Fertige Chatantworten lokal vorlesen

[VoiceStudio](https://github.com/debpalash/VoiceStudio) läuft als separater lokaler Dienst. Auf einem Windows-PC ohne NVIDIA-GPU die [offizielle Windows-Installation](https://github.com/debpalash/VoiceStudio/blob/main/docs/install/windows.md) verwenden: den aktuellen `VoiceStudio-<Version>-win-x64.exe`-Installer von den [Releases](https://github.com/debpalash/VoiceStudio/releases/latest) herunterladen, mit `SHA256SUMS.txt` vergleichen und installieren. Auf dem ersten Bildschirm „Install local runtime“ wählen. Danach in der Modellübersicht eine deutschfähige Stimme installieren und aktivieren. Die Desktop-App startet ihren lokalen Dienst auf `127.0.0.1:3900`; in diesem Modus benötigt Jarvis keinen VoiceStudio-API-Schlüssel. Wenn zuvor der Docker-Helfer benutzt wurde, `JARVIS_VOICESTUDIO_API_KEY` in der lokalen `.env` für den Desktop-Modus leeren und Jarvis neu starten.

Eine getestete CPU-Auswahl ist `MOSS-TTS-Nano` unter **Settings → Models → TTS**. Laut [Projekt-README](https://github.com/OpenMOSS/MOSS-TTS-Nano#supported-languages) unterstützt es Deutsch. Bei einem `uv`-Fehler zum Python-Versionslink kann ein kürzerer „App environment“-Ordner unter dem Benutzerprofil helfen; VoiceStudio danach erneut installieren lassen.

Für andere Stimmen die jeweilige Modellkarte und Lizenz prüfen. Die VoiceStudio-Anwendung ist separat lizenziert.

Bei späteren Sitzungen zuerst die installierte Desktop-App öffnen und ihre lokale API unter `http://127.0.0.1:3900/health` prüfen. Jarvis anschließend im Projektordner starten:

```powershell
& "$env:LOCALAPPDATA\Programs\VoiceStudio\VoiceStudio.exe"
# Nach dem VoiceStudio-Start in einem zweiten Terminal im Jarvis-Projektordner:
python scripts/dev.py
```

Alternativ startet der Jarvis-Docker-Helfer VoiceStudio als separaten Container (Docker Desktop muss laufen):

```powershell
.\scripts\start_voicestudio.ps1
python scripts/dev.py
```

Der Helfer erzeugt bei Bedarf einen **lokalen** 64-stelligen Schlüssel in der ignorierten `.env`, lädt das offizielle stabile VoiceStudio-Image und bindet nur `127.0.0.1:3900`. Das ist kein OpenRouter-Key und kostet keine API-Credits. Beim ersten Start in [VoiceStudio](http://127.0.0.1:3900) ein für Deutsch geeignetes Modell und eine Standardstimme einrichten; Lizenz und Größe der gewählten Modellgewichte werden dort separat angezeigt. Die [voreingestellten OmniVoice-Gewichte](https://huggingface.co/k2-fsa/OmniVoice/blob/c5fdb5ccb189668d56333f77ba2629f4cd7535f4/README.md) stehen laut Modellkarte unter CC-BY-NC für nichtkommerzielle Nutzung; die [VoiceStudio-Anwendung](https://github.com/debpalash/VoiceStudio/blob/main/LICENSE-NOTICE.md) ist AGPL-3.0. Die Jarvis-Vorgaben `JARVIS_VOICESTUDIO_MODEL=tts-1` und `JARVIS_VOICESTUDIO_VOICE=default` nutzen das dort aktive Modell und dessen Standardstimme. Für eine andere installierte Stimme oder ein anderes Modell diese beiden Werte in `.env` anpassen und Jarvis neu starten. Die [VoiceStudio-API](https://github.com/debpalash/VoiceStudio/blob/main/docs/agentic-voice.md) verwendet `POST /v1/audio/speech` mit WAV-Ausgabe; Jarvis erlaubt für diese Verbindung nur eine Loopback-Adresse, folgt keinen Weiterleitungen und ignoriert Proxy-Umgebungsvariablen.

Unter „Chat“ bei einer **fertigen** Antwort „Vorlesen“ wählen. Während der Erzeugung erscheint ein Status, danach Stimme, Audioplayer, „Stoppen“ und „Wiedergeben“. Der Text bleibt unverändert. Antworten bis 6000 Zeichen werden bei Bedarf in Teile zerlegt; WAV-Dateien liegen nur im Jarvis-Datenordner `speech/`, höchstens 30 Dateien/128 MiB und 32 MiB pro Antwort. Chat-Löschen entfernt dessen Audiodateien; das normale Backup enthält diesen regenerierbaren Cache nicht. Ist VoiceStudio nicht gestartet oder das Modell nicht bereit, erklärt Jarvis den Fehler; Textchat funktioniert weiter. Die Windows-Desktop-App über ihr Menü beenden; nur den Docker-Container mit folgendem Helfer stoppen:

```powershell
.\scripts\stop_voicestudio.ps1
```

Unter „Aktivität“ kann auch ein Ziel für einen **Mehrschrittplan** eingegeben werden. Das Modell schlägt höchstens `JARVIS_MAX_STEPS` registrierte Werkzeugschritte vor. Argumente und Wirkungen sind vor dem Start sichtbar; bei einer Datei zeigt die Vorschau zusätzlich den lokalen Pfad und verweigert Dateikennungen außerhalb der angebotenen Auswahl. Ein Schreibschritt wartet zusätzlich auf Freigabe des genauen Argument-Hashes. Ein Fehler oder unbekanntes Ergebnis stoppt den Plan. Die gesamte aktive Ausführungszeit ist auf fünf Minuten begrenzt; die Wartezeit auf eine menschliche Freigabe zählt nicht dazu. Pläne bleiben nach Neustart im Verlauf. `JARVIS_MAX_CONTEXT_CHARS` begrenzt den Eingabetext; ohne ausdrücklich erzeugten Gesprächszustand wird bei langen Chats nur der neueste passende Verlauf an das Modell gesendet; mit lokalem Zustand werden ältere Punkte samt Quellen-IDs und neuere Rohbeiträge kombiniert.

Soll das Modell in einem Mehrschrittplan eine Webseite lesen, die **vollständige HTTP(S)-URL ausdrücklich im Auftrag angeben**, zum Beispiel „Lies https://example.com/“. Jarvis bietet dem Modell das Browserwerkzeug sonst nicht an und verwirft vorgeschlagene andere Ziele oder zusätzliche Pfad-/Query-Werte auch beim späteren Start und nach Neustart. Dateinamen und Dokumentinhalte erweitern diese Webfreigabe nicht. Ein direkter, bewusster `browser_read`-Aufruf unter „Aktivität“ bleibt mit dem öffentlichen URL-/Weiterleitungsschutz möglich.

### Gespräche suchen, archivieren und löschen

Unter „Chat“ lassen sich bestehende Gespräche mit „Gespräche suchen“ nach Titel oder Nachrichteninhalt durchsuchen. Die Suche bleibt lokal und startet keinen Modellaufruf. „Archivieren“ blendet ein Gespräch aus der normalen Liste aus; „Archiv anzeigen“ zeigt archivierte Gespräche, die sich dort wiederherstellen und danach fortsetzen lassen. Ein archiviertes Gespräch kann gelesen, aber erst nach Wiederherstellung weitergeführt werden. „Löschen“ öffnet eine zweite, ausdrückliche Bestätigung; „Dauerhaft löschen“ entfernt nur das ausgewählte Gespräch samt Nachrichten und Antwortbewertungen. Andere Gespräche bleiben erhalten. Die Nutzungsübersicht bewahrt Kosten-/Tokenbelege als lokale Abrechnung ohne Nachrichtentext. Archivstatus und Gespräche sind im normalen Jarvis-Backup enthalten; ältere Backup-Archive bewahren ihren damaligen Stand.

### Nächste Schritte aus einer Chatantwort vorbereiten

Unter der letzten fertig gespeicherten Chatantwort startet „Nächste Schritte vorschlagen“ auf Wunsch **einen lokalen Ollama-Aufruf**. Jarvis zeigt höchstens zwei kurze Vorschläge: eine Aufgabe oder eine öffentliche Recherche. Den Modelltext prüfen; „Ausblenden“ entfernt einen Vorschlag aus der aktuellen Ansicht. „Aufgabe vorbereiten“ beziehungsweise „Recherche vorbereiten“ öffnet das jeweilige vorhandene Formular mit editierbarem Text. Erst „Aufgabe speichern“ legt die Aufgabe an; erst „Recherche starten“ sucht und liest öffentliche Quellen. Bestehende Formulareingaben werden vor einer Übernahme geschützt und können bewusst beibehalten werden. Neue Chatnachrichten verwerfen ältere Vorschläge. Es gibt keinen automatischen Werkzeugaufruf, keine automatische Websuche und keinen OpenRouter-Aufruf für diese Vorschläge. Ohne laufendes Ollama ist die Funktion nicht verfügbar.

### Ältere Chatbeiträge lokal zusammenfassen

Ab zwölf fertigen Nachrichten zeigt der Chat „Ältere Beiträge zusammenfassen“. Ein Klick verarbeitet mit lokalem Ollama höchstens zwölf ältere Nachrichten und lässt mindestens die acht jüngsten unverändert. Der gespeicherte Gesprächszustand nennt Ziele, Entscheidungen, offene Punkte oder Quellen samt Quell-Nachrichten-IDs. „Quelle anzeigen“ führt zum ursprünglichen Beitrag; Modellzusammenfassungen bitte anhand dieser Beiträge prüfen. Weitere Klicks können zusätzliche ältere Abschnitte in einer neuen, unveränderlichen Fassung aufnehmen. Die gesamte Rohhistorie bleibt lesbar und im Backup erhalten. Beim nächsten lokalen Ollama-Chat geht die jüngste Fassung zusammen mit den noch nicht abgedeckten Rohbeiträgen innerhalb des Kontextlimits ans Modell. OpenRouter erhält diese lokalen Zusammenfassungen nicht; sein Chatverlauf benötigt weiterhin die eigene Cloud-Freigabe. Die Zusammenfassungen werden nicht als dauerhaftes persönliches Wissen gespeichert. Sehr große Einzelbeiträge oder ein voller Zustandsbereich melden einen begrenzten Fehler und lassen die letzte gültige Fassung bestehen.

Nach dem Prüfen kann ein falsch erkannter Punkt mit „Diesen Punkt entfernen“ aus dem **künftigen** Zustand genommen werden; beim letzten Punkt stattdessen „Aktuellen Zustand zurücksetzen“ und die zweite Bestätigung wählen. Beides erstellt ohne Modellaufruf eine neue Fassung, die als manuelle Änderung gekennzeichnet ist. Ältere Fassungen und alle Rohbeiträge bleiben lesbar. Nach dem Zurücksetzen nutzt der nächste lokale Chat wieder rohe Beiträge und ein neuer Verdichtungsklick kann von vorn beginnen. Unter jeder späteren Antwort steht die beim Start verwendete Zustandsfassung oder „ohne Gesprächszustand“, damit Änderungen während einer Antwort den Beleg nicht nachträglich verfälschen.

Lokale Ollama-Chats schätzen die Eingabe zusätzlich aus UTF-8-Text und Nachrichtenrahmen. Die `.env`-Option `JARVIS_OLLAMA_CONTEXT_TOKENS=8192` legt das an Ollama gesendete `num_ctx` fest; Jarvis reserviert davon das konfigurierte Ausgabelimit plus 1024 Tokens als Sicherheitsraum. Die Schätzung ist **kein exakter Modell-Tokenizer**. Die Antwort zeigt geschätzte Eingabetokens/Budget und, falls Platz fehlte, die Zahl ausgelassener älterer Rohbeiträge. „Letzten ausgelassenen Beitrag zeigen“ führt zur Grenze im unveränderten Verlauf. Eine bewusste Verdichtung kann ältere Entscheidungen wieder in den Kontext holen. Die tatsächlichen Eingabetokens aus Ollamas Antwort stehen getrennt daneben. Das alte Zeichenlimit `JARVIS_MAX_CONTEXT_CHARS` gilt zusätzlich. Für OpenRouter bleibt der vorhandene separate Freigabe- und Budgetpfad maßgeblich.

### Werkzeugergebnisse bewerten

Unter „Aktivität → Aufträge“ bei einem abgeschlossenen Lauf „Ergebnis bewerten“ wählen. Das Werkzeugprotokoll zeigt Status, Ergebnis oder Fehler, Lauf-ID und die **tatsächlich protokollierte Werkzeugdauer**. „succeeded“ heißt nur, dass das Werkzeug ohne gemeldeten Fehler ausgeführt wurde. Wähle den tatsächlichen Aufgabenerfolg (erreicht, teilweise, verfehlt oder unklar) und begründe ihn anhand des sichtbaren Ergebnisses. Die Bewertung lässt sich später korrigieren; gleichzeitige Änderungen erzeugen einen Versionskonflikt und überschreiben keine neuere Beurteilung.

Bei einem teilweisen oder verfehlten Ergebnis kann „Als Fehler-Testfall sichern“ aktiviert und das erwartete Ergebnis beschrieben werden. Der Fall verweist auf die ursprünglichen Werkzeugargumente, den Argument-Hash und den beobachteten Lauf. Unter „Gesicherte Fehler-Testfälle“ ist er nach einem Neustart wieder sichtbar; das Backup enthält ihn ebenfalls. Ein Testfall führt das Werkzeug **nicht automatisch erneut aus**. Modellkosten sind bisher nach Chat-/Entwurfs-/Planzweck belegt, aber keinem einzelnen Werkzeuglauf eindeutig zugeordnet; die Laufansicht zeigt deshalb „nicht zugeordnet“. Die separaten Modellkostenbelege stehen weiter unter „Übersicht“.

### Mehrschrittpläne bewerten

Unter „Aktivität → Mehrschrittauftrag planen“ zeigt jeder neue Plan die belegte Dauer der Modellanfrage, Eingabe- und Ausgabetokens sowie die API-Kostenart des Vorschlags. Für Ollama steht „keine API-Gebühr (lokal)“; Strom- und Hardwarekosten sind damit nicht erfasst. Werkzeugkosten werden dem Vorschlag nicht zugerechnet. Ältere Pläne ohne eindeutige Modellzuordnung zeigen „kein eindeutiger Modellbeleg“.

Nach einem abgeschlossenen, gestoppten oder fehlgeschlagenen Plan „Plan bewerten“ wählen. Aufgabenerfolg und Quellenlage selbst einschätzen und anhand des Werkzeugergebnisses begründen. Ein teilweiser oder verfehlter Plan kann mit dem erwarteten Ergebnis als Plan-Fehler-Testfall gesichert werden. Der Fall enthält ursprüngliches Ziel, Schritte, Planstatus, Regelversion und vorhandenen Modellbeleg; „Gesicherte Plan-Fehler-Testfälle“ zeigt ihn auch nach einem Neustart. Bewertungen sind gegen parallele Änderungen geschützt und verändern weder den Plan noch seine Nutzungsbelege. Kein Fehlerfall wird automatisch erneut ausgeführt.

`local_only`-Quellen werden nur mit Ollama verarbeitet. Für OpenRouter verlangt die Oberfläche eine separate Cloud-Freigabe für ausgewählte Quellen, Schreibproben und den jeweiligen Chatverlauf oder Entwurf. Bei freigegebenen Dokumenten gehen ausgewählte Ausschnitte und Dateinamen an das Modell; vollständige lokale Ordnerpfade werden dabei nicht automatisch beigefügt. Pfad und Hash bleiben in der lokalen Quellenanzeige erhalten. Inhalte aus Dokumenten und Webseiten gelten als Daten, nicht als Anweisungen.

Unter „Übersicht“ stehen die letzten 20 Modellläufe mit Zweck, Provider, Modell, Tokenzahlen, Status und Kostenart. „Belege aktualisieren“ lädt neue Chat-, Entwurfs- und Planaufrufe. Für Ollama zeigt Jarvis „keine API-Gebühr (lokal)“; das ist keine Aussage über Stromkosten. Bei OpenRouter unterscheidet die Anzeige gemeldete Preise, Schätzungen und reservierte Obergrenzen. Das Tagesbudget steht darüber.

### Chat-Regeln versionieren

Unter „Chat-Regeln“ die aktuell aktive Zusatzanweisung und alle gespeicherten Fassungen ansehen. Im Feld „Neue Chat-Regel“ eine Anweisung bis 2.000 Zeichen eingeben und „Neue Fassung aktivieren“ wählen. Jede Fassung bleibt unverändert gespeichert; „Diese Fassung aktivieren“ setzt eine frühere Fassung wieder ein, auch die erste Fassung ohne Zusatz. Der Aktivierungsverlauf zeigt die Wechsel. Ein paralleler Änderungsversuch mit veralteter Revision wird abgewiesen und die Ansicht neu geladen.

Neue Chatantworten zeigen unter dem Text ihre tatsächlich verwendete Regelversion. Die Version wird beim Start der Modellanfrage festgehalten und bleibt auch bei einem abgebrochenen oder fehlgeschlagenen Lauf am gespeicherten Assistentenbeitrag. Ältere Antworten vor dieser Funktion zeigen „alte Antwort ohne Regelbeleg“. Die feste Grundanweisung bleibt bestehen; Quellenfreigaben, Providerwahl und Werkzeugrechte werden weiterhin durch die App durchgesetzt. Diese Zusatzregel gilt derzeit nur für den Chat, nicht für Entwürfe oder Mehrschrittpläne. Bei OpenRouter wird die aktive Zusatzregel als Teil der Modellanfrage an den Provider gesendet; für private Regeln Ollama verwenden.

### Chat-Skills anlegen und bewusst auswählen

Unter „Chat-Skills“ einen Namen und eine Zusatzanweisung bis 2.000 Zeichen eingeben. Der neue Skill ist aktiv, wird aber erst verwendet, wenn du ihn im Chat unter „Chat-Skill“ für die nächste Nachricht auswählst. „Kein Skill“ sendet keine Skill-Anweisung. Bei jeder neuen Nachricht kann die Auswahl geändert werden. Falls ein ausgewählter Skill inzwischen deaktiviert wurde, lehnt Jarvis die Anfrage ab, bevor eine Nachricht oder Modellnutzung gespeichert wird.

Für bestehende Skills eine neue Fassung anlegen, deaktivieren oder eine frühere Fassung aktivieren. Revisionen schützen gegen parallele Änderungen. Die alten Inhalte, SHA-256-Belege und Aktivierungswechsel bleiben im lokalen Profil erhalten. Neue Antworten und gesicherte Antwort-Testfälle zeigen die tatsächlich verwendete Skill-Version und den Hash-Präfix; ein späterer Wechsel überschreibt diesen Beleg nicht. Die App prüft den Hash vor der Nutzung. Skill-Text erweitert nur den Chat-Prompt; Werkzeugrechte, Quellenfreigaben und feste Systemregeln bleiben im Code. Bei OpenRouter wird ein ausgewählter Skill mit der ausdrücklich freigegebenen Chatanfrage übertragen, daher für private Skills Ollama verwenden. Entwürfe und Mehrschrittpläne verwenden diese Chat-Skills derzeit nicht.

### Entwurfsregeln versionieren

Unter „Entwurfsregeln“ eine zusätzliche Anweisung für neue Entwürfe eingeben und „Neue Fassung aktivieren“ wählen. Alle Fassungen bis 2.000 Zeichen bleiben unveränderlich im lokalen Profil; „Diese Fassung aktivieren“ kehrt auch zur ursprünglichen Fassung ohne Zusatz zurück. Die Ansicht zeigt die aktive Revision und jeden Wechsel. Bei gleichzeitigen Änderungen verlangt die API die aktuelle Revision und meldet einen Konflikt, statt eine andere Änderung still zu überschreiben.

Ein neu erzeugter Entwurf zeigt seine beim Start des Modellaufrufs verwendete Regelversion in der Liste und beim Bearbeiten. Bearbeitung und Export erhalten diesen Beleg auch nach einem späteren Regelwechsel. Ältere Entwürfe ohne Regelbeleg werden entsprechend gekennzeichnet. Stilprofil und Schreibproben sind eigenständige Eingaben; die feste Systemanweisung, Quellenfreigaben und das Verbot eines automatischen Versands bleiben wirksam. Für einen OpenRouter-Entwurf würde die aktive Zusatzregel mit der ausdrücklich freigegebenen Modellanfrage übertragen; lokal mit Ollama wird kein API-Key benötigt.

### Planregeln versionieren

Unter „Planregeln“ eine zusätzliche Anweisung für neue modellgestützte Mehrschrittplan-Vorschläge eingeben. Neue Fassungen werden sofort aktiv, bleiben unveränderlich und können über „Diese Fassung aktivieren“ bewusst zurückgesetzt werden. Die aktive Revision und der Wechselverlauf sind sichtbar; parallele Änderungen mit veralteter Revision werden abgewiesen. Jeder neu vorgeschlagene Plan zeigt die beim Modellaufruf verwendete Planregelversion. Auch nach Regelwechsel, Planstart oder Wiederaufnahme bleibt dieser Beleg erhalten; ältere Pläne ohne Beleg werden entsprechend gekennzeichnet.

Die Planregel beeinflusst nur den Vorschlag des Modells. Sie fügt keine Werkzeuge oder Rechte hinzu: Schema-Prüfung, Schritt-/Zeitlimit, Dateiauswahl, ausdrückliche Webziele und Freigabe vor Schreibaktionen gelten weiterhin im App-Code. Bei OpenRouter wird die aktive Planregel mit dem ausdrücklich freigegebenen Auftragsziel übertragen; für rein lokale Regeln Ollama nutzen.

### Chatantworten bewerten

Unter einer gespeicherten Jarvis-Antwort im „Chat“ „Antwort bewerten“ wählen. Die Zeile nennt Modell, Status, Regelversion, Token und Kostenart. Bei neuen Antworten zeigt „Anfrage … s“ die Zeit vom angelegten Modellnutzungsbeleg bis zu dessen Abschluss; das ist die gesamte Anfrage einschließlich lokaler Verarbeitung, nicht die reine Modell-Rechenzeit. Ältere Antworten ohne eindeutige Nutzungs-ID erhalten keine nachträglich geschätzte Dauer.

Aufgabenerfolg und Quellenlage selbst prüfen und mit einer kurzen Begründung speichern. Jarvis behauptet damit keine automatische Faktenprüfung. Bei teilweisem oder verfehltem Ergebnis lässt sich ein Antwort-Testfall mit erwartetem Ergebnis festhalten. „Gesicherte Antwort-Testfälle“ zeigt die ursprüngliche Frage, tatsächliche Antwort, Regelversion und Erwartung auch nach einem Neustart. Die Fälle führen keine neue Modellanfrage aus. Änderungen an einer Bewertung sind versionsgeschützt; der gespeicherte Antworttext und der ursprüngliche Modell-/Kostenbeleg bleiben unverändert. Kosten von Ollama sind nur „keine API-Gebühr“, nicht null Strom- oder Hardwarekosten.

Die Dokumentenfreigabe lässt sich in der Oberfläche wieder entfernen. Ausgeschlossene Dateien verlieren ihre lokalen Suchtexte; nach „Einbeziehen“ den Index erneut aktualisieren. Ein Indexlauf besucht höchstens 2.000 Verzeichnisse und 1.000 TXT-/Markdown-/Text-PDF-Dateien; ein begrenzter Lauf belässt nicht besuchte alte Indexeinträge. Verknüpfte Unterordner einschließlich Windows-Junctions werden übersprungen. Pro Datei gelten 20 MB, zwei Millionen extrahierte Zeichen und bei PDF höchstens 200 Seiten. Dateien ohne Textschicht erscheinen als „OCR ausstehend“.
Bei indexierten Dateien zeigt die Dokumentenliste den letzten Indexzeitpunkt und den vollständigen SHA-256-Hash der eingelesenen Version. Nach Änderungen an Originaldateien „Index aktualisieren“ wählen; Jarvis überwacht die freigegebenen Ordner nicht ständig.
Ändert sich eine Datei während des Einlesens, verwirft Jarvis diesen Indexversuch und zeigt einen Fehler. Nach Abschluss der Dateisynchronisierung den Indexlauf erneut starten.
Ist ein Unterordner vorübergehend nicht lesbar, meldet der Indexlauf einen Fehler und belässt dessen bisherige Einträge bis zu einem vollständigen Scan. Ein Fehler beim Lesen einer einzelnen Datei verhindert dagegen nicht, dass andere tatsächlich gelöschte Dateien aus dem Index entfernt werden.
Wird ein freigegebener Ordner später verschoben oder auf ein anderes Ziel umgeleitet, verweigert Jarvis Indexierung und Dateiöffnung. Den gewünschten Ordner dann erneut freigeben.

## Lokale Markdown-Notizen bearbeiten

Unter „Dokumente“ einen Ordner freigeben und indexieren. Bei einer indexierten `.md`- oder `.markdown`-Datei „Markdown bearbeiten“ wählen, Text ändern und „Änderungsvorschau erstellen“ drücken. Die Vorschau zeigt den vollständigen Unterschied und die Hashes der alten und neuen Datei; erst „Gezeigte Änderung speichern“ schreibt die Originaldatei. Danach wird der Suchindex aktualisiert. „Letzte Änderung rückgängig“ stellt die vorherige Dateifassung wieder her und aktualisiert den Index erneut. Mehrere Änderungen lassen sich in umgekehrter Reihenfolge zurücknehmen.

Wenn die Datei seit dem Öffnen oder der Vorschau in einem anderen Programm geändert wurde, stoppt Jarvis mit einem Konflikt; „Datei neu laden“ übernimmt dann den aktuellen Stand. Bearbeitbar sind nur bereits indexierte, nicht ausgeschlossene UTF-8-Markdown-Dateien im freigegebenen Ordner bis 200 KB. Verknüpfte Dateien und Unterordner einschließlich Windows-Junctions werden abgewiesen. Beim Ausschließen einer Datei löscht Jarvis die gespeicherten Vorversionen aus seiner Datenbank, ohne die Originaldatei zu ändern. Die lokale Backup-Datei enthält Vorversionen in SQLite, aber keine externen Quelldateien; nach Wiederherstellung funktioniert Rückgängig nur, wenn die Originaldatei noch genau dem gespeicherten Stand entspricht.

## Arbeitsstand manuell sichern und fortsetzen

Unter „Arbeitsstände“ „Neuen Arbeitsstand anlegen“ wählen. Projektname und **nächsten Schritt** eingeben; bestätigte Ergebnisse und offene Fragen in getrennte Felder schreiben. Die gewünschten indexierten Dateien und offenen Aufgaben einzeln anhaken, optionale HTTP(S)-Links zeilenweise eintragen und „Arbeitsstand speichern“ drücken. Jarvis übernimmt nur diese Auswahl. Eine Datei, die sich seit der Indexierung geändert hat, muss zuerst unter „Dokumente“ neu indexiert werden.

Nach einem Neustart den Arbeitsstand unter „Arbeitsstände“ öffnen. Jede Datei zeigt den beim Sichern erfassten Hash und ihren aktuellen Zustand: unverändert, geändert, fehlend, ausgeschlossen oder nicht mehr verfügbar. Nur eine unveränderte und weiterhin freigegebene Datei lässt sich über „Datei öffnen“ starten; das Werkzeug prüft ihren Hash unmittelbar vor der Öffnung erneut. „Link prüfen“ startet bewusst das bestehende eingeschränkte Browserwerkzeug und meldet Erfolg oder Fehler. Aufgaben zeigen ihren aktuellen Status, auch wenn sie inzwischen erledigt oder gelöscht wurden. Der Arbeitsstand kann mit Versionsprüfung bearbeitet oder in zwei Schritten gelöscht werden; die referenzierten Dateien und Aufgaben bleiben dabei bestehen. Links werden beim Speichern syntaktisch geprüft, ihre Erreichbarkeit erst nach Klick auf „Link prüfen“. Es findet keine automatische Hintergrundaufnahme statt.

Unter „Personen im Projekt“ lässt sich zu diesem Arbeitsstand eine Person mit Beziehung und Herkunftsnotiz ausdrücklich eintragen. Optional eine der bereits ausgewählten, unveränderten Projektdateien als zusätzlichen Beleg wählen; Jarvis speichert ihren Pfad und SHA-256 zu dieser Beziehung. Die Notiz allein ist als **eigene Angabe** gekennzeichnet, nicht als geprüfte Tatsache. „Status neu prüfen“ zeigt geänderte, entzogene oder im Arbeitsstand erneuerte Quelldateien. Eine Beziehung kann mit Versionsschutz korrigiert und nach Bestätigung entfernt werden. Personenangaben bleiben lokal und werden nicht automatisch für Chat, Recherche oder ein dauerhaftes Gedächtnis genutzt. Das Löschen des Arbeitsstands entfernt seine Personenbeziehungen; die zugrunde liegenden Dateien und Aufgaben bleiben bestehen.

Für die belegbare Antwort auf „Welche Personen, Unterlagen und Aufgaben gehören zu diesem Projekt?“ den Arbeitsstand öffnen und **„Projektbeziehungen lokal abfragen“** klicken. Jarvis zeigt die ausdrücklich zugeordneten Personen mit Rolle und Herkunftsnotiz, unterscheidet eigene Angaben, unveränderte Dateibelege und prüfbedürftige Belege und zeigt die ausgewählten Dateien mit aktuellem Status sowie die Aufgaben mit ihrem aktuellen Status. Arbeitsstand-Fassung und Prüfzeit stehen über dem Ergebnis. „Personenbeleg ansehen“, „Quelldatei ansehen“ und „Aufgabe ansehen“ springen zu den Details im selben Projekt. Nach einer Änderung „Projektbeziehungen lokal abfragen“ erneut klicken; der alte Abfragestand wird bei Änderungen in der Oberfläche entfernt. Die Abfrage liest nur lokale Daten, erzeugt keine neuen Beziehungen und ruft kein Modell auf. Ihre lokale API lautet `GET /work-states/{id}/connections`.

Unter „Aufgaben und Personen verbinden“ eine bereits zugeordnete Projektperson und eine ausgewählte Projektaufgabe wählen. Die Rolle bei dieser Aufgabe und eine Herkunftsnotiz sind Pflicht; optional lässt sich eine unveränderte Projektdatei als zusätzlicher Beleg wählen. „Aufgabe und Person verbinden“ speichert erst nach dem Klick. Die Liste zeigt den aktuellen Aufgaben- und Belegstatus sowie die Fassung der Verbindung; „Bearbeiten“ korrigiert sie mit Versionsschutz, „Entfernen…“ verlangt eine zweite Bestätigung. Bei Quellenänderung, entzogener Freigabe, erneuerter Quelle, gelöschter oder nicht mehr zugeordneter Aufgabe wird die Verbindung in der lokalen Projektabfrage als prüfbedürftig markiert. Das Entfernen einer Person oder des Arbeitsstands entfernt auch deren Aufgabenverbindungen; die Domain-Aufgabe und Dateien bleiben bestehen. Die Abfrage zeigt danach zusätzlich den Pfad **Aufgabe → Person → Dateibeleg** mit Sprüngen zu Verbindung und Belegen. Diese Verknüpfungen bleiben lokal und werden nicht automatisch für Chat oder Cloud-Anfragen verwendet.

Für Personen und Aufgabenverbindungen können zusätzlich ein **Vertrauensgrad als eigene Einschätzung** und ein optionaler Gültigkeitszeitraum gesetzt werden. Neue und aus älteren Daten migrierte Beziehungen beginnen als „Ungeprüft“ ohne Zeitgrenze. „Plausibel“ oder „Bestätigt“ sind bewusste Nutzerangaben und ersetzen keinen Dateibeleg; eine geänderte oder entzogene Quelle bleibt weiterhin als prüfbedürftig sichtbar. „Gültig ab“ und „Gültig bis“ verwenden inklusive lokale Kalendertage im Format YYYY-MM-DD. Ein Zeitraum mit Ende vor Beginn wird abgewiesen. Die Projektabfrage nennt den lokalen Stichtag mit der aktuell in Jarvis gewählten IANA-Zeitzone und zeigt „Noch nicht gültig“ oder „Abgelaufen“ sowohl am Detail als auch im Antwortpfad. Nach Korrektur erneut lokal abfragen.

Unter „Personen-Aliase“ eine Projektperson auswählen, einen anderen Namen und eine Herkunftsnotiz eingeben und „Alias hinzufügen“ klicken. Optional eine unveränderte Projektdatei als Beleg sowie Vertrauen und Gültigkeit festlegen. Der Alias bleibt eine ausdrücklich gepflegte Namensvariante derselben Projektperson; Jarvis führt keine Personen automatisch zusammen. Doppelte Alias- oder Personennamen innerhalb des Projekts werden unabhängig von Groß-/Kleinschreibung abgewiesen. Höchstens fünf Aliase je Person und 30 je Projekt sind erlaubt. Alias und Beleg lassen sich mit Versionsprüfung bearbeiten oder nach Bestätigung entfernen. Wenn die Quelldatei geändert, erneuert oder entzogen wurde, zeigt die Aliasliste den entsprechenden Status. Die lokale Projektabfrage führt die Aliasnamen mit der kanonischen Person und einem Sprung zum Aliasbeleg auf. Beim Löschen der Person verschwinden ihre Aliase; Dateien und Aufgaben bleiben erhalten.

Unter „Personen zusammenführen“ zuerst die Quellperson und dann die Zielperson wählen und den Vorgang ausdrücklich bestätigen. Jarvis hält den ursprünglichen Quellnamen als Alias mit dessen Herkunft und verschiebt weitere Aliase sowie Aufgabenbeziehungen zur Zielperson; der Verlauf zeigt die Aktion. „Trennen…“ stellt den ursprünglichen Personeneintrag und die übertragenen Verbindungen wieder her. Das Trennen wird bei späterer Änderung an Zielperson, übertragenen Daten oder Quellnamen-Alias sowie bei Kollisionen abgewiesen, damit keine neuen Angaben überschrieben werden. Eine Zielperson mit aktiver Zusammenführung lässt sich erst nach dem Trennen löschen. Alles bleibt lokal; Jarvis führt Namen nicht automatisch zusammen.

Unter „Belegte Projektpfade“ „Projektgraph lokal laden“ wählen. Der Startknoten steht zunächst auf dem Projekt; für eine Pfadfrage einen Zielknoten wählen. Die Nachbarrichtung gilt nur für „Nachbarn abfragen“, Pfade folgen den angezeigten Pfeilen. Ohne gesetzte Beziehungstypen werden alle berücksichtigt; die Kontrollkästchen schränken beide Abfragen ein. Pfade sind auf drei Kanten, 20 Ergebnisse und 1000 Suchschritte begrenzt; ein Graph mit mehr als 100 Knoten oder 200 Kanten wird abgewiesen. Jeder Pfad zeigt die konkreten Beziehungstypen, gespeicherte Herkunft einschließlich ursprünglichem Belegpfad, Quellenstatus, gegebenenfalls Datei-Hash, Vertrauen und zeitliche Gültigkeit. Die Pfadknoten lassen sich zum zugehörigen Projektabschnitt öffnen. Nach einer Quellen- oder Beziehungsänderung den Graph erneut laden. Die Abfragen verwenden nur die schon gespeicherten Projektangaben, keine Modellanfrage und keine fremden Projekte. Für die API: `GET /work-states/{id}/graph`, `/graph/neighbors?node=task:{id}&direction=out&relations=person_task` und `/graph/paths?source=project:{id}&target=document:{id}&max_depth=3&relations=project_task,person_task,person_evidence`.

Eine bestehende Erinnerung wird unter „Dokumente“ bewusst mit Text, Quelle und Policy gespeichert. Im geöffneten Arbeitsstand unter „Bewusste Erinnerungen im Projekt“ nach mindestens zwei Zeichen suchen, „Lokale Erinnerungen suchen“ klicken, die passende Erinnerung auswählen, eine Herkunftsnotiz zur Zuordnung eingeben und „Erinnerung zuordnen“ klicken. Pro Projekt sind höchstens zehn Verknüpfungen erlaubt. Der damalige Text, Quellenverweis, Policy und SHA-256 bleiben als Beleg gespeichert. „Status neu prüfen“ zeigt eine spätere Änderung oder Löschung der ursprünglichen Erinnerung; erst „Aktuelle Erinnerung bestätigen“ übernimmt geänderten Text, Quelle und Policy. Vertrauen und lokale Gültigkeitsdaten sind eigene Angaben und ersetzen die Quelle nicht. „Beleg bearbeiten“ ändert nur die Projektnotiz und diese Metadaten; „Verknüpfung entfernen…“ löscht nach zweitem Klick nur die Projektzuordnung, nicht die Erinnerung. Die lokale Projektabfrage zeigt den Erinnerungsbeleg, der Graph den Pfad **Projekt → Erinnerung**. Diese Zuordnung wird nicht automatisch in Chat-, Recherche- oder Cloud-Anfragen aufgenommen. Für die API: `GET/POST /work-states/{id}/memory-links`, `PUT/DELETE /work-states/{id}/memory-links/{link_id}` und `POST /work-states/{id}/memory-links/{link_id}/confirm`.

„Projektfristen“ zeigt die Fälligkeiten der beim Anlegen ausdrücklich ausgewählten Projektaufgaben chronologisch. Zu jeder Aufgabe stehen die beim Zuordnen gespeicherte lokale Zeit, IANA-Zeitzone, UTC-Offset und gegebenenfalls das erste oder zweite Vorkommen der doppelt vorkommenden Winterzeitstunde neben dem aktuellen Stand. „Status neu prüfen“ kennzeichnet hinzugefügte, geänderte oder entfernte Fristen; ein älterer Arbeitsstand ohne Fristbeleg wird nicht nachträglich als unverändert ausgegeben. „Projektbeziehungen lokal abfragen“ zeigt dieselben Angaben, und „Belegte Projektpfade“ bietet den Typ **Aufgabe → Frist**. Eine Fälligkeit wird weiterhin unter „Aufgaben“ bearbeitet; im Projekt entsteht keine zusätzliche Erinnerung oder externe Kalendersynchronisierung. Nur bereits ausgewählte Aufgaben erscheinen.

## Globaler Hotkey auf Windows (S5a)

Solange `python scripts/dev.py` läuft, registriert der kleine Windows-Helfer `Ctrl+Alt+J`. In einer Anwendung Text markieren oder im Explorer Dateien auswählen und die Kombination drücken. Jarvis kopiert diese Auswahl einmalig, stellt die vorherigen Zwischenablageformate wieder her und öffnet einen neuen Browser-Tab. Bei Text ist das Chat-Eingabefeld vorausgefüllt und fokussiert; erst ein bewusster Klick auf „Senden“ startet Ollama. Bei Dateien zeigt Jarvis zunächst nur die ausgewählten Pfade. Für Dateiinhalte den Ordner unter „Dokumente“ freigeben und indexieren. Auswahl und Zwischenablage werden nicht dauerhaft überwacht.

Bei einem Konflikt die Kombination in `.env` mit `JARVIS_HOTKEY=CTRL+SHIFT+J` ändern und Jarvis neu starten. Zulässig sind `CTRL`, `ALT`, `SHIFT` und eine Taste von `A` bis `Z` oder `0` bis `9`; mindestens `CTRL` oder `ALT` ist nötig. Der Helfer meldet eine belegte Kombination im Startterminal, während Backend und Oberfläche weiterlaufen. Falls eine Anwendung kein `Ctrl+C` für die Auswahl unterstützt, zeigt Jarvis einen Fallback im Chat. Kann der Helfer ein vorhandenes Zwischenablageformat nicht sicher sichern (etwa ein Bitmap-Handle oder mehr als 64 MB), verändert er die Zwischenablage nicht und zeigt ebenfalls einen Fallback. Eine Anwendung mit höheren Windows-Rechten kann simulierte Tasteneingaben blockieren.

## Edge-Tab als Aufgabe übernehmen (S4a)

Jarvis zuerst mit `python scripts/dev.py` starten. In Edge `edge://extensions` öffnen, Entwicklermodus einschalten und über „Entpackte Erweiterung laden“ den absoluten Repository-Ordner `extension` auswählen. Die Erweiterung ist lokal und muss nicht veröffentlicht werden. Unter „Einstellungen“ in Jarvis „Neuen Kopplungscode erzeugen“ wählen; den Code innerhalb von fünf Minuten im Erweiterungs-Popup eingeben. Der Code verbindet genau diese Erweiterungs-ID mit dem lokalen Jarvis. Unter „Einstellungen“ kann die Verbindung wieder getrennt werden. Bei einer erneuten Installation ist eine neue Kopplung nötig.

Auf einer HTTP(S)-Webseite gewünschten Text markieren und auf „Tab an Jarvis“ in der Edge-Leiste klicken. Das Popup zeigt Titel, URL und markierte Auswahl. Eine konkrete Aufgabe eingeben; optional „Seitentext zusätzlich übernehmen“ wählen, um höchstens 20.000 Zeichen der sichtbaren Seite in die Vorschau aufzunehmen. Erst „Als Aufgabe speichern“ überträgt die gezeigten Daten an den Dienst auf `127.0.0.1:8765`. Unter „Aufgaben“ erscheint genau eine lokale Aufgabe mit anklickbarem Quellenlink. Ein Wiederholen derselben Übernahme erzeugt keine zweite Aufgabe. Es wird weder ein Modell gestartet noch Browserhistorie gelesen; Jarvis holt die Seite dabei nicht selbst ab. Für einen neuen Übernahmevorgang das Popup erneut öffnen. Bei gestopptem Jarvis erklärt das Popup den Verbindungsfehler.

Die Produktions-Erweiterung verlangt nur `activeTab`, `scripting`, lokalen Speicher und Zugriff auf `http://127.0.0.1:8765`. `activeTab` gilt erst nach bewusstem Klick auf das Erweiterungssymbol. Der automatisierte Popup-Test lädt eine **separate temporäre Kopie** mit Zusatzrechten ausschließlich für seine lokale Quellseite, weil Playwright einen Toolbar-Klick nicht direkt auslösen kann. Diese Testrechte stehen nicht in `extension/manifest.json`. Zusätzlich wurde die unveränderte Erweiterung mit echtem Edge-Toolbar-Klick und lokalem Kopplungscode geprüft: Titel, URL, Auswahl und optionaler Seitentext erschienen im Popup; Speichern erzeugte genau eine Aufgabe, der erneute Klick meldete das vorhandene Duplikat. `scripts/smoke_edge_toolbar_harness.py` stellt dafür ein temporäres Profil und eine Testseite bereit; der Toolbar-Klick erfolgt bewusst im sichtbaren Edge-Fenster. [Microsofts Anleitung zum lokalen Laden](https://learn.microsoft.com/en-us/microsoft-edge/extensions-chromium/getting-started/extension-sideloading) beschreibt die Edge-Schritte.

Die lokale API weist schreibende Anfragen über 1 MiB zurück, auch wenn der Client keine `Content-Length` angibt, und setzt `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff` und `X-Frame-Options: DENY`. Nach acht falschen Edge-Kopplungsversuchen für dieselbe Erweiterungs-ID einen neuen Code unter „Einstellungen“ erzeugen; bis dahin antwortet die Kopplung mit HTTP 429. Die Grenzen betreffen keine über lokale Pfade erstellten Backups. [Lokale Sicherheitsgrenzen und Fernzugriffsbedarf](docs/SECURITY.md) sind getrennt beschrieben.

## Manuelles Zeitmodell (A15a)

Unter „Zeitmodell“ wiederkehrende feste Zeiten mit Wochentagen, IANA-Zeitzone, Beginn, Ende und Puffern eintragen. Ein Ende vor oder gleich dem Beginn liegt am Folgetag, etwa bei Schlaf. „Regel an diesem Tag aussetzen“ entfernt genau ein Vorkommen; „Zusätzliche feste Zeit“ legt einen einmaligen Block an. Beides kann später bearbeitet oder gelöscht werden. In der Tagesansicht Datum und Ansicht-Zeitzone wählen: Sie zeigt jeden belegten Block, zusammengefasste freie Zeit und die tatsächlich verfügbaren Minuten. Überlappende Blöcke zählen nur einmal. Bei der Zeitumstellung hat ein Tag entsprechend 23 oder 25 reale Stunden; nicht existierende Uhrzeiten im Frühjahr werden auf den ersten gültigen Zeitpunkt vorgerückt, und die doppelte Stunde im Herbst wird konservativ vollständig belegt. Diese lokale Version benötigt kein Kalenderkonto.

## Abendplanung (A22a)

Zuerst unter „Aufgaben“ offene Aufgaben anlegen und unter „Zeitmodell“ feste Termine, Wegezeiten und Puffer eintragen. Dann „Abendplanung“ öffnen, Datum, Abendbeginn, Schlafziel und Pausenrhythmus wählen, Aufgaben ankreuzen und je eine geschätzte Dauer sowie Priorität von 1 bis 5 setzen. „Plan berechnen“ zeigt die zeitlich sortierten Arbeits- und Pausenabschnitte sowie nicht eingeplante Restminuten. Höhere Priorität geht vor; bei gleicher Priorität werden frühere Fälligkeiten zuerst berücksichtigt. Abschnitte liegen nur in freien Zeitfenstern vor dem Schlafziel und nie nach einer Aufgabefrist. Ein Schlafziel vor dem Abendbeginn gilt für den Folgetag.

„Plan speichern“ hält Eingaben und Ergebnis lokal fest. Nach geänderten Aufgaben oder Zeitblöcken einen gespeicherten Plan erneut öffnen; Jarvis kennzeichnet ihn als veraltet. Mit „Plan berechnen“ und „Neu berechnen und speichern“ entsteht die aktualisierte Fassung. „Neuer Plan“ beginnt einen weiteren Plan, „Diesen Plan löschen“ entfernt die aktuell geöffnete Fassung nach Bestätigung. Die Abendplanung benötigt weder Kalenderkonto noch OpenRouter-Key.

## Erinnerungen bestätigen und verschieben (J11a)

Im Aufgabenformular die Priorität 1 bis 5 wählen. Nach der Fälligkeit erscheint eine erste Meldung genau einmal. „Gesehen“ entfernt sie aus den neuen Hinweisen, hält sie aber im Erinnerungsverlauf zugänglich. „Bestätigen“ stoppt weitere Nachfragen. „Verschieben“ stellt den Hinweis für 15 Minuten, eine, vier oder 24 Stunden zurück; er bleibt unter „Zurückgestellte Erinnerungen“ sichtbar und wird danach erneut angezeigt. Die Verschiebung rechnet mit tatsächlichen UTC-Minuten, auch über eine Zeitumstellung hinweg.

Nur offene Aufgaben mit Priorität 4 oder 5 erhalten ohne Bestätigung automatisch höchstens zwei Nachfragen: die erste frühestens eine Stunde nach der letzten Zustellung, die zweite frühestens vier Stunden später. „Gesehen“ allein stoppt diese Folge nicht. Aufgabenerledigung, Löschung oder eine geänderte Fälligkeit beendet Nachfragen zum alten Termin. War Jarvis während einer Fälligkeit aus oder der Laptop im Schlafmodus, holt der lokale Worker die Meldung nach dem nächsten Start beziehungsweise Aufwachen nach. Eine geöffnete Oberfläche ist für die Anzeige nötig; es gibt keine Windows-Systembenachrichtigung und keine Mail- oder Messengerüberwachung.

## Lokaler Fokusmodus (J11b)

Unter „Aufgaben“ im Abschnitt „Fokusmodus“ den manuellen Fokus einschalten oder ein tägliches Fokusfenster mit Beginn, Ende und IANA-Zeitzone speichern. Ein Fenster von 22:00 bis 07:00 läuft über Mitternacht. Während des Fokus bleiben neue Hinweise zu offenen Aufgaben mit Priorität 4 oder 5 sofort sichtbar. Priorität 1 bis 3 erscheint nicht im globalen In-App-Hinweis, bleibt aber unter „Im Fokus gesammelt“ erreichbar. Der Fokusmodus liest keine Meldungen anderer Programme und verändert keine Windows-Benachrichtigungen.

Nach dem Ausschalten oder Ende des Zeitfensters zeigt der globale Hinweis die noch ungelesenen Meldungen mit einer Angabe, wie viele davon im Fokus gesammelt wurden. Unter „Aufgaben“ stehen sie gesammelt, bis sie als gesehen markiert, bestätigt, verschoben oder die Aufgabe erledigt wird. Ein Neustart löscht sie nicht. Eine Verschiebung beginnt nach der Wiederanzeige als neuer Hinweis; die alte Fokuszuordnung wird dabei entfernt. Wiederholte Herbststunden gelten beide als Teil eines passenden Fokusfensters, die im Frühjahr fehlende Stunde löst keine zusätzliche Zustellung aus. Manuelle Aktivierung gilt zusätzlich zum Zeitfenster; zum vollständigen Ausschalten beide Schalter deaktivieren.

## Öffentliche Webrecherche (J4a–J4j)

Unter „Recherche“ eine konkrete Frage eingeben und optional eine gezielte Suchanfrage. Ein Klick auf „Recherche starten“ überträgt **die Suchanfrage** an die öffentliche DuckDuckGo-HTML-Suche; wenn dort keine Treffer lesbar sind, an die Wikipedia-Suche. Ohne bezahlten Suchschlüssel werden höchstens fünf Treffer berücksichtigt, drei Seiten versucht und zwei öffentliche HTML-, Text- oder Text-PDF-Quellen gelesen. Jarvis speichert je Quelle bis zu 1500 Zeichen mit URL, Abrufzeit und bei PDFs der Anzahl gelesener Seiten. Es führt weder Seitenskripte aus noch öffnet es private Netzadressen oder unsichere Weiterleitungen.

Wenn du eine Quelle schon kennst, trage unter „Eigene öffentliche Quellenlinks“ **eine oder zwei vollständige HTTP(S)-URLs** ein, jeweils auf einer eigenen Zeile. Dann fragt Jarvis weder DuckDuckGo noch Wikipedia an; das Feld „Suchanfrage“ bleibt für diesen Auftrag unbenutzt. HTML-/Textseiten und Text-PDFs werden mit denselben öffentlichen Netz- und PDF-Grenzen gelesen. Der Bericht zeigt die tatsächlich erreichten Adressen, Abrufzeiten, Textausschnitte und eventuelle Fehler für einzelne Quellen. Ein lesbarer Link kann einen Bericht liefern, auch wenn der andere scheitert. Lokale/private Ziele, Zugangs-URLs, Fragmente und andere Protokolle sind gesperrt. Nach dem Prüfen kannst du einen neuen Auftrag mit anderen Links starten; Jarvis crawlt keine verlinkten Folgeseiten.

Für [G03](https://trello.com/c/3GlpHtQT) kannst du hier zusätzlich eine öffentliche YouTube-Video-URL (`https://www.youtube.com/watch?v=…` oder `https://youtu.be/…`) oder eine öffentliche GitHub-Repository-, Issue- oder Pull-Request-URL angeben. Die gepinnte [Agent-Reach-Version 1.6.1](https://github.com/Panniantong/Agent-Reach/releases/tag/v1.6.1) wird mit `python -m uv sync --project backend` installiert. Jarvis ruft ausschließlich die festen Leseaktionen `youtube read`/`youtube subtitle` beziehungsweise `github read` auf; die CLI erhält ein isoliertes temporäres Benutzerverzeichnis und keine API-Schlüssel. YouTube liefert Titel, Kanal, einen Beschreibungsausschnitt und verfügbare Untertitel. Wenn Untertitel fehlen oder der Abruf scheitert, zeigt der Bericht den Fehler sichtbar und kennzeichnet den verbleibenden Text als Metadaten/Beschreibung. GitHub liefert den öffentlich lesbaren Repository- oder Diskussionsinhalt. Der Bericht speichert kanonische URL, Typ, Abrufzeit und höchstens 1500 Textzeichen. Je Abruf gelten ein CLI-Zeitlimit von 60 Sekunden, ein Prozesslimit von 80 Sekunden und ein Ausgabelimit von 1 MB. Es werden keine Anmeldungen, Cookies oder kostenpflichtigen Transkriptionsdienste verwendet; ein Login für GitHub oder YouTube ist dafür nicht nötig. Inhalte und automatische Einordnung immer an der Originalquelle prüfen.

Optional unter „Lokaler Arbeitsstand“ ein bereits angelegtes Projekt auswählen. Jarvis übernimmt für genau diesen Bericht einen begrenzten Snapshot von Projektname, bestätigtem Stand, offenen Fragen und nächstem Schritt samt Version und Änderungszeit; späteres Bearbeiten oder Löschen des Arbeitsstands verändert den Bericht nicht. Dateien des Arbeitsstands müssen noch unverändert und freigegeben sein. Der Bericht kennzeichnet den manuell bestätigten Projekttext ausdrücklich als Kontext: Wörtliche öffentliche Belege stammen weiterhin nur aus gelesenen Webseiten/PDFs. Es werden keine Projektdatei-Inhalte oder lokalen Pfade an die Recherche angehängt. Projektkontext ist derzeit nur mit lokalem Ollama möglich; bei OpenRouter „Ohne Projektkontext“ wählen. Recherche legt keine Projektaufgabe an und führt keinen Projektschritt aus.

Wenn zwei Quellen lesbar sind und beide eine wörtlich geprüfte Fundstelle liefern, zeigt „Quellen nebeneinander“ die zwei Zitate mit Quelle, Typ und Abrufzeit. Darunter stehen bis zu acht aussagekräftige Wörter, die in beiden beziehungsweise nur in einem Zitat vorkommen. Das ist ein **Wortlautvergleich**: Unterschiedliche Wörter belegen weder einen sachlichen Widerspruch noch die Glaubwürdigkeit einer Quelle. Die kurze Modell-Einordnung kann Gemeinsamkeiten vorsichtig beschreiben; sie bleibt getrennt von den tatsächlichen Zitaten. Bei nur einer belegten Quelle erscheint keine Gegenüberstellung.

Falls eine Webseite ein ausdrücklich markiertes, plausibles Veröffentlichungsdatum als Metadatum angibt, zeigt Jarvis es zusätzlich zur Abrufzeit im Bericht, in der Zwei-Quellen-Ansicht und im Export. Die Kennzeichnung „laut Quelle (ungeprüft)“ bedeutet, dass Jarvis diese Angabe nicht unabhängig verifiziert. Fehlt sie oder ist sie ungültig beziehungsweise zukünftig, bleibt nur die Abrufzeit; bei Text-PDFs wird kein Veröffentlichungsdatum abgeleitet.

Bei zwei belegten Zitaten zeigt „Zahlen als Prüfhinweis“ einfache Zahlen, Prozentangaben und Einheiten aus beiden Fundstellen sowie Zahlen, die nur in einem Zitat stehen. Das ist ein Hinweis zum eigenen Nachprüfen: Unterschiedliche Zahlen können verschiedene Messgrößen oder Zeitpunkte betreffen. Jarvis stellt daraus keinen automatischen Widerspruch oder Faktenentscheid fest. Die gleiche Einordnung steht im Markdown-Export.

Für zwei gezielte Teilfragen zusätzlich „Zweite Suchfrage“ ausfüllen. Jarvis sucht zu jeder Frage nacheinander, versucht höchstens zwei Treffer pro Suche und übernimmt je eine lesbare Quelle; im Bericht und Export steht bei jeder Quelle die zugehörige Suchfrage samt Suchweg. Eine fehlgeschlagene Teilfrage erscheint als Fehler, während ein belegter Teilerfolg der anderen gespeichert bleibt. Der zweite Suchlauf ist mit direkten Quellenlinks nicht kombinierbar. Auch hier bleibt es bei höchstens zwei Quellen und einem Modellaufruf für die Antwort; der Recherchelauf entwickelt keine weiteren Suchfragen.

Mit lokalem Ollama kann „Teilfragen mit Ollama vorschlagen“ aus der eingegebenen Forschungsfrage zwei editierbare Suchtexte erzeugen. Dieser eigene, auf 45 Sekunden begrenzte Modellaufruf startet keine Websuche und überträgt keine Projektdateien. Erst nach eigener Prüfung und Klick auf „Recherche starten“ gehen die Suchtexte an öffentliche Suchdienste. Bei ungültiger oder fehlgeschlagener Antwort bleiben bestehende Eingaben erhalten. Die Funktion erscheint bei OpenRouter nicht.

Im ausgewählten Bericht lädt „Bericht als Markdown herunterladen“ eine UTF-8-Datei in den Browser-Downloadordner. Sie enthält Frage, Suchweg, Modell, optionalen Projekt-Snapshot, wörtliche Belege, Quellenvergleich, getrennte Schlussfolgerung, Unsicherheit, Fehler sowie Quellen-URLs und Abrufzeiten. Der Export löst keine neue Suche und keine Cloud-Übertragung aus. Prüfe den Modelltext und die verlinkten Quellen vor einer Weitergabe selbst.

Der Bericht zeigt zuerst wörtliche Fundstellen aus den gelesenen Seiten. Jarvis wählt sie anhand der Frage aus; das ist keine unabhängige Wahrheitsprüfung. Ollama ergänzt eine kurze, separat gekennzeichnete Einordnung. Falls das Modell nach 90 Sekunden nicht antwortet, bleiben die Fundstellen samt Hinweis trotzdem gespeichert. Berichte stehen nach einem Neustart wieder zur Verfügung und werden im lokalen Backup erfasst. Bei einem später eingestellten OpenRouter-Provider muss die Cloud-Übertragung für jeden Rechercheauftrag ausdrücklich aktiviert werden. Diese Version liest öffentliche Text-PDFs mit demselben URL-, DNS-, Weiterleitungs-, TLS- und 2-MB-Schutz wie Webseiten. Ein getrennter Prozess liest höchstens die ersten 12 von maximal 50 Seiten und endet nach spätestens 8 Sekunden. Entpackte PDF-Inhaltsströme sind zusätzlich auf 8 MB je Strom und die Parserarbeit auf ein begrenztes Budget beschränkt; ein kleines, stark komprimiertes PDF kann deshalb abgewiesen werden. Beschädigte, verschlüsselte und reine Scan-PDFs werden mit einem sichtbaren Fehler abgewiesen. OCR und mehrstufige Deep Research sind nicht enthalten. Suchmaschinen-Sperren können nicht umgangen werden. Das separate Werkzeug `browser_read` unter „Aktivität“ liest weiterhin nur HTML und Klartext.

Unter einem gespeicherten Recherchebericht kannst du „Bericht bewerten“ ausfüllen: Ergebnis, Quellenqualität und eine Begründung. Bei „Teilweise erreicht“ oder „Ziel verfehlt“ lässt sich mit einem erwarteten Ergebnis ein lokaler Fehler-Testfall sichern. Die Fälle stehen am Ende der Rechercheansicht und bleiben nach Neustart sowie Backup/Restore erhalten. Eine erneute Ausführung oder Modellanpassung erfolgt dadurch nicht automatisch. Gleichzeitige Änderungen an derselben Bewertung werden als Versionskonflikt gemeldet. „Testfälle als JSONL herunterladen“ speichert bewusst höchstens 100 aktuelle Fälle mit Bewertungsversion, ursprünglicher Frage, Modelltext und den damals gelesenen öffentlichen Quellenausschnitten. Jede Zeile ist ein eigenes JSON-Objekt mit `format_version: 1`, `evaluation` und `report`. Der gesonderte Projektarbeitsstand, interne lokale Pfade und App-Zugangsdaten werden nicht angehängt; frei eingegebener Frage- und Modelltext kann private Angaben enthalten und sollte vor einer Weitergabe geprüft werden. Der Knopf ist ohne Fälle deaktiviert; der Export führt weder Modellaufrufe noch Websuche aus.

Wenn du bei der Recherche einen lokalen Arbeitsstand gewählt hast, zeigt dessen Ansicht unter „Recherchen mit diesem Arbeitsstand“ die passenden gespeicherten Berichte mit Frage, Quellenzahl, Snapshot-Version und kurzer Einordnung. „Bericht öffnen“ führt zum vollständigen Bericht in „Recherche“. Andere Projekte und Berichte ohne bewusste Zuordnung erscheinen dort nicht. Die Liste ist auf die letzten 50 passenden Berichte begrenzt und startet keine neue Recherche. Nach dem Löschen eines Arbeitsstands bleiben die alten Berichte als Snapshots in „Recherche“ erhalten; die Projektansicht ist dann nicht mehr verfügbar.

## Projekt prüfen und nächsten Schritt vorbereiten (J5a)

Unter „Arbeitsstände“ einen Projektstand mit mindestens einer ausdrücklich ausgewählten, indexierten Datei und einem konkreten nächsten Schritt anlegen oder öffnen. „Status neu prüfen“ vergleicht die gespeicherten Datei-Hashes mit den aktuellen Dateien und zeigt offene, erledigte und gelöschte verknüpfte Aufgaben. Bei einer geänderten, ausgeschlossenen oder fehlenden Datei bleibt „Nächsten Schritt als Aufgabe anlegen“ gesperrt. Eine geänderte Datei zuerst unter „Dokumente“ erneut indexieren und nach eigener Prüfung im Arbeitsstand mit „Neu indexierte Datei bestätigen“ ausdrücklich übernehmen.

Der Klick auf „Nächsten Schritt als Aufgabe anlegen“ erzeugt lokal genau eine Aufgabe ohne Fälligkeit. Wiederholte Klicks, Neustarts und eine später erledigte oder gelöschte Aufgabe legen denselben Schritt nicht erneut an. Für eine neue Aufgabe den Text unter „Nächster Schritt“ bewusst ändern und speichern. Die Projektprüfung führt keine Arbeit selbstständig aus, synchronisiert Trello nicht und liest nur die bereits für Jarvis freigegebenen lokalen Dateien.

## Regelmäßige Projektchecks (J5b)

Einen Arbeitsstand öffnen, unter „Regelmäßige Projektprüfung“ ein Intervall von 1–30 Tagen, lokale Uhrzeit und IANA-Zeitzone wählen und „Prüfplan speichern“ klicken. Der nächste lokale Termin erscheint direkt darunter. „Projektprüfung pausieren“ und erneut speichern setzt den Zeitplan aus; beim Fortsetzen beginnt die Berechnung ab dem aktuellen Tag. „Jetzt als Bericht prüfen“ erzeugt unabhängig vom Zeitplan bei jedem bewussten Klick einen eigenen manuellen Bericht. Der Verlauf zeigt bestätigten Stand, offene Fragen, nächsten Schritt, geänderte oder fehlende Dateien, Aufgabenstatus und Blocker. Der Bericht führt keine Aufgabe aus und ändert keine Projektquelle.

Der Hintergrundworker prüft nur, während Jarvis läuft, ungefähr einmal pro Minute. Ein während einer ausgeschalteten App verpasster Termin wird beim nächsten Start als **ein** Bericht nachgeholt. Auch in der doppelten Herbststunde gibt es höchstens einen geplanten Bericht pro lokalem Kalendertag. Fehler erscheinen am Prüfplan und im Bericht; „Jetzt als Bericht prüfen“ erlaubt nach der Behebung einen erneuten Versuch. Zeitplan und Berichte liegen im lokalen SQLite-Profil und sind im Backup enthalten. Pausierte Tage werden nicht nachträglich abgearbeitet.

## Lokaler Zugangsdaten-Tresor (A79a)

Unter „Zugänge“ Anbieterkennung (klein geschrieben, zum Beispiel `openrouter`), Kontokennung, Geheimnistyp und Wert eingeben und „Geschützt speichern“ klicken. Der Wert ist nur während der Eingabe im Passwortfeld; nach Speichern oder Verlassen des Bereichs wird das Feld geleert. Die Liste zeigt ausschließlich Zuordnung, Version und ob der Wert für diesen Windows-Benutzer lesbar ist. „Ersetzen“ verlangt einen neuen Wert und erhöht die Version; „Entziehen…“ entfernt den Eintrag nach zweitem Klick lokal. Ein beim Anbieter bereits gültiger Token wird dadurch **nicht** beim Anbieter widerrufen.

Die verschlüsselten Werte liegen in `%LOCALAPPDATA%\JarvisLocal\vault_credentials.json` beziehungsweise im gewählten `JARVIS_DATA_DIR`, getrennt von Chat und Dokumentindex. Windows-DPAPI bindet sie an den aktuellen Windows-Benutzer. Das Jarvis-Backup enthält die Tresordatei **nicht**; nach Gerätewechsel oder Wiederherstellung Zugangsdaten neu eingeben. Dieser erste Tresorteil verbindet noch kein Konto und aktiviert OpenRouter nicht automatisch. Ohne Key/Credits weiterhin Ollama verwenden. Für den Test werden ausschließlich synthetische Werte genutzt.

## Prüfungen

Der öffentliche Quellstand wird bei Push und Pull Request in [Windows CI](.github/workflows/ci.yml) mit gesperrten Abhängigkeiten, Ruff, Backend- und Frontendtests, Produktionsbuild und dem Datei-Scan geprüft. Der Workflow verwendet Standard-Runner ohne OpenRouter-Key, lokale Daten oder Live-Ollama; echte Modell- und Edge-Klickpfade bleiben separate lokale Prüfungen.

Für einen eigenen öffentlichen Git-Push zuerst die Änderungen committen und `./scripts/push_public.ps1` in PowerShell ausführen. Der Helfer scannt den veröffentlichbaren Baum und alle von `HEAD` erreichbaren Commits nach Schlüsselmustern und privaten Dateinamen, prüft Git-Whitespace und pusht nur bei sauberem Arbeitsbaum `HEAD` nach `origin/main`. So fällt auch ein Secret auf, das in einem früheren Commit wieder gelöscht wurde. Mit `-CheckOnly` laufen die Prüfungen ohne Push bereits während der Bearbeitung. `.gitignore` schließt lokale `.env`-Varianten, Tresor-, Datenbank-, Schlüssel-, Log- und Backup-Dateien aus; `.env.example` ist die öffentliche Vorlage. Das Skript ersetzt keine bewusste Sichtung neuer Dateien vor einem Commit.

```powershell
python -m uv run --project backend pytest -q backend/tests
python -m uv run --project backend ruff check backend
pnpm --dir frontend check
pnpm --dir frontend test
pnpm --dir frontend build
```

Bei laufendem Jarvis und Ollama prüfen diese optionalen Skripte echte lokale Abläufe:

```powershell
python -m uv run --project backend python scripts/smoke_ollama.py
python -m uv run --project backend python scripts/smoke_draft_ollama.py
python -m uv run --project backend python scripts/smoke_approval.py
python -m uv run --project backend python scripts/smoke_plan.py
python -m uv run --project backend python scripts/smoke_desktop.py
python -m uv run --project backend python scripts/e2e_local.py
```

Die Windows-Auswahl und Hotkey-Konfliktmeldung lassen sich ohne laufenden Jarvis-Server prüfen. Für die UI-Übergabe und das Zeitmodell starten die weiteren Skripte jeweils einen eigenen isolierten Jarvis-Server und benötigen das lokale Ollama-Modell:

```powershell
python -m uv run --project backend python scripts/smoke_hotkey_selection.py
python -m uv run --project backend python scripts/smoke_hotkey_registration.py
python -m uv run --project backend python scripts/smoke_quick_capture_ui.py
python -m uv run --project backend python scripts/smoke_time_model_ui.py
python -m uv run --project backend python scripts/smoke_evening_plan_ui.py
python -m uv run --project backend python scripts/smoke_reminder_followup_ui.py
python -m uv run --project backend python scripts/smoke_extension_ui.py
python -m uv run --project backend python scripts/smoke_research_pdf_ui.py
python -m uv run --project backend python scripts/smoke_focus_ui.py
python -m uv run --project backend python scripts/smoke_research_ui.py
python -m uv run --project backend python scripts/smoke_project_check_ui.py
python -m uv run --project backend python scripts/smoke_project_review_ui.py
python -m uv run --project backend python scripts/smoke_vault_ui.py
python -m uv run --project backend python scripts/smoke_chat_archive_ui.py
python -m uv run --project backend python scripts/smoke_chat_suggestions_ui.py
python -m uv run --project backend python scripts/smoke_chat_compaction_ui.py
python -m uv run --project backend python scripts/smoke_chat_compaction_revision_ui.py
python -m uv run --project backend python scripts/smoke_chat_context_budget_ui.py
```

Die Modellproben prüfen den Ollama-Provider vor dem ersten Aufruf; `e2e_local.py` verwendet Ollama auch dann, wenn in der normalen `.env` später OpenRouter gewählt wurde.
Den echten Mehrschrittplan mit Rechnerstart, Schreibfreigabe und lokaler Notiz kannst du ohne laufenden Jarvis-Server in einem eigenen Testprofil prüfen. Rechner wird dabei kurz geöffnet:

```powershell
python -m uv run --project backend python scripts/smoke_plan_isolated.py
```

Das folgende Skript startet einen isolierten Backend-Prozess auf Port 8767, legt eine bald fällige Aufgabe an, beendet den Prozess vor der Fälligkeit abrupt und prüft nach zwei echten Neustarts genau eine nachgereichte Benachrichtigung. Es dauert ungefähr zwei Minuten und benötigt keinen OpenRouter-Key:

```powershell
python -m uv run --project backend python scripts/smoke_reminder_restart.py
```

Für J11a prüft eine zweite isolierte Probe auf demselben Port eine dringende Erinnerung nach abruptem Prozessabbruch, eine über zwei echte Neustarts genau einmal nachgereichte Nachfrage und das Ende der Folge nach Bestätigung. Die einstündige Nachfragefrist wird für diesen Test nach der Erstzustellung im gestoppten Testprofil beschleunigt. Die beiden Port-8767-Proben nacheinander ausführen:

```powershell
python -m uv run --project backend python scripts/smoke_followup_restart.py
python -m uv run --project backend python scripts/smoke_focus_restart.py
```

Für eine kurze Pünktlichkeitsprobe bei wachem Laptop startet `smoke_reminder_awake.py` denselben isolierten Port 8767. Das Skript prüft während des Wartens fortlaufend den Healthcheck und erwartet die einmalige In-App-Erinnerung spätestens 40 Sekunden nach Fälligkeit (Worker-Takt: 30 Sekunden). Es danach getrennt vom Neustarttest ausführen:

```powershell
python -m uv run --project backend python scripts/smoke_reminder_awake.py
```

Start und portgebundenen Stopp von Backend und UI prüft eine weitere isolierte Windows-Probe:

```powershell
python -m uv run --project backend python scripts/smoke_stop.py
```

`smoke_desktop.py` öffnet kurz Rechner und eine Testdatei in der Windows-Standardanwendung. Es nutzt außerdem `example.com` und `httpbin.org` für die Browserprobe.
`smoke_open_file.py` prüft die Dateiöffnung separat mit einem ausdrücklich unechten Schlüssel in der Testumgebung:

```powershell
python -m uv run --project backend python scripts/smoke_open_file.py
```

Den Chat-Abbruch prüft ein separater lokaler HTTP-Test mit einem absichtlich langsamen Fixture-Provider. Er braucht weder Ollama noch einen laufenden Jarvis-Server und kontrolliert sowohl eine gespeicherte Teilantwort als auch den Abbruch vor dem ersten Modelltext samt Kostenbeleg:

```powershell
python -m uv run --project backend python scripts/smoke_chat_cancel.py
```

Für einen isolierten Klicktest die laufende App zuerst mit `.\scripts\stop.ps1` stoppen. Danach startet das Skript Backend und UI mit einem temporären Datenprofil, klickt Quelle → Ollama-Entwurf → EML, Erinnerung, Aufgabe und Browserwerkzeug durch und wartet auf eine echte fällige In-App-Erinnerung. Es stoppt seine Prozesse am Ende selbst:

```powershell
python -m uv run --project backend python scripts/smoke_ui.py
```

Der getrennte Edge-Klicktest für Markdown benötigt weder Ollama noch einen API-Key. Er prüft Speichern, Suchindex, Rückgängig, Konflikt, Ausschluss und die mobile Ansicht mit einer temporären CRLF-Datei:

```powershell
python -m uv run --project backend python scripts/smoke_markdown_ui.py
```

Für Arbeitsstände startet ein weiterer isolierter Edge-Test Jarvis zweimal mit demselben temporären Profil. Er prüft ausgewählte Datei, Aufgabe und Link, Wiedereinstieg nach Neustart, Dateiveränderung und Verlust, Werkzeugaufrufe, Bearbeitung, Löschung und 390-Pixel-Ansicht. Die Werkzeugaufrufe werden in diesem UI-Test abgefangen; die echte Windows-Dateiöffnung wird separat durch `smoke_open_file.py` geprüft:

```powershell
python -m uv run --project backend python scripts/smoke_work_state_ui.py
python -m uv run --project backend python scripts/smoke_project_people_ui.py
```

## Backup und Wiederherstellung

Jarvis vor dem Backup stoppen. Das Backup enthält SQLite-Datenbanken einschließlich Arbeitsständen und Markdown-Vorversionen, Exporte und Jarvis-Notizen. Es enthält **keine** `.env`-Datei, API-Keys oder Browser-Cookies.

Für ein **passwortgeschütztes Backup** eine eigene, lange Passphrase wählen. Die CLI fragt sie ohne Anzeige im Terminal ab; sie erscheint weder in Befehlsargumenten noch im Archivnamen. Das Archiv wird mit Scrypt und AES-256-GCM in authentifizierten Blöcken verschlüsselt. Das Passwort sicher getrennt vom Archiv aufbewahren: Ohne es ist eine Wiederherstellung nicht möglich. Vor dem Restore das Zielarchiv zum Beispiel auf ein externes Laufwerk kopieren; eine Sicherung auf demselben Laptop schützt nicht vor Geräteverlust.

```powershell
.\scripts\stop.ps1
$backup = Join-Path $env:USERPROFILE 'Documents\JarvisBackups\jarvis-backup.jbackup'
python -m uv run --project backend python scripts/backup.py create-encrypted $backup
python -m uv run --project backend python scripts/backup.py restore-encrypted $backup --data-dir "C:\Pfad\JarvisWiederhergestellt"
```

Der Restore benötigt einen **neuen**, noch nicht vorhandenen Zielordner. Er entschlüsselt und authentifiziert zuerst alle Blöcke und prüft danach Manifest, SHA-256, sichere Archivpfade, SQLite und Exportbelege wie beim normalen Backup. Ein falsches Passwort oder verändertes bzw. unvollständiges Archiv hinterlässt keinen Zielordner. Bei normalem Abschluss entfernt Jarvis temporäre Klartext-ZIP-Dateien im System-Tempordner; nach einem abrupten Prozess- oder Geräteabsturz können temporäre Klartextdateien dort zurückbleiben. Der System-Tempordner sollte deshalb auf einem geschützten lokalen Laufwerk liegen.

### Tägliche verschlüsselte Sicherung

Jarvis **stoppen**, dann einen Zielordner außerhalb von `%LOCALAPPDATA%\JarvisLocal` und die gewünschte lokale Uhrzeit festlegen:

```powershell
.\scripts\stop.ps1
$backupDir = Join-Path $env:USERPROFILE 'Documents\JarvisBackups'
python -m uv run --project backend python scripts/backup_schedule.py configure --directory $backupDir --time 03:00 --timezone Europe/Paris --keep 7
python scripts/dev.py
```

Die CLI fragt eine mindestens zwölf Zeichen lange Passphrase zweimal ohne Anzeige ab. Nur eine mit **Windows-DPAPI für diesen Benutzer auf diesem Laptop geschützte** Fassung liegt in `%LOCALAPPDATA%\JarvisLocal\backup_schedule.json`; sie ist nicht im eigentlichen Backup enthalten. Die `.jbackup`-Archive bleiben mit der gemerkten Passphrase auch auf einem anderen Gerät wiederherstellbar. Nach Wiederherstellung auf einem anderen Gerät muss der Zeitplan neu eingerichtet werden.

Solange Jarvis läuft, prüft ein lokaler Worker einmal pro Minute den Termin. Er sichert höchstens einmal pro lokalem Kalendertag; eine fehlende Winterzeitstunde wird danach nachgeholt, die doppelte Stunde löst nur einmal aus. War Jarvis zur Uhrzeit aus, holt es den Lauf beim nächsten Start **am selben Tag** nach. Ein erzeugtes Archiv wird vor Erfolgsmeldung in ein temporäres Profil zurückgespielt und geprüft. Danach bleiben höchstens die gewählten Anzahl automatisch erzeugter `jarvis-auto-*.jbackup`-Dateien im Zielordner; manuelle Archive werden nicht gelöscht. Der Status, der letzte Erfolg und Fehler stehen in **Übersicht → Geplante verschlüsselte Backups**. Dort kann auch „Jetzt sichern und prüfen“ bewusst gestartet werden. Der Healthcheck zeigt einen fehlgeschlagenen Lauf als gestörten Dienst.

Status lesen oder den Zeitplan deaktivieren (für das Deaktivieren Jarvis zuerst stoppen):

```powershell
python -m uv run --project backend python scripts/backup_schedule.py status
.\scripts\stop.ps1
python -m uv run --project backend python scripts/backup_schedule.py disable
```

Die Planung ist ein Worker der laufenden Laptop-App und keine Windows-Aufgabe. Bei ausgeschaltetem Laptop laufen keine Backups; für Schutz vor Geräteverlust die verschlüsselten Archive zusätzlich auf ein externes Laufwerk kopieren. Der Zielordner muss beim Lauf vorhanden und beschreibbar sein. Nach einem fehlgeschlagenen Lauf bleibt der Fehler sichtbar; nach Korrektur kann „Jetzt sichern und prüfen“ erneut genutzt werden.

Das bisherige **unverschlüsselte** ZIP-Format bleibt für lokale Tests und ältere Archive verfügbar:

```powershell
python -m uv run --project backend python scripts/backup.py create "C:\Pfad\jarvis-backup.zip"
```

Wiederherstellung benötigt einen neuen, noch nicht vorhandenen Zielordner:

```powershell
python -m uv run --project backend python scripts/backup.py restore "C:\Pfad\jarvis-backup.zip" --data-dir "C:\Pfad\JarvisWiederhergestellt"
```

Danach in `.env` `JARVIS_DATA_DIR=C:/Pfad/JarvisWiederhergestellt` setzen und Jarvis starten. Bereits beim Erstellen prüft Jarvis die SQLite-Momentaufnahmen und gleicht gespeicherte Exporte mit ihren Hashes ab; verschachtelte Links/Junctions in Exporten oder Notizen brechen das Backup mit einer Fehlermeldung ab, und höchstens 2.000 Verzeichnisse werden besucht. Die Wiederherstellung prüft Dateiliste, sichere Archivpfade einschließlich Windows-Namenskollisionen, SHA-256-Prüfsummen, SQLite-Integrität und Schema-Version. Exportpfade in der Datenbank zeigen danach auf den neuen Zielordner. Unterstützte ältere Schemata werden beim ersten Start migriert. Freigegebene Originalordner und Quelldateien liegen außerhalb des Backups und müssen am neuen Ort weiterhin verfügbar sein. Das Browserprofil wird bei Bedarf neu angelegt.

## OpenRouter bewusst einrichten

Für einen bewussten Cloud-Betrieb in `.env` `JARVIS_PROVIDER=openrouter` und eine gültige `JARVIS_OPENROUTER_MODEL`-ID eintragen. Ein Live-Test verwendete `openai/gpt-4o-mini`; Modellverfügbarkeit und Preise werden vor jeder Anfrage erneut geprüft. Den **Schlüssel nicht in `.env` speichern**. Es gibt zwei lokale Wege:

1. **Windows-Tresor:** Jarvis mit Ollama starten, unter „Zugänge“ „OpenRouter-Eintrag vorbereiten“ wählen, den Schlüssel im Passwortfeld eingeben und geschützt speichern. Der Eintrag lautet `openrouter` / `default` / `api_key`. Danach unter „Übersicht“ Provider und Modell bewusst umstellen oder die beiden Werte in `.env` setzen und Jarvis neu starten. Der Schlüssel wird bei jedem neuen Modellaufruf aus dem DPAPI-Tresor des aktuellen Windows-Benutzers gelesen; Ersetzen und lokales Entziehen wirken für folgende Aufrufe. Der Tresor wird nicht ins normale Backup aufgenommen und muss nach Geräte- oder Benutzerwechsel neu eingerichtet werden.
2. **Nur für das Startterminal:** Schlüssel als Prozessvariable setzen und Jarvis starten:

```powershell
$env:JARVIS_OPENROUTER_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'OpenRouter-Key' -AsSecureString)).Password
python scripts/dev.py
```

Nach dem Schließen dieses Terminals ist die Prozessvariable weg. Falls sie gesetzt ist, hat sie Vorrang vor dem Tresoreintrag. Das Startskript reicht sie nur an das Backend weiter, nicht an Vite. Von Jarvis gestartete Desktopprogramme, der Dateizuordnungs-Hilfsprozess und Edge erhalten eine Umgebung ohne den Schlüssel. Den Key weder in Chat noch im Repository ablegen. Jarvis prüft das Modell und dessen aktuelle Preise vor einer bezahlten Anfrage und reserviert konservativ Budget. Der Adapter setzt 180 Sekunden SDK-Anfrage-Timeout und deaktiviert automatische Wiederholungen. Die Variante `:online` ist wegen zusätzlicher, hier nicht budgetierter Websuchkosten gesperrt. Der isolierte [R02-Live-Test](scripts/smoke_openrouter_live.py) bestand mit gestreamtem Chat, Provider-Kostenbeleg, Reload, Cloud-Einwilligung und Sperre einer `local_only`-Fundstelle. Der [Edge-Cloud-Klicktest](scripts/smoke_openrouter_ui.py) prüfte den Freigabeschalter, Antwort, Kostenbeleg und Reload; mit `--vault` prüft er den verschlüsselten Schlüsselpfad ohne Prozessvariable und das Entziehen. Zum bewussten Wiederholen `python -m uv run --project backend python scripts/smoke_openrouter_live.py` oder `python -m uv run --project backend python scripts/smoke_openrouter_ui.py --vault` ausführen und den Schlüssel am verdeckten Prompt eingeben; jeder Test verursacht eine kleine Modellgebühr und löscht sein temporäres Profil danach. Die normale lokale `.env` bleibt bei Ollama.

## Begrenzte Nachrecherche

Öffne unter „Recherche“ einen gespeicherten Bericht. Trage bei „Offenen Punkt nachrecherchieren“ eine konkrete offene Frage und eine bearbeitbare Suchfrage ein. Erst „Nachrecherche starten“ löst die öffentliche Suche aus. Jarvis versucht höchstens drei Treffer, übernimmt höchstens eine neue lesbare Quelle und fragt höchstens einmal das lokale Ollama-Modell. Der neue Bericht verweist auf den Ursprung und lässt ihn unverändert. Such- oder Modellfehler bleiben als eigener Teilbericht sichtbar. „Ursprungsbericht öffnen“ führt zurück; Herkunft und offener Punkt stehen auch im Markdown-Export. Die Funktion setzt lokales Ollama voraus und startet keinen weiteren Suchlauf von selbst.

Unter „Nachrechercheverlauf“ zeigt ein geöffneter Bericht seine bis zu 50 neuesten direkten Nachläufe mit offenem Punkt, Suchfrage, Erstellzeit, Quellenzahl, Modellantwort und Fehlerhinweis. „Nachlauf öffnen“ führt zum vollständigen gespeicherten Bericht. Ein Nachlauf kann selbst wieder einen direkten Nachlauf haben; die Liste zeigt nur die jeweilige nächste Ebene. Die Anzeige startet weder Websuche noch Modell.

## Gegliederter Essay mit lokalem Ollama

Unter „Entwürfe“ zuerst ein allgemeines oder eigenes Essay-Stilprofil speichern oder eine eigene Schreibprobe auswählen. Als Entwurfstyp „Essay“ wählen, einen Auftrag formulieren, ein Wortziel von 300–1200 eingeben und 2–6 verschiedene Gliederungspunkte zeilenweise notieren. Über 600/750/900/1050 Wörter sind mindestens 3/4/5/6 Punkte nötig; bis 900 Wörter sind höchstens vier Punkte erlaubt. Ausgewählte lokale Dokumentenfundstellen werden wie bei anderen Entwürfen verwendet. „Entwurf erstellen“ ruft für jeden Punkt einmal das lokale Ollama-Modell auf. Bis 900 Wörter sind insgesamt höchstens vier Aufrufe mit je maximal 360 Sekunden möglich; darüber höchstens acht mit je maximal 480 Sekunden. Auf langsameren Rechnern können lange Essays deutlich länger dauern. Es wird nichts automatisch veröffentlicht.

Bleibt der Text nach allen Gliederungspunkten unter 80 % des Wortziels, verwendet Jarvis höchstens die bis zur jeweiligen Gesamtgrenze übrigen lokalen Ollama-Anfragen für zusätzliche Absätze. Die Erstfassung jedes Abschnitts bleibt erhalten. Der Entwurf nennt die Gesamtzahl der versuchten Modellaufrufe, mögliche Ergänzungen, die tatsächliche Wortzahl und einen möglichen Fehler. Auch nach einer Ergänzung ist das Wortziel ein Richtwert; ein langes Ziel kann trotz aller Aufrufe unter dem Ziel bleiben.

Jarvis speichert den Essay mit Wortziel, tatsächlicher Wortzahl, Gliederung, verwendeten Quellen, Stilprofil-Snapshot und Entwurfsregel-Version. Ein später fehlgeschlagener Abschnitt lässt den bereits erzeugten Teil als gekennzeichneten, bearbeitbaren Entwurf stehen. Den Text und alle Quellen vor Nutzung prüfen; das Wortziel ist ein Richtwert, und persönliche Stiltreue hängt von den eigenen Proben und späterem Feedback ab. Wie andere Entwürfe lässt er sich bearbeiten und als TXT oder Markdown exportieren. Essay-Erzeugung ist vorläufig nur mit Ollama möglich.

Nach dem Bearbeiten eines Essays kann unter „Stilkorrektur für spätere Essays“ ein eigener konkreter Hinweis bewusst gespeichert werden. Ungespeicherte Textänderungen zuerst speichern. Jarvis zeigt alle aktiven Hinweise unter „Bestätigte Essay-Stilhinweise“ und verwendet höchstens die fünf neuesten für künftige **lokale** Essays. Die Liste lässt sich einzeln löschen; ein älterer Entwurf behält seinen Snapshot der damals verwendeten Hinweise. Jarvis übernimmt keinen erzeugten Modelltext automatisch als Schreibprobe. E-Mail-, Text- und Antwortentwürfe verwenden diese Essay-Hinweise nicht; sie werden auch nicht an OpenRouter gesendet.

Eigene TXT-/Markdown-Dateien können als lokale Schreibprobe übernommen werden: Unter „Dokumente“ den Ordner ausdrücklich freigeben und indexieren, dann unter „Entwürfe“ bei „Eigene Datei als Schreibprobe übernehmen“ genau eine Datei und einen Titel auswählen. Das Feld „Ich habe diesen Text selbst verfasst“ muss bewusst bestätigt werden. Jarvis vergleicht Datei, Indexstand und SHA-256 erneut; geänderte Dateien zunächst neu indexieren. Gültig sind UTF-8-Texte bis 80 KB und höchstens 20.000 Zeichen. Die übernommene Probe wird automatisch für den nächsten lokalen Entwurf ausgewählt, sofern noch weniger als fünf Proben ausgewählt sind. Die Originaldatei bleibt unverändert; die gespeicherte Probe zeigt Pfad und Hash und bleibt auch nach späterer Dateiänderung erhalten. Der Import vergibt immer „nur lokal“ und erlaubt keine Cloud-Nutzung dieser Probe.

Für einen Stilprofil-Vorschlag unter „Entwürfe“ 1–3 eigene Schreibproben ankreuzen, die eigene Urheberschaft ausdrücklich bestätigen und „Stilprofil vorschlagen“ wählen. Nur lokales Ollama sieht höchstens die ersten 1800 Zeichen je Probe und erzeugt in einem Aufruf mit höchstens 384 Ausgabetokens einen Vorschlag. Die Vorschau zeigt Titel und SHA-256 der verwendeten gespeicherten Proben und lässt sich bearbeiten. „Ins Stilprofil übernehmen“ kopiert den Text in das bearbeitbare Profil und setzt die Freigabe auf „Nur lokal“. Erst „Profil speichern“ sichert ihn dauerhaft. Modellfehler und das bloße Ansehen der Vorschau verändern das gespeicherte Profil nicht. Die Stiltreue mit eigenen Texten bitte selbst beurteilen.

Unter „Stilprofil je Textsorte“ kann für E-Mail, Text, belegte Antwort und Essay jeweils eine eigene Vorgabe mit eigener lokaler oder Cloud-Freigabe gespeichert werden. Die Auswahl der Textsorte, der Text und die Freigabe bleiben beim Wechsel in einen anderen Bereich innerhalb der geöffneten Oberfläche erhalten, auch vor dem Speichern. Ohne eigene Vorgabe gilt das **gespeicherte allgemeine Stilprofil**. „Ausnahme entfernen“ stellt diesen Rückfall wieder her; frühere Entwürfe behalten ihren tatsächlich verwendeten Stiltext. Eine ungespeicherte Änderung fließt noch nicht in neue Entwürfe ein. Beim Cloud-Provider darf der für die Textsorte wirksame Stiltext nur mit passender Cloud-Freigabe verwendet werden.

Unter beiden Profilformularen zeigt „Fassungen und Rückkehr“ alle gespeicherten Fassungen mit Zeitpunkt, Text und damaliger Cloud-Freigabe. Ein ausdrücklich gespeicherter geänderter Text oder eine geänderte Freigabe legt eine neue Fassung an; identisches erneutes Speichern tut das nicht. Auch das Entfernen einer Textsortenausnahme erscheint als Fassung mit allgemeinem Rückfall. „Fassung … wiederherstellen“ legt eine neue Fassung mit dem früheren Text **und dessen Freigabe** an. Ungespeicherte Eingaben vorher speichern oder mit „Profil neu laden“ beziehungsweise „Textsorte neu laden“ bewusst verwerfen. Wenn ein zweites Fenster das Profil inzwischen verändert hat, wird der veraltete Speicherversuch mit Konflikt abgewiesen; neu laden und die eigene Änderung erneut prüfen. Bereits erzeugte Entwürfe behalten ihren Stil-Snapshot. Jarvis übernimmt Korrekturen oder Modelltext nicht automatisch als neues Profil.

### Persönliches Schreibprofil verwenden

Unter „Entwürfe“ eigene, selbst verfasste Schreibproben anlegen oder aus einem freigegebenen TXT-/Markdown-Dokument übernehmen. Optional mit lokalem Ollama „Stilprofil vorschlagen“ wählen, den Vorschlag prüfen und in das persönliche Profil übernehmen. Dort nur tatsächlich passende Merkmale des eigenen Stils belassen und speichern. Das Profil ist zunächst **nur lokal** freigegeben. Beim neuen Entwurf unter „Schreibprofil“ ausdrücklich das persönliche Profil wählen; das funktioniert für E-Mail, Text, belegte Antwort und Essay. Die angekreuzten Schreibproben gehen zusätzlich in die jeweilige Modellanfrage ein. Ohne Profilwahl gelten das allgemeine beziehungsweise das Textsortenprofil. Ungespeicherte Profiländerungen gelten noch nicht für neue Entwürfe. Die Entwurfsansicht zeigt den verwendeten Namen und die Fassung; ältere Entwürfe behalten deren Stiltext auch nach einer Änderung. Ein Konflikt mit einer Änderung aus einem zweiten Fenster wird abgewiesen; die Schaltfläche „Neu laden“ lädt die aktuelle Fassung. Den Anzeigenamen unter „Einstellungen“ ändern; Standard ist „Mein Stil“. Die interne Profilkennung `Maxim` bleibt für bestehende Daten und API-Aufrufe erhalten.

Nach der Erzeugung bietet ein Entwurf mit persönlichem Profil eine Stilüberarbeitung an. Ollama erzeugt lokal einen **Vorschlag**, der zunächst nur als Vorschau erscheint. Jarvis lehnt eine Vorschau ab, wenn Zahlen, Daten, URLs, E-Mail-Adressen, Quellenmarken oder wörtliche Zitate hinzugefügt, entfernt oder geändert wurden. Die übrigen Fakten und den Stil selbst vergleichen. „In Editor übernehmen“ füllt den bearbeitbaren Entwurf; erst „Änderung speichern“ sichert ihn. Überarbeitung ist auf 8000 Entwurfszeichen beschränkt und für bereits exportierte Entwürfe nicht verfügbar.

„Schreibmuster prüfen“ untersucht den gerade sichtbaren Entwurf einschließlich ungespeicherter Änderungen **lokal und ohne Modellaufruf**. Jarvis zeigt konkrete deutsche Einleitungs-, Kontext-, Hinweis- und Schlussfloskeln mit Textausschnitt und Änderungsidee. Wörtliche Zitate werden ausgelassen. Nach einer Textänderung verschwinden die alten Treffer bis zur nächsten Prüfung. Das sind redaktionelle Hinweise, keine KI-Erkennung, keine Bewertung der Autorschaft und keine Garantie für eine bestimmte Detektorentscheidung. Die Regelidee wurde eigenständig auf Deutsch umgesetzt und ist von [blader/humanizer](https://github.com/blader/humanizer) (MIT-Lizenz) inspiriert. Dessen README erklärt selbst, dass Detektoren die meisten Ausgaben weiterhin markieren. Es wird kein fremder Quelltext eingebunden und kein Text an dieses Projekt gesendet.

Für diesen Ablauf ist **kein OpenRouter-Key** nötig: `.env` mit `JARVIS_PROVIDER=ollama` belassen, `ollama pull qwen3:4b-instruct` ausführen, Ollama starten und im Repository `python scripts/dev.py` aufrufen. Danach [http://127.0.0.1:5173](http://127.0.0.1:5173) öffnen. Die unabhängige lokale Stilfunktion verwendet keinen Code oder Dienst von Turnitout. Sie verbessert die Anpassung an eigene Beispiele; sie kann weder perfekte Stiltreue noch eine bestimmte Bewertung durch KI-Detektoren zusichern. Fakten, Zahlen, Zitate und Quellen vor Verwendung selbst prüfen.

## Grenzen und Status

Kein verbundenes Postfach, kein verbundener Kalender, kein Versand, keine OCR für gescannte PDFs, keine allgemeine Browsersteuerung und kein Fernzugriff. Das bewusste Lesen öffentlicher JavaScript-Seiten ist über `browser_dynamic_read` verfügbar. Die Mehrschrittplanung verwendet nur die registrierten Werkzeuge und verarbeitet Ergebnisse nicht automatisch zu neuen Schritten; der Plan wird vor Ausführung festgelegt. Die Nachweise pro Trello-Karte stehen in [docs/ABNAHME.md](docs/ABNAHME.md); aktueller Teststand, offene Punkte und nächster Schritt in [docs/STATUS.md](docs/STATUS.md).

## Lizenz

Der Jarvis-Quellcode steht unter der [MIT-Lizenz](LICENSE). Optional installierte Dienste und Modellgewichte haben eigene Lizenzen und müssen getrennt geprüft werden.

## Vor einem öffentlichen Push

`.env`, Datenbank, Exporte und lokale Schlüsseldateien sind ausgeschlossen. Der oben beschriebene Push-Helfer prüft auch die erreichbare Commit-Historie; `python scripts/audit_public.py --check-stdin-secret` erlaubt zusätzlich einen verdeckten Vergleich mit einem bekannten Schlüssel. Vor dem Commit neue Dateien und Bilder selbst ansehen. Neue Funktionen mit `python -m uv run --project backend pytest backend/tests -q`, `pnpm --dir frontend test` und `pnpm --dir frontend build` prüfen.
