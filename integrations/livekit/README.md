# G02: lokale Spracheingabe und LiveKit-Prüfung

## Laufender Chatpfad

Der Browser nimmt Audio mit `MediaRecorder` nur nach einem expliziten Klick auf. Jarvis prüft Typ und 8-MiB-Grenze, sendet das Audio an einen getrennten, wiederverwendeten Faster-Whisper-CPU-Prozess und zeigt das deutsche Transkript editierbar an. Senden verwendet denselben Chatstream und dieselben Ollama-Regeln wie Texteingaben; Vorlesen verwendet den bestehenden VoiceStudio-Endpunkt. Ein neuer Mikrofonstart stoppt den Player und bricht eine ausstehende Antwort beziehungsweise Audioanforderung ab. Die Aufnahme wird weder in der Datenbank noch als Datei abgelegt.

Die Einrichtung steht im Haupt-README. Bei fehlendem Modell antwortet `/voice/status` mit `stt_ready: false`; `/voice/transcribe` meldet den Einrichtungsfehler. Die isolierte Laufzeit verwendet Python 3.13, `faster-whisper==1.2.1` und `av==18.1.0`. Das Modell `base` wird nach `scripts/setup_voice.py` ohne Netzwerkzugriff geladen. Der Backendprozess beendet den STT-Prozess beim Herunterfahren.

## LiveKit-Pilot

Die isolierte Umgebung enthält `livekit-agents[openai,silero]==1.8.10`. Der selbst gehostete LiveKit-Server 1.13.11 ließ sich unter Windows auf `127.0.0.1:7880` starten und wurde nach der Prüfung gestoppt. Für den produktiven Chatpfad wird zurzeit kein LiveKit-Raum geöffnet und kein LiveKit-Agent gestartet. Der Browserpfad erfüllt eine bewusst gestartete, unterbrechbare Sprachrunde; freies Dazwischensprechen ohne Klick ist noch nicht implementiert. Für echtes kontinuierliches Barge-in wäre eine AgentSession mit lokalem STT, Ollama und VoiceStudio als Audioframes sowie eine eigene Latenz-/Echo-Prüfung nötig. Die vorhandene isolierte Umgebung und der Serverstart sind dafür vorbereitet, aber keine Abnahme dieser Realtime-Variante.

Quellen: [LiveKit Agents](https://github.com/livekit/agents), [lokaler LiveKit-Server](https://docs.livekit.io/transport/self-hosting/local/), [Ollama-LLM](https://docs.livekit.io/agents/models/llm/ollama/), [Silero VAD](https://docs.livekit.io/agents/logic/turns/vad/), [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper).

## Gemessener Stand auf dem i7-1360P

- Echtes deutsches WAV, 5,52 s: Whisper-base-Modellstart ca. 15 s, Erkennung 5,39 s im ersten direkten Lauf; derselbe Satz durch den persistenten Jarvis-Prozess 4,04 s.
- Edge mit emuliertem Mikrofon, WebM-Aufnahme 6 s: 15,85 s STT unter gleichzeitiger CPU-Last; 38,75 s vom Aufnahmestart bis zum editierbaren Transkript. Der Text hatte kleine Erkennungsfehler.
- Edge-Unterbrechung während tatsächlich laufender Wiedergabe: altes Audio nach beobachteten 134 ms, in einer Wiederholung unter CPU-Last nach 685 ms pausiert; neue Aufnahme begonnen. Der erneute Test durch Ollama, Abbruch der Audioanforderung, Mikrofon-Aus und 390-Pixel-Ansicht bestand.
- Drei verschiedene deutsche Audioteile als getrennte Dialogrunden: STT 8,34/13,36/11,12 s, Ollama 9,02/15,00/30,50 s, drei fertige Antworten gespeichert.
- Ein zusätzlicher Edge-Lauf mit Mikrofon, STT und Ollama wartete auf frisch erzeugtes VoiceStudio-`tts-1`-Audio (HTTP 200), spielte es im Browser ab und stoppte es durch einen neuen Mikrofonstart. Auch Mikrofon-Aus und 390-Pixel-Ansicht bestanden.
- Nach der letzten UI-Korrektur bestand der Edge-Retest mit 4,06 s STT, 117 ms beobachteter Unterbrechung, Ollama-Antwort, Mikrofon-Aus und 390-Pixel-Ansicht. Der Wechsel zu „Aufgaben“ beendete den aktiven Mikrofon-Track, ohne später Audio hochzuladen.
- VoiceStudio/MOSS-TTS-Nano auf CPU: ein kurzer Satz benötigte 124,8 s für 253.484 WAV-Bytes. Der erste längere Aufruf überschritt 120 s; Antworten können also deutlich später hörbar sein. Es gibt bewusst keine Behauptung einer Latenz unter 300 ms.

Der erste Edge/Ollama-Lauf scheiterte an einem Timeout unter CPU-Last; die Wiederholung bestand. Änderungen an VoiceStudio-Modell oder Hardware brauchen neue Messungen.

Gesamtregression nach G02: 514 Backendtests bestanden, 2 übersprungen; zwölf Frontendtests, TypeScript-Build und Ruff bestanden.
