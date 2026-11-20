# Jarvis: gemeinsamer Projektauftrag

## Ziel

Jarvis soll ein deutschsprachiger, gesprächsfähiger persönlicher Assistent sein. Er soll freie Fragen verstehen, nötige aktuelle Informationen aus freigegebenen Quellen holen, sie über mehrere Themen hinweg sinnvoll verbinden und passende Werkzeuge einsetzen. Die Qualität einer Antwort bemisst sich daran, ob sie die konkrete Frage mit belegbaren Daten beantwortet, Unsicherheit kenntlich macht und beim nächsten Schritt hilft. Ein paar fest verdrahtete Befehlsketten erfüllen dieses Ziel nicht.

Beispiele für das angestrebte Verhalten: Nach Terminen für heute fragen, aus ihnen freie Zeit für eine neue Aufgabe ableiten, wichtige E-Mails benennen und auf Wunsch einen Entwurf in der gewählten Mail-App sichtbar öffnen. Später können freigegebene Sensoren und Geräte zu einer vorher vereinbarten Heimautomatik führen. Das sind Produktziele; sie sind nicht alle bereits umgesetzt. Insbesondere tatsächlicher Mailversand, Versandbeleg und allgemeine Homelab-Steuerung dürfen erst als vorhanden gelten, wenn sie implementiert und geprüft sind.

## Produktgrundsätze

1. **Gespräch vor Befehlssammlung:** Der Nutzer kann natürlich formulieren und nachfragen. Jarvis hält Gesprächskontext, fragt bei Mehrdeutigkeit nach und unterscheidet Wissen von Vermutung.
2. **Aktueller Kontext mit Herkunft:** Dokumente, bewusst gespeicherte Erinnerungen und optionale Module liefern konkrete, überprüfbare Informationen. Quellen, Zeitbezug und veraltete Daten bleiben sichtbar. Ohne passenden Zugriff behauptet Jarvis keine persönlichen Fakten.
3. **Werkzeuge mit Freigabe:** Lesen, Schreiben, Öffnen und Versenden sind unterschiedliche Wirkungen. Einstellungen erlauben eine Fähigkeit grundsätzlich; die konkrete riskante Aktion wird mit ihren Argumenten überprüft und nach den Produktregeln freigegeben. Die sichtbare App und ein Ausführungsbeleg sind Teil eines vollständigen Mail- oder Computerablaufs.
4. **Modularer Aufbau:** Der öffentliche Kern stellt Chat, Daten- und Modulverträge, Berechtigungen und wiederverwendbare UI bereit. Optionale Module lassen sich installieren, aktivieren und entfernen. Persönliche Pakete bleiben privat, bis ihre Veröffentlichung ausdrücklich entschieden ist. Module sollen sich für Nutzer wie normale Bereiche von Jarvis anfühlen.
5. **Prüfbarer Fortschritt:** Eine Karte ist fertig, wenn der Nutzerablauf funktioniert, passende Tests und bei Bedarf ein echter UI-Pfad bestanden haben, Dokumentation den Stand trifft und verbleibende Grenzen klar sind. Review und Secret-Scan gehören vor die Veröffentlichung.

## Technischer Stand als Ausgangspunkt

Der aktuelle öffentliche Kern bietet eine lokale Windows-Oberfläche mit Chat, Suche in bewusst freigegebenen Dokumenten, Entwürfen, Aufgaben, Erinnerungen, Zeitmodell, Kontakten und begrenzten Browser- und PC-Werkzeugen. Eine Modulschnittstelle stellt optionale Ansichten und Werkzeuge bereit. Neue Installationen wählen OpenRouter als Modellanbieter; ohne eigenen Schlüssel ist kein Cloud-Modellaufruf möglich. Ollama ist die lokale Alternative. Die persönliche Werkzeugwahl im Chat und einige angrenzende Abläufe sind derzeit noch an Ollama gebunden. Die genaue Funktionsliste, Setup-Schritte und offene Grenzen stehen in [README.md](../README.md) und [STATUS.md](STATUS.md).

Der nächste Ausbau sollte die allgemeine Gesprächs- und Werkzeugfähigkeit verbessern, insbesondere die Verbindung von aktuellem Kontext, Rückfragen, Quellen und mehrstufigen Aktionen. Einzelne Integrationen werden über Module und klare Berechtigungen angeschlossen. Lernkarten sind derzeit keine Priorität.

## Arbeits- und Veröffentlichungsgrenze

Das öffentliche Repository ist für andere Nutzer bestimmt. Es enthält keine persönlichen Daten, Zugangsdaten, lokalen Agenten- oder Boardzustände und keine privaten Module. Verwende synthetische Fixtures. Vor jedem öffentlichen Push den Diff prüfen und die vorhandenen Audit- und Git-Schutzskripte ausführen. Eine bestandene Modellbewertung allein ist kein Test für eine externe Aktion, Berechtigung oder erfolgreiche Zustellung.
