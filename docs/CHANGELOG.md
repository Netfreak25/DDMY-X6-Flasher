# Änderungen

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
