# Bedienung

## App starten

Das Windows-Helper-ZIP aus der Release entpacken und **DDMY X6 Helper.exe**
öffnen. Die portable App zeigt ein kleines Fenster und öffnet den Flasher in
Edge oder Chrome. Das Fenster während der Verwendung offen lassen und danach
schließen. Es gibt keinen Windows-Dienst, Autostart oder Hintergrundbetrieb
nach dem Schließen.

Die EXE enthält Python und die sechs generischen RAM-Hilfsdateien. Eine
vollständige Gerätefirmware ist nicht enthalten. **Offline öffnen** lädt die
mitgelieferte Oberfläche direkt vom Rechner.

## Verbinden

1. Den X6 per USB anschließen.
2. **Verbinden** im Flasher anklicken. Die App erkennt den internen Player
   und übernimmt den Wechsel nach ADFU.
3. **USB-Zugriff erlauben** anklicken und den ADFU-X6 auswählen.

Chrome/Edge fragt gegebenenfalls nach Zugriff auf die lokale App; diesen für
unsere Pages-Seite erlauben. WinUSB muss für `10D6:10D6` installiert sein.
Wenn Windows beim Wechsel Zugriff verweigert, die App schließen und über
**Als Administrator ausführen** erneut starten. Bei mehreren passenden
Playern bricht die Auswahl ab; bitte nur einen X6 anschließen.

Die Startseite und die App selbst senden keine Geräteabfrage. Erst die
Schaltfläche **Verbinden** beginnt den Zugriff.

## Sichern und Vergleichen

**Sicherung erstellen** speichert Bootdatensätze und Firmware als
`.x6backup.zip`. Mediendaten und SD-Karte sind nicht enthalten. Unvollständige
Sicherungen können gespeichert, aber nicht für Full Restore verwendet werden.
Bei einem Speicherfehler bietet **Gelesene Sicherung speichern** die bereits
gelesenen Daten erneut an. Bis dahin die Browserseite nicht schließen.

Eine LFI-`.bin` über **Datei auswählen** laden. Die Prüfung kontrolliert Größe,
Header und alle 148 internen Dateiprüfsummen. **Vergleichen** liest anschließend
den gesamten Firmwarebereich aus und zählt sämtliche abweichenden Bytes.
Auch beschädigter Geräteinhalt kann damit verglichen werden.

## LFI-Teilaktualisierung

Zur vollständigen angepassten Firmware gehören **BREC und LFI**. Für diese
verwende ein passendes vollständiges Paket über **Sicherung wiederherstellen**.
Der einzelne `.bin`-Flash aktualisiert ausschließlich den LFI-Bereich.

**Firmware flashen** bestätigen und den Speicherort der bisherigen Firmware
wählen. Erst nach dem Speichern und lokalen Rücklesen dieser Sicherung wird
geschrieben. Danach wird das vollständige Geräte-LFI zurückgelesen und
byteweise verglichen. Der anschließende Neustart wird separat bestätigt.

## Full Restore

Ein vollständiges Backup-ZIP oder einen bestehenden Toolkit-Sicherungsordner
öffnen. Das Tool prüft Vollständigkeit, Hashes, Modell, NAND-Zuordnung und
übereinstimmende Bootkopien. Der derzeitige Boot-Schreibweg verlangt die
Checkbox **Notfall-Wiederherstellung**.

Eine Rücksicherung gilt nur dann als vollständig verifiziert, wenn LFI und
beide Bootkopien bytegleich zurückgelesen wurden. Fehlende Boot-Rücklesung
wird ausdrücklich als offen ausgewiesen. Bei unbestätigten Schreibbefehlen
stoppt jeder Ablauf ohne automatische Wiederholung.

## Beenden und Berichte

**Player starten** versucht die Rückkehr aus einer antwortenden ADFU-Sitzung
und prüft den normalen USB-Modus. **Trennen** beendet lediglich den Zugriff
des Browsers. **Details** zeigt technische Ergebnisse; **Bericht speichern**
exportiert diese ausschließlich lokal.

Nach Abschluss Browserseite und Windows-App schließen.

## Grenzen der Beta

Die Browserimplementierung und die neue portable App sind noch nicht mit
einem echten X6 auf Hardware geprüft: **unverifizierter Build-Kandidat**.
Automatisierte Tests verwenden simuliertes USB und künstlich erzeugte Daten.
Der Name X6 allein ist keine Kompatibilitätszusage für andere OEM-Varianten.
