# Änderungen

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
