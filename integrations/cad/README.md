# G07 · Lokaler CAD-Halter in Jarvis

Der erste Prüfgegenstand ist ein rechteckiger Halter mit zwei Bohrungen. Die Maße und die Modellversion stehen in [holder.py](holder.py). Das Modell nutzt `build123d` über die lokale, auf `cad-server==0.10.3` festgelegte [text-to-cad-Runtime](https://github.com/earthtojake/text-to-cad). Jarvis stellt dafür `cad_holder_build` und `cad_holder_export` bereit.

## Jarvis starten und benutzen

Im Repository-Ordner einmalig die CAD-Laufzeit bereitstellen und ihre Telemetrie ausschalten:

```powershell
python -m uv tool run --no-config --managed-python --python 3.14 --from 'cad-server==0.10.3' cad telemetry off
```

Dann Jarvis starten:

```powershell
python scripts/dev.py
```

[Die lokale Oberfläche](http://127.0.0.1:5173) öffnen. Unter **Aktivität → Werkzeug ausführen** `cad_holder_build` auswählen. Eine Beschreibung mit allen fünf Maßen eingeben, beispielsweise: „Halter 60 mm breit, 30 mm tief, 6 mm dick, zwei Bohrungen mit 5 mm Durchmesser und 40 mm Abstand der Mittelpunkte.“ Mit **Maße aus Beschreibung vorschlagen** kann lokales Ollama die ausdrücklich genannten Werte in die editierbaren Felder übernehmen. Die Felder prüfen und **CAD-Vorschau erstellen** klicken. Der Bau kann beim ersten Mal wegen Python-3.14-/CAD-Installation einige Minuten dauern. Nach dem Bau zeigt der Auftrag die Maße, die PNG-Vorschau, Modell-ID und Skript-SHA-256.

Erst nach Sichtprüfung **STEP exportieren** oder **STL exportieren** klicken. Der Exportauftrag wartet auf **Freigeben**; ohne Freigabe entsteht keine Exportdatei. Nach der unabhängigen Geometrieprüfung erscheint **Freigegebene CAD-Datei herunterladen**. STEP und STL brauchen je einen eigenen Auftrag. Die Dateien liegen im Jarvis-Datenordner unter `cad-models/<Modell-ID>/`; Vorschau und Export werden im normalen Backup gesichert, temporäre Engine-Dateien nicht. Zum Stoppen `Ctrl+C` im Startterminal oder bei hängenden Prozessen `.\scripts\stop.ps1` ausführen.

Das Modell generiert keinen frei ausführbaren Python-Code: Jarvis setzt nur fünf validierte Zahlen in das geprüfte, versionierte build123d-Skript. Die Beschreibung ist ein Herkunftsbeleg. Der Ollama-Vorschlag darf fehlende Maße nicht ergänzen, und man kann ihn vor dem Bau korrigieren. Bei jedem CAD-Auftrag läuft ein eigener lokaler Server mit einem Worker, zufälligem Loopback-Port und 260-Sekunden-Grenze. „Stoppen“ beendet seinen Prozessbaum. Es gibt keinen Drucker- oder Maschinenbefehl und keinen OpenRouter-Bedarf.

## Lokaler Lauf unter Windows

```powershell
$env:CAD_TELEMETRY = '0'
$env:CAD_UPDATE_CHECK = '0'
$env:DO_NOT_TRACK = '1'
python -m uv tool run --no-config --managed-python --python 3.14 --from 'cad-server==0.10.3' cad telemetry off
python -m uv tool run --no-config --managed-python --python 3.14 --from 'cad-server==0.10.3' cad run integrations/cad/holder.py
```

Der erste Lauf lädt Python 3.14 und den CAD-Kern. `cad telemetry off` speichert die private Einstellung. Die drei Umgebungsvariablen sperren zusätzlich Telemetrie und die stündliche Versionsabfrage für diesen Lauf. `cad run` baut nur das Modell; STEP/STL werden erst mit einer gesonderten, bewusst ausgelösten Exportaktion geschrieben. Der erste Prüflauf erzeugte [STEP](../../artifacts/cad/holder-v1.step), [STL](../../artifacts/cad/holder-v1.stl) und eine [Vorschau des erneut geöffneten STL](../../artifacts/cad/holder-v1-stl.png). [verify_holder.py](verify_holder.py) importiert STEP unabhängig vom Modellskript und prüft 60 × 30 × 6 mm, 10.564,38 mm³ sowie zwei Bohrungsachsen bei −20 und +20 mm mit Radius 2,5 mm. Es liest außerdem alle 228 STL-Dreiecke und prüft deren Hüllmaße. Es gibt keine Drucker- oder Maschinensteuerung.

[test_invalid_model.py](test_invalid_model.py) baut einen zweiten Halter mit ungültigem Bohrungsabstand. Der CAD-Lauf endet mit einem verständlichen Fehler; die SHA-256-Prüfsummen der früheren STEP-/STL-Dateien bleiben gleich.

Die [text-to-cad-Dokumentation](https://github.com/earthtojake/text-to-cad/blob/main/skills/cad/SKILL.md) erklärt, dass ein durch Shell-Timeout beendeter `cad run`-Client den laufenden Server-Bau nicht automatisch stoppt. [test_timeout_engine.py](test_timeout_engine.py) startet deshalb einen eigenen Server mit getrenntem `CAD_ENGINE_DIR`/`CAD_STATE_DIR`, einem Worker und zufälligem lokalen Port. Ein 120-Sekunden-Modell wurde während „Building geometry“ samt isoliertem Serverprozessbaum in **3,51 Sekunden** beendet; der normale CAD-Server blieb unter derselben PID erreichbar und STEP/STL blieben unverändert. Der Test legt seinen temporären Ordner geprüft unter `data/` an und bereinigt ihn anschließend.

Die Anwendung prüft STEP nach erneutem Öffnen auf Hüllmaße, Volumen und zwei zylindrische Bohrungen. STL wird als binäres Dreiecksnetz erneut gelesen und auf vollständige Struktur und Hüllmaße geprüft. Nur nach dieser Prüfung wird die angeforderte Datei unter der Modell-ID gespeichert. Ein bereits freigegebener Export wird nicht überschrieben.
