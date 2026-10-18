# Änderungen

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
