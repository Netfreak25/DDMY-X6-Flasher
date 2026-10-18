# Bedienung · Windows-App und Pages

## Starten und verbinden

Windows-ZIP entpacken, **DDMY X6 Flasher.exe** öffnen und **Verbinden** anklicken.
Der Start allein greift nicht auf den Player zu. Es wird kein Browser geöffnet.
WinUSB muss für das ADFU-Gerät `10D6:10D6` installiert sein.

**Trennen** in der Windows-App hebt die Geräteauswahl auf; es startet den
Player nicht neu. Dafür **ADFU verlassen** verwenden. Der ADFU-Wechsel bei einer
Sicherung oder einem Schreibauftrag erfolgt automatisch.

## Sicherung erstellen

**Sicherung erstellen** wählen und einen Speicherort für die
`.x6backup.zip` angeben. Die fünf Binärdateien und ein kompatibles
`manifest.json` werden gespeichert. Ein Teilfehler wird ausdrücklich als
**unvollständig** angezeigt. Es wird keine fehlende Bootkopie erfunden.

Schlägt nur das Speichern fehl, erscheint **Gelesene Sicherung speichern**.
Dieser Knopf speichert die vorhandenen Daten ohne erneuten Gerätezugriff.
Zusätzlich bleiben die gelesenen Dateien im privaten Arbeitsverzeichnis erhalten.

## Firmware automatisch erkennen und flashen

1. Eine oder mehrere BIN-Dateien auswählen (Mehrfachauswahl mit Strg), oder ein vollständiges `.x6backup.zip`.
2. Die Anzeige nennt automatisch **MBREC**, **BREC**, **LFI** oder ihre Kombination. Erkannt wird der Inhalt, unabhängig vom Dateinamen.
3. **Auswahl flashen** wählen, die angezeigten Bereiche bestätigen und den Speicherort der Rücksicherung wählen.
4. Sichern → Schreiben → vollständiges Rücklesen → Neustart abwarten.

Die Sicherung wird geschlossen, auf Datenträger geschrieben und vollständig
verglichen, bevor der erste Flash-Schreibbefehl startet. Die ausgewählte
Firmwaredatei bleibt unverändert. LFI-Header, Größe und alle 148 Dateiprüfsummen
werden geprüft. Bei BREC werden NAND-Zuordnung, Größe sowie Code- und Headerprüfsumme
geprüft; bei MBREC Marker, Größe und NAND-Geometrie.

| Auswahl | Geschrieben wird |
| --- | --- |
| MBREC-BIN | MBREC |
| BREC-BIN | Beide BREC-Kopien, einschließlich frühem Startbild |
| LFI-BIN | Hauptfirmware |
| Mehrere BINs | Genau die erkannten Bereiche, in Reihenfolge MBREC → BREC → LFI |
| Vollständiges Sicherungs-ZIP | MBREC, beide BREC-Kopien und LFI |

Alle sieben Kombinationen sind möglich. Identische doppelt ausgewählte Bootkopien
werden zusammengefasst; widersprüchliche Kopien werden abgewiesen. Bereits für
die Übertragung angepasste BREC-Dateien werden abgelehnt: die App braucht den
fertigen BREC aus dem Dump und bereitet selbst eine getrennte Übertragungskopie vor.

Bei LFI allein wird das bisherige LFI als BIN gespeichert. Sobald MBREC oder BREC
gewählt ist, wird vorher eine vollständige, geprüfte ZIP-Sicherung erstellt.
Ist das nicht möglich, stoppt dieser Weg vor dem Schreiben; für eine Reparatur
aus vorhandener Sicherung dient **Sicherung wiederherstellen**.

MBREC-Bootpositionen und NAND-Geometrie müssen zum angeschlossenen X6 passen.
Nach Boot-Schreibvorgängen werden beide BREC-Kopien und MBREC zurückgelesen:
ausgewählte Bereiche müssen dem Kandidaten entsprechen, andere Bootbereiche
müssen unverändert sein. Nur nach erfolgreicher Prüfung wird der Neustart versucht.

## Mit Gerät vergleichen

Dateien wie beim Flashen auswählen, dann **Mit Gerät vergleichen**. Die erkannten
Bereiche werden vollständig gelesen, BREC in beiden Kopien. Das Ergebnis zeigt
**identisch** oder die Summe unterschiedlicher Bytes über die verglichenen Kopien.
Ein beschädigter LFI-Header auf dem Gerät verhindert den LFI-Vergleich nicht.
Offsets und Hashes stehen in **Details**. Es wird nicht in den Flash geschrieben.

