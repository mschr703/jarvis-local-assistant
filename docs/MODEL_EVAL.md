# Lokale Gesprächsqualität und Tempo

Stand: 20.11.2026, P09 in Arbeit. Alle folgenden Fragen wurden mit Ollama auf
dem Windows-Laptop mit 16 GB RAM und CPU-Inferenz ausgeführt. Die Zeiten sind
Einzelläufe; Modellladung, Windows-Last und die Länge der Antwort schwanken.
Sie sind keine zugesicherte Antwortzeit. Es wurden synthetische Projekt- und
Zeitdaten in einem temporären Profil verwendet. Keine Mail- oder Kalenderkonten
und kein Cloudmodell waren beteiligt.

## Werkzeugwahl und Kontext

| Fall | `qwen3:1.7b` | `qwen3:4b-instruct` | Beobachtung |
| --- | ---: | ---: | --- |
| Projektquelle wählen, synthetischer Planer | 15,7 s | 45,8 s | Beide wählten die Projektquelle. |
| Freies 20-Minuten-Fenster wählen, synthetischer Planer | 23,3 s | 67,7 s | Beide wählten das Zeitwerkzeug. |
| Echte Projektfrage mit kleinem Planer | 20,1 s Planer, 209,1 s gesamt | – | Quelle und Antwort korrekt; Modellwechsel und Antwort dauern zusätzlich. |
| Echte Zeitfrage mit kleinem Planer | 23,3 s Planer, 56,5 s gesamt | – | Richtige Quelle und berechneter Start in der Antwort. |
| Klare Projektrückfrage mit normalem Planer | – | 152,8 s bis Timeout, 265,3 s gesamt | Aktuelle Quelle rettete die Antwort; der zweite Planeraufruf war unnötig. |
| Dieselbe Rückfrage mit direktem Quellen-Refresh | – | 16,4 s gesamt | Warmes Modell, keine neue Werkzeugwahl; konkreter nächster Schritt korrekt. |

Die Option `JARVIS_OLLAMA_PLANNER_MODEL=qwen3:1.7b` betrifft nur die Wahl von
lokalen Lesewerkzeugen. Das Antwortmodell bleibt über
`JARVIS_OLLAMA_MODEL` bzw. die Chat-Modellregel steuerbar. Ein fehlendes
optionales Planermodell löst einmal einen lokalen Rückfall auf das normale
Ollama-Modell aus; es gibt keinen Cloud-Rückfall. Der kleinere Planer traf in
einer Folgefrage auch eine ungeeignete allgemeine Aufgabenliste. Deshalb
aktualisiert Jarvis bei eindeutigen Rückbezügen die zuletzt erfolgreiche Quelle
mit den geprüften ursprünglichen Argumenten. Wenn die Frage eine neue Datenart
oder ein neu benanntes Thema nennt, findet eine neue Auswahl statt.

Ein vollständiger sechs Fragen umfassender Dialog mit dem bisherigen 4B-Modell
brauchte pro Frage 253,1 / 124,9 / 79,2 / 260,3 / 352,2 / 72,8 Sekunden.
Alle geforderten Quellen, Schritte und Rechenergebnisse stimmten. Die vierte
Frage nutzte damals noch den Planer für `tools: []` (134,6 Sekunden davon);
die reine Begriffsfrage überspringt ihn im aktuellen Code. Die erste und fünfte
Frage blieben echte Werkzeugwahlen. Die Zeiten enthalten Modellladung und
Antwortgenerierung und sind nicht mit dem isolierten Planerbenchmark identisch.

## Antwortqualität

| Fall | Ergebnis mit `qwen3:4b-instruct` |
| --- | --- |
| Projekt → nächster Schritt → offener Punkt | Bericht bis Freitag lesen und „Rückmeldung offen“ nach strukturierter Übergabe der Werkzeugdaten korrekt getrennt. Keine Zuständigkeit aus dem bloßen Status ableiten. |
| Allgemeine Zeitrechnung mit 10 Minuten Puffer | 13:30 korrekt. |
| Nutzerkorrektur auf 15 Minuten Puffer | 13:25 korrekt im selben Gespräch. |
| Neues Thema „Antwort offen“ | Zuständigkeit als unklar erkannt. |
| Hypothetische 14:00/20-Minuten-Rechnung nach persönlicher Zeitfrage | 13:40 ohne Werkzeug korrekt. Eine experimentelle allgemeine Begriffsregel führte zunächst zu dem falschen Zusatz „keine Annahmen“; nach Rücknahme antwortete der isolierte Retest knapp und korrekt. |
| Annahme, Tatsache und eigener Pufferwunsch | **Noch nicht bestanden:** Das 4B-Modell klassifiziert „Ich will 15 Minuten Puffer“ wiederholt fälschlich als Annahme. Der Pufferwunsch ist eine Präferenz oder Planung; die angenommene Fahrtzeit ist ohne Messung keine bestätigte Tatsache. Eine allgemeine Antwortregel allein beseitigte den Fehler nicht. |

`qwen3.5:4b` ordnete den Wunsch im isolierten Begriffstest eher richtig ein,
formulierte aber unsauber; in einem separaten Projektfall behauptete es, der
Bericht sei bereits erstellt, obwohl die Quelle nur „Bericht lesen“ sagte.
`qwen3:8b` scheiterte am selben Puffer-Begriffstest ebenfalls und war unter
gleichzeitiger Backend-Testlast deutlich langsamer. Beide Modelle wurden
deshalb nicht als neuer Standard übernommen. Die Standardeinstellung bleibt
`qwen3:4b-instruct`, und der Begriffstest ist als offener Qualitätsfall
sichtbar statt als bestandener Test ausgegeben. Ein isolierter Test mit
`qwen3:4b` und Ollamas aktiviertem Denkmodus lieferte auf diesem Laptop
innerhalb von 600 Sekunden keine finale Antwort und endete mit HTTP-Lese-Timeout.
Der Modus wird daher ebenfalls nicht als Standard übernommen. Ollama trennt
[Denkspur und finale Antwort](https://github.com/ollama/ollama/blob/main/docs/capabilities/thinking.mdx)
im API-Ergebnis; die Diagnoseprobe speichert oder druckt die Denkspur nicht.

## Wiederholen

```powershell
backend\.venv\Scripts\python.exe scripts\smoke_reasoning_chat.py
backend\.venv\Scripts\python.exe scripts\smoke_reasoning_chat.py --only concept --model qwen3.5:4b
backend\.venv\Scripts\python.exe scripts\probe_ollama_thinking.py --model qwen3:4b --think
```

Die letzten beiden Befehle setzen das jeweilige lokal installierte Modell
voraus. Die Denkmodus-Probe ist standardmäßig auf 300 Sekunden begrenzt; der
obige 600-Sekunden-Versuch wurde mit `--timeout 600` ausgeführt. Der vollständige
Test kann mit dem aktuellen Standardmodell an der Begriffseinordnung scheitern;
die übrigen echten Fälle wurden separat beobachtet. Der private
Mehrturn-Test für Projekt- und Zeitquellen liegt im privaten Modul-Repository
unter `tests/smoke_multi_turn_chat.py`.
