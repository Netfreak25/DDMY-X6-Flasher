# Änderungen

## 0.3.0-beta.8 – lokal, unveröffentlicht

- Screensaver-Einbau und -Aktualisierung in beiden Oberflächen deaktiviert.
  Der seitliche Bildwechsel bleibt ein ungelöster Gerätefehler.
- Bildverwaltung für den vorhandenen Katalog: Schwarz unveränderlich, zehn
  Standardmotive ersetzbar, Zusatzmotive innerhalb der verfügbaren Reserve
  hinzufügbar und löschbar. Kein Screensaver-Code wird geändert.
- Gemeinsames Speicherprofil für Python/JavaScript. Nur die vollständig
  nullgefüllte Datei `X6FREE.BIN` gilt als Reserve; Änderungen bleiben atomar,
  gleich groß, vollständig zugeordnet und prüfsummengültig.
- Optionales Vorabbackup nun auch beim Full Restore, mit derselben Checkbox-
  Einstellung wie Flash und Bildeditor. Image- und Rückleseprüfung bleiben.
- Alle fünf Firmwaredateien einzeln oder gemeinsam sichern: eine als BIN,
  mehrere als ZIP. Absichtliche Teilsicherung ist keine vollständige Rücksicherung.
- „Player neustarten“ und offline erreichbare Recovery-Hilfe mit der am X6
  bestätigten Power-/Reset-Abfolge. Wartephasen bleiben ohne Laufanimation.
- 156 Python-Tests, 74 JavaScript-Tests und 28 Browser-Tests bestanden;
  vier Python- und zwei JavaScript-Tests mit optionalen privaten Voraussetzungen
  übersprungen. Tk-Oberflächentests laufen wegen Tcl-Lebensdauer getrennt.
  Ruff, ESLint und EXE-Selbsttest bestanden, ohne Gerätezugriff.
- Abschlusstests reproduzierten einen abgeschnittenen Speichern-Knopf bei
  mehrzeiligem Fehler und einen falschen Profilpfad in der gepackten EXE.
  Abstände bzw. Ressourcenpfad korrigiert; passende Tests danach bestanden.

## 0.3.0-beta.7 – lokal, unveröffentlicht

- Checkbox **Vor dem Flashen sichern** in Windows/Web und im Bildeditor.
  Standardmäßig aktiviert; abgewählt entfallen vollständiger Vorabdump und
  Speicherdialog. Geräteprüfungen und vollständiges Rücklesen bleiben aktiv.
- Windows-Warteschritte zeigen einen statischen Hinweis statt einer laufenden
  Fortschrittsanimation. Bestätigte Schreibwerte bleiben als Prozent sichtbar.
- Regressionen für beide Optionen, alle sieben Bereichskombinationen, Editor,
  Headerfehler, ausbleibende Bestätigungen und ruhige Wartephasen.
- Nur lokal gebaut; wegen des laufenden Nutzer-Flashs nicht installiert.

### Integrationsfehler der vorherigen beta.6

Die Sicherungsoption und ruhige Statusanzeige waren bereits im separaten
Branch `codex/optional-flash-backup`, Commit `72f7ef7`, umgesetzt. Dieser Branch
und der spätere Screensaver-/Padding-Stand `9c89fd2` hatten beide die Nummer
beta.6. Das später installierte Paket wurde aus `main` gebaut, ohne den
Feature-Branch zu integrieren. Der ältere Feature-Build war wegen eines
laufenden Nutzer-Flashs nur vorbereitet und nicht installiert worden.

beta.7 führt beide Entwicklungsstände zusammen. Der EXE-Selbsttest prüft
zusätzlich die Sicherungs-Checkbox und den eigenen flachen Fortschrittsstil
zusammen mit dem Screensaver-Profil. Der vollständige Branchabgleich ergab
keine weiteren unabhängigen Funktionen in dem ausgelassenen Commit.

## 0.3.0-beta.6 – lokal, unveröffentlicht

- Optionales Nachrüsten und Aktualisieren des Screensavers auf v7 in Windows
  und Web; bereits aktuelle Versionen werden nicht doppelt gepatcht.
- Erkennt Varianten mit und ohne Hello X6, erhält dessen Menü und Launcher.
- Hintergrundaufbereitung nach Zuschnitt/Drehung mit dem vorhandenen dezenten
  Verlauf; der schwarze v7-Platz ist vor wirkungslosen Bildänderungen geschützt.
- LFI-Prüfung umfasst die Bootloader-Größenregel. Import, Export und
  Schreibvorbereitung ordnen Lücken als Nullpadding zu und aktualisieren
  Dateilängen und Prüfsummen. Quelldateien und benannte Inhalte bleiben erhalten.
- Alle vorhandenen Dateien werden geprüft; die Prüfung setzt nicht mehr
  unveränderlich 148 Verzeichniseinträge voraus.
- Keine Firmware im App-Paket; kein Gerätezugriff bei Entwicklung und Tests.

## 0.3.0-beta.3

- Zusätzlicher Windows-Installer mit App-Icon, Startmenüeintrag, optionaler
  Desktop-Verknüpfung und Deinstallation. Portable ZIP bleibt verfügbar.
- Installer und ZIP verwenden dieselbe begrenzte Dateiliste. Keine Firmware,
  extrahierten Bilder oder privaten Sicherungen im Installer.
- Installation und Deinstallation blockieren bei laufender X6-App; kein
  zwangsweises Schließen und kein automatischer Neustart.