## Sicherung wiederherstellen

ZIP-Datei oder bisherigen Toolkit-Ordner auswählen, dann
**Sicherung wiederherstellen**. Vollständigkeit, Modell, NAND, Größen, Hashes
und übereinstimmende Kopien werden geprüft. Die BREC-Übertragung wird vorbereitet,
bevor der erste MBREC-Schreibbefehl gesendet wird.

Die Reihenfolge ist **MBREC → BREC → LFI → Abschluss → vollständiges Rücklesen**.
Verglichen wird mit den unveränderten Sicherungsdateien. Nur wenn alle fünf
Bereiche bytegleich sind, erscheint „vollständig verifiziert“.

Das aktuelle X6-Profil gibt den belegten Boot-Schreibweg frei. Bei einem Profil
ohne diese Freigabe verlangt die App eine ausdrückliche Notfall-Bestätigung.
Fehlende Boot-Rücklesung bleibt dabei als unverifiziert sichtbar; kein
automatischer Neustart. Ein unbestätigter Schreibbefehl stoppt in jedem Modus.

## ADFU und Ausschalten

- **ADFU betreten:** vorhandenen Windows-SCSI-Wechsel ausführen und ADFU-Anmeldung prüfen.
- **ADFU verlassen:** antwortende Sitzung per RAM-Helfer zurücksetzen und Normalmodus prüfen.
- **Player ausschalten:** eigenen RAM-Code laden und bytegleich zurücklesen.
  Bei **„Start bestätigt. Jetzt USB-Kabel abziehen.“** USB abziehen. Erst danach
  fordert der RAM-Loop das Ausschalten an. Es gibt keinen Countdown.

Eine Startbestätigung ist keine Bestätigung des physischen Ausschaltens.
Bei einem nicht antwortenden Gerät gibt es keinen automatischen Wiederholungsversuch.
Bei Windows-Zugriff verweigert die App gegebenenfalls als Administrator starten.

## Online-Flasher

Die [Pages-Seite](https://netfreak25.github.io/DDMY-X6-Flasher/) bleibt verfügbar.
App starten, Pages in Chrome/Edge öffnen und dort **Verbinden** wählen.
Den Zugriff auf die lokale App erlauben; anschließend ADFU-X6 im WebUSB-Dialog
auswählen. Nach einem längeren Wechsel einmal **USB-Zugriff erlauben** anklicken.

**Während der Browser verbunden ist, bleibt die native App für Geräteaufträge
gesperrt.** Im Browser **Trennen** wählen, bevor zur Windows-App gewechselt wird.
Vor Schließen/Neuladen der Seite ebenfalls trennen. Die Sperre läuft nicht
zeitgesteuert ab, damit ein langer Schreibvorgang nicht unterbrochen wird.

Wurde ein Tab unerwartet beendet und bleibt die Sperre bestehen: sicherstellen,
dass kein Browserauftrag mehr läuft, alle X6-Tabs schließen und erst dann die
X6-App nötigenfalls über den Task-Manager beenden und neu öffnen. Keine
Geräteschreibbefehle werden dadurch automatisch wiederholt.

Der lokale Port bietet ausschließlich die Kopplungs-API; eine Offline-Website
gibt es nicht mehr. Ist der Port belegt, bleibt die native App verfügbar.
Mehrere App-Instanzen werden zusätzlich über eine Windows-Dateisperre koordiniert.

## Fortschritt, Berichte und Dateien

Der Fortschrittsbereich zeigt den aktuellen Schritt. Prozentwerte werden nur
aus bestätigten Backend-Meldungen berechnet; ohne solche Meldungen läuft eine
Schrittanzeige. **Details** und **Bericht speichern** enthalten technische Angaben.

Private Arbeitsdateien: `%LOCALAPPDATA%\DDMY-X6-Flasher\private_dumps`.
Dort bleiben Dumps und Laufprotokolle auch nach dem Schließen erhalten.
Die App läuft nur, solange ihr Fenster geöffnet ist: kein Dienst, kein Autostart.

**Full ist kein Rohdump aller 4 GB.** Gesichert werden MBREC, dessen zweite
Rücklesung, beide BREC-Kopien und LFI. Musik und SD-Karte gehören nicht dazu.

## Prüfstand

Version 0.2.0-beta.2: automatisiert und mit vorhandenen Dateien offline geprüft,
**auf Hardware unverifizierter Build-Kandidat**. Für diese Version wurde der
physische Player nicht angesprochen.
