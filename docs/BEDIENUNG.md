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

## Firmware flashen · LFI

1. Eine passende LFI-`.bin` auswählen.
2. **Firmware flashen · LFI** wählen und bestätigen.
3. Speicherort für das bisherige LFI wählen.
4. Sichern → Schreiben → vollständiges Rücklesen → Neustart abwarten.

Die Sicherung wird geschlossen, auf Datenträger geschrieben und vollständig
verglichen, bevor der erste Flash-Schreibbefehl startet. Die ausgewählte
Firmwaredatei bleibt unverändert. Header, Größe und alle 148 Dateiprüfsummen
werden geprüft.

Eine einzelne `.bin` ändert nur das LFI. Für die vollständige Firmware samt
BREC und frühem Startbild ein vollständiges Paket wiederherstellen.

## Mit Gerät vergleichen

LFI-Datei wählen, dann **Mit Gerät vergleichen**. Der gesamte LFI-Bereich wird
gelesen. Das Ergebnis zeigt **identisch** oder die Anzahl unterschiedlicher Bytes.
Auch ein beschädigter Header auf dem Gerät verhindert den Vergleich nicht.
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

Version 0.2.0-beta.1: automatisiert und mit vorhandenen Dateien offline geprüft,
**auf Hardware unverifizierter Build-Kandidat**. Für diese Version wurde der
physische Player nicht angesprochen.