- Lokales Update-Skript wartet auf das Ende des Geräteauftrags, schließt die
  App regulär und startet erst danach das Setup. Ungespeicherte Bearbeitungen
  werden weiterhin durch die App geschützt.
- Pakettests prüfen Dateiauswahl und veraltete Installer. Programm- und
  Firmware-Übertragungsfunktionen bleiben unverändert.

## 0.3.0-beta.2

- Windows-Bildeditor: Die Galerie nutzt die gesamte verfügbare Breite und passt
  ihre Spaltenzahl beim Vergrößern und Verkleinern des Fensters an.
- Die leere weiße Fläche entfällt; der Galeriehintergrund folgt dem Fensterthema.
- Die Beschriftung des frühen Startbilds bleibt auch ohne geladenes BREC lesbar.
- Regressionstest mit 960, 1040 und 1920 Pixel Fensterbreite sowie anschließendem
  Verkleinern; Auswahl und bereits bearbeitete Bilder bleiben erhalten.
- Native GUI-Tests und EXE-Selbsttest ohne Gerätezugriff. Die Flashfunktionen
  bleiben ein auf Hardware unverifizierter Build-Kandidat.

## 0.3.0-beta.1

- Bildeditor in der nativen Windows-App und auf Pages: 14 feste Bildplätze mit
  Galerie, Originalvergleich, Ersetzen, Ausschnitt und Zurücksetzen.
- 11 Screensaver-Hintergründe, normales Startbild, Ausschaltbild und frühes
  BREC-Startbild; Startphasen standardmäßig gekoppelt.
- Statischer PNG-/JPEG-/WebP-Import mit Größenlimits, EXIF, sRGB und schwarzer
  Alpha-Verrechnung. Zuschnitt, Einpassen, Verschieben, Zoom und 90°-Drehung.
- Gemeinsamer Ressourcenkatalog und Farbtestvektoren; STY RGB565 LE, BREC BE.
- Nur Pixel und erforderliche LFI-Prüfsummen ändern; Bootcode, Tabellen,
  Übersetzungen, Dateigrößen und andere Ressourcen bleiben erhalten.
- Geänderte BINs oder vollständiges bearbeitetes Firmwarepaket exportieren,
  erneut öffnen und prüfen. Vorhandene Dateien werden nicht überschrieben.
- Nur tatsächlich geänderte Bereiche über den vorhandenen Flasher übertragen;
  Bildeditor schreibt kein MBREC. Beide BREC-Kopien werden zurückgelesen.
- Explizites Laden vom Gerät speichert zuerst die unveränderte Originalsicherung.
- Automatisierte Python-/JavaScript-/GUI-Tests, Vergleich beider Implementierungen
  mit vorhandenen lokalen Dateien und Windows-EXE-Selbsttest ohne Gerätezugriff.
  Die neue Funktion bleibt ein auf Hardware unverifizierter Build-Kandidat.

## 0.2.0-beta.2

- Automatische Erkennung von MBREC, BREC und LFI nach Dateiinhalt, in Windows-App und Pages.
- Einzeldateien, Mehrfachauswahl und vollständige ZIP-Pakete; alle sieben Bereichskombinationen.
- Vorschau und Bestätigung der tatsächlich gewählten Bereiche; identische Kopien werden zusammengefasst.
- Vor Boot-Teilaktualisierungen vollständige Rücksicherung speichern; danach FWSC neu laden.
- Nur ausgewählte Bereiche schreiben; beide Bootkopien zurücklesen und unveränderte Bootbereiche mitprüfen.
- BREC-Prüfsummenkorrektur vor dem ersten Schreibbefehl prüfen, auch im Web-Full-Restore.
- Gerätevergleich für dieselbe automatische Bereichsauswahl mit vollständiger Bytezählung.
- Regressionstests für Auswahl, beschädigte Daten, alle Kombinationen, fehlende Bestätigungen und zweite Bootkopie.
- Original und vorhandene Rücklesesicherung offline geprüft. Kein Gerätezugriff;
  auf Hardware unverifizierter Build-Kandidat.

## 0.2.0-beta.1

- Native Windows-App mit Sicherung, Wiederherstellung, LFI-Flash und Gerätevergleich.
- Gemeinsame exklusive Gerätefreigabe für App und GitHub Pages; zusätzliche Sperre zwischen App-Instanzen.
- Kein automatischer Browserstart; lokale Website und Offline-Webstarter entfernt.
- Persistente Arbeitsdaten unter LocalAppData, getrennt von den Programmressourcen.
- ZIP-/Ordner-Austausch mit Pages; teilweise gelesene Sicherungen bleiben speicherbar.
- Vollständiger Rücklesevergleich, vorgeschaltete gespeicherte LFI-Sicherung und bekannte BREC-Vorbereitung.
- ADFUS-RAM-Prüfung vor ROM INFO ermöglicht Wiederverwendung einer vorhandenen Sitzung.
- Fehlende LFI-Dateianzahl im Python-Prüfer durch realen Dateitest reproduziert und ergänzt.
- Bestehender Actions-Backend-Build und libusb werden mit Hashprüfung mitgeliefert;
  Backend-Quelle und Bibliotheksquelle sind im Source-ZIP enthalten.
- Prüfungen mit simulierten Antworten und Offline-Dateien; kein physischer Gerätezugriff.
  Die neue App bleibt ein auf Hardware unverifizierter Build-Kandidat.
