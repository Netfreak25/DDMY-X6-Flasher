# Bedienung

## App starten

Das Windows-Helper-ZIP aus der Release entpacken und **DDMY X6 Helper.exe**
öffnen. Die portable App zeigt ein kleines Fenster und öffnet den Flasher in
Chrome, falls installiert, sonst Edge. Das Fenster während der Verwendung offen lassen und danach
schließen. Es gibt keinen Windows-Dienst, Autostart oder Hintergrundbetrieb
nach dem Schließen.

Die EXE enthält Python, die sechs generischen RAM-Hilfsdateien und ab Beta 6
unseren eigenen 196-Byte-RAM-Ausschaltcode. Eine
vollständige Gerätefirmware ist nicht enthalten. **Offline öffnen** lädt die
mitgelieferte Oberfläche direkt vom Rechner.

Ab **Beta 2** kann die Pages-Seite auch direkt in einem vorhandenen Chrome-Tab
geöffnet werden. Die App muss lediglich laufen. Die Version steht im App-Fenster;
zum Aktualisieren die alte App schließen und die neue EXE starten.

Ab **Beta 3** verhindert die App, dass mehrere X6-Webstarter denselben lokalen
Port verwenden. Meldet die App einen belegten Port, die bereits laufende
X6-App beziehungsweise alte X6-Webstarter schließen. Die neue App beginnt
in diesem Fall keinen Gerätezugriff.

## Verbinden

1. Den X6 per USB anschließen.
2. **Verbinden** im Flasher anklicken. Die Seite koppelt sich mit der laufenden
   App. Diese erkennt den internen Player und übernimmt den Wechsel nach ADFU.
3. Den ADFU-X6 im USB-Dialog auswählen. Dauert der Wechsel länger, zuerst
   **USB-Zugriff erlauben** anklicken: Chrome benötigt dafür eine aktuelle
   Benutzeraktion.

Chrome/Edge fragt gegebenenfalls nach Zugriff auf die lokale App; diesen für
unsere Pages-Seite erlauben. WinUSB muss für `10D6:10D6` installiert sein.
Die Berechtigung kann in aktuellem Chrome als Zugriff auf andere Apps und
Dienste auf diesem Gerät erscheinen. Sie betrifft den lokalen HTTP-Helper;
die anschließende USB-Geräteauswahl ist eine eigene Berechtigung.
Wenn Windows beim Wechsel Zugriff verweigert, die App schließen und über
**Als Administrator ausführen** erneut starten. Bei mehreren passenden
Playern bricht die Auswahl ab; bitte nur einen X6 anschließen.

Die Startseite und die App selbst senden beim Öffnen keine Geräteabfrage.
Erst **Verbinden** oder eine der drei Geräteaktionen in der App beginnt den Zugriff.

## ADFU direkt in der App betreten

Ab **Beta 5** lässt sich auch der Wechsel nach ADFU ohne Website auslösen:

1. Den normal gestarteten X6 per USB anschließen und die Windows-App öffnen.
2. **ADFU betreten** anklicken und das Ergebnis abwarten.

Die App erkennt den internen Player, verwendet den bestehenden Windows-SCSI-
Wechsel und wartet auf seine Anmeldung als ADFU-Gerät. Ist er bereits in ADFU,
wird kein weiterer Umschaltbefehl gesendet. Bei verweigertem Windows-Zugriff
die App schließen und **Als Administrator ausführen** wählen.

Während eines Wechsels sind beide ADFU-Buttons gesperrt. Das Öffnen der App
allein schaltet den Player nicht um. Beide Buttons benötigen keine Website.

## ADFU direkt in der App verlassen

Ab **Beta 4** lässt sich der Player auch ohne Website starten:

1. Den X6 per USB anschließen und die Windows-App öffnen.
2. Falls der Browser noch mit dem Player verbunden ist, dort **Trennen** wählen.
   Einen laufenden Schreib- oder Lesevorgang zuerst fertigstellen lassen.
3. Im App-Fenster **ADFU verlassen** anklicken und das Ergebnis abwarten.

Die App verwendet den enthaltenen ADFUS-RAM-Helfer und sendet einen Reset.
Es wird keine Firmware im Flash verändert. Die Erfolgsmeldung setzt voraus,
dass der X6 anschließend als normaler USB-Player erkannt wurde. Ist er bereits
im Normalmodus, wird kein Reset gesendet.

Bei belegtem USB-Zugriff, fehlender Antwort oder unbestätigtem Reset erscheint
eine Fehlermeldung. Es gibt keinen automatischen Wiederholungsversuch und keine
Erfolgsmeldung allein aufgrund eines gesendeten Befehls. Ein hängendes Gerät
kann weiterhin einen physischen Reset benötigen.

## Player über RAM ausschalten

Ab **Beta 6** gibt es **Player ausschalten** direkt im App-Fenster:

1. Laufende Lese- oder Schreibvorgänge abschließen und eine bestehende
   Browser-Verbindung über **Trennen** freigeben.
2. **Player ausschalten** anklicken. Die App wechselt bei Bedarf nach ADFU,
   lädt den enthaltenen Ausschaltcode in den RAM und liest ihn vollständig
   zum Vergleich zurück. Erst bei Übereinstimmung wird er einmal aufgerufen.
3. Die Rückmeldung abwarten. USB kann den Player weiterhin versorgen; in
   diesem Fall anschließend das USB-Kabel abziehen.

Es wird keine Firmware geschrieben. Eine gültige Rückmeldung bestätigt die
Ausführung der Ausschaltfunktion und ihre Registerwerte, aber keinen vollständigen
Stromverlust. Bei fehlender USB-Bestätigung bleibt der Vorgang unverifiziert;
es gibt keinen automatischen Wiederholungsversuch. Die physische Ausschaltwirkung
ist bisher am X6 nicht geprüft. Während des Vorgangs sind alle drei Gerätebuttons gesperrt.

## Sichern und Vergleichen

**Sicherung erstellen** speichert Bootdatensätze und Firmware als
`.x6backup.zip`. Mediendaten und SD-Karte sind nicht enthalten. Unvollständige
Sicherungen können gespeichert, aber nicht für Full Restore verwendet werden.
Die Ergebnismeldung nennt ab Beta 5 Firmware und beide Bootkopien einzeln,
einschließlich des jeweiligen Lesefehlers oder der fehlgeschlagenen Prüfung.
Eine ungültige Boot-Prüfsumme kann auch bei vollständig gültigem LFI auftreten.
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
