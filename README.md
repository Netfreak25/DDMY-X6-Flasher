# DDMY X6 - Flasher

<img src="assets/x6-icon.svg" alt="X6 Flasher Icon" width="72" align="right">

Eine **Windows-App mit Installer und portabler Version** zum Sichern, Vergleichen und Flashen des
**DDMY X6 mit Actions ATJ2157**. Dieselben Hauptfunktionen stehen auch auf
unserer WebUSB-Seite bereit.

**[Windows-App herunterladen](https://github.com/Netfreak25/DDMY-X6-Flasher/releases/tag/v0.3.0-beta.3)** ·
**[Online-Flasher öffnen](https://netfreak25.github.io/DDMY-X6-Flasher/)** ·
**[Bedienung](docs/BEDIENUNG.md)**

- **[Windows-Installer](https://github.com/Netfreak25/DDMY-X6-Flasher/releases/download/v0.3.0-beta.3/DDMY-X6-Flasher-Setup.exe)**: Installation, Startmenüeintrag und Deinstallation.
- **[Portable ZIP](https://github.com/Netfreak25/DDMY-X6-Flasher/releases/download/v0.3.0-beta.3/DDMY-X6-Flasher-Windows.zip)**: entpacken und starten.

## Funktionen

| Funktion | Ablauf |
| --- | --- |
| Sicherung erstellen | MBREC, MBREC-Kopie, BREC 0, BREC 1 und vollständiges LFI als kompatibles `.x6backup.zip` speichern. |
| Sicherung wiederherstellen | ZIP oder Toolkit-Ordner prüfen, MBREC → BREC → LFI schreiben, abschließen und alle fünf Dateien zurücklesen. |
| Auswahl flashen | MBREC, BREC und LFI am Inhalt erkennen: einzeln, beliebig kombiniert oder als vollständiges ZIP-Paket. Vorher Rücksicherung speichern, danach vollständig zurücklesen. |
| Mit Gerät vergleichen | Alle ausgewählten Bereiche byteweise vergleichen, bei BREC beide Kopien. Beschädigtes Geräte-LFI bleibt vergleichbar. |
| ADFU betreten / verlassen | Den Wartungsmodus direkt in der App wechseln; Erfolg erst nach erkannter USB-Anmeldung melden. |
| Player ausschalten | Eigenen RAM-Code laden und zurücklesen. Nach Startbestätigung USB abziehen; der Code wartet darauf. |
| Bilder bearbeiten | 11 Hintergründe, Startbild, Ausschaltbild und frühes BREC-Startbild ersetzen, zuschneiden und vergleichen. Geänderte BINs/Pakete lokal speichern oder genau die geänderten Bereiche flashen. |

**Full umfasst Bootdatensätze und LFI**, etwa 62 MB. Musikdateien, der übrige
NAND-Inhalt und die SD-Karte sind nicht enthalten. **BREC gehört zur vollständigen
Firmware**, einschließlich des frühen Startbilds. Eine einzelne LFI-`.bin`
ist nur eine Teilaktualisierung. Für BREC + LFI beide BINs gemeinsam auswählen;
für alle Bereiche zusätzlich MBREC oder ein vollständiges Sicherungspaket laden.
Der Dateiname bestimmt niemals das Schreibziel. Unterschiedliche Dateien für
denselben Bereich werden abgewiesen; identische Bootkopien werden zusammengefasst.

Unvollständige Sicherungen bleiben speicherbar, werden jedoch nicht für Restore
freigegeben. Kein unbestätigter Schreibbefehl wird automatisch wiederholt.
„Verifiziert“ erfordert vollständiges bytegleiches Rücklesen.

## Schnellstart

1. Installer ausführen oder Windows-ZIP entpacken, dann **DDMY X6 Flasher.exe** öffnen.
2. **Verbinden** anklicken. Erst dann wird der angeschlossene Player abgefragt.
3. Eine der vier Aktionen auswählen. Für Flash/Restore zuerst die Datei beziehungsweise Sicherung wählen.
4. Die App nach dem Vorgang schließen.

Kein Dienst, kein Autostart, keine separate Python-Installation und kein
automatischer Browserstart. Fenster- und Taskleisten-Icon sind enthalten.
Die native App benötigt **keinen Browser und keine lokale Website**.
Der frühere Offline-Webstarter und „Offline öffnen“ sind entfernt.

Updates erst nach beendetem Lesen, Schreiben und Rücklesen installieren. Das
Setup wartet auf eine geschlossene App und beendet keine Prozesse. Sicherungen
und Einstellungen in LocalAppData bleiben auch bei der Deinstallation erhalten.

Voraussetzungen: Windows 10/11 (64 Bit), WinUSB für ADFU `10D6:10D6`.
Der normale Massenspeichertreiber bleibt bestehen. Bei einem Windows-Zugriffsfehler
beim ADFU-Wechsel die App über **Als Administrator ausführen** starten.

## Bildeditor

**Bilder bearbeiten** öffnet die Galerie der 14 festen Bildplätze. LFI/BREC oder
eine eigene Sicherung laden, ein Bild ersetzen und den Ausschnitt wählen. PNG,
JPEG und WebP werden auf 240 × 320 RGB565 umgewandelt. Die Startbilder in LFI und
BREC sind standardmäßig gekoppelt. Keine zusätzlichen Bildplätze, kein Löschen.

Bearbeitung und Speichern sind offline möglich. **Vom Gerät laden** und
**Änderungen flashen** greifen erst nach ausdrücklicher Auswahl auf den Player zu.
Quellen werden nicht überschrieben, Exporte erneut geprüft. Bilder und Firmware
bleiben lokal. [Anleitung und Liste aller Bildplätze](docs/BILDEDITOR.md).

## GitHub Pages weiterverwenden

App geöffnet lassen und die [Pages-Seite](https://netfreak25.github.io/DDMY-X6-Flasher/)
in Chrome oder Edge öffnen. Alternativ **Online-Flasher öffnen** in der App.
**Verbinden** koppelt den Tab mit der App und öffnet nach dem ADFU-Wechsel den
WebUSB-Dialog. Nach einem längeren Wechsel ist ein zweiter Klick auf
**USB-Zugriff erlauben** nötig.

Browser und App teilen eine exklusive Gerätesperre. **Im Browser zuerst Trennen**,
bevor die native App das Gerät verwenden soll. Auch mehrere App-Instanzen sind
gegeneinander gesperrt. Ein belegter API-Port verhindert nur die Pages-Kopplung;
die native App bleibt verwendbar.

## Wie es arbeitet

Die Windows-App steuert den vorhandenen Actions-Backend-Code direkt. Ein
Hintergrundauftrag hält die Oberfläche bedienbar; Fortschritt stammt aus
bestätigten Backend-Antworten. Details und Berichte sind separat erreichbar.

Die bekannte Reihenfolge der RAM-Treiber, 16-KiB-Schreibblöcke und 25-ms-Pausen
bleiben bestehen. Bereits geladenes ADFUS wird vor dem ROM-INFO-Befehl erkannt.
Die BREC-Prüfsumme wird nur in einer Übertragungskopie vorbereitet; Originaldateien
bleiben erhalten und sind Grundlage des Rücklesevergleichs.

Die Pages-Seite überträgt Boot- und Firmwaredaten weiterhin **direkt per WebUSB**.
Die App bietet dafür ausschließlich eine Loopback-API mit Sitzungsschlüssel,
Herkunftsprüfung und Gerätesperre. Sie liefert keine HTML-/JS-/CSS-Website aus
und nimmt keine Firmwaredateien über HTTP an.

Dumps, Arbeitsdateien und Protokolle der nativen App liegen unter
`%LOCALAPPDATA%\DDMY-X6-Flasher\private_dumps`. Ausgewählte Sicherungen werden
zusätzlich am gewünschten Speicherort abgelegt. Firmware, Backups und Berichte
werden nicht hochgeladen; keine Telemetrie.

## Das Gerät

Der DDMY X6 ist ein Multimedia-Player mit Touchscreen, Musik, Video, Radio,
Aufnahme, Bluetooth, E-Books und microSD-Unterstützung.

| Merkmal | Unterstützte Variante |
| --- | --- |
| Marke / Modell | DDMY X6 |
| Prozessor | Actions ATJ2157 |
| Display | 240 × 320 Pixel, Hochformat |
| Interner Speicher | 4-GB-NAND; internes USB-Laufwerk mit 3.835.691.008 Byte |
| NAND-Kennung | `2C 68 04 4A A9` |
| USB normal / ADFU | `10D6:1101` / `10D6:10D6` |
| LFI-Größe | 61.657.088 Byte |

Andere „X6“-Geräte können andere Hardware enthalten; die Unterstützung gilt für
dieses Profil.

<img src="assets/ddmy-x6-detail.png" alt="Nahaufnahme des DDMY-X6-Displays" width="340">

*Mit Zustimmung veröffentlichtes Gerätefoto. Die abgebildete Firmware ist nicht enthalten.*

## Pakete und Prüfstand

- **Windows-ZIP:** portable App, bestehendes Actions-Backend + libusb, sechs
  freigegebene generische RAM-Helfer und eigener RAM-Ausschaltcode.
- **Source-ZIP:** Flasher, Pages-Code, Tests, Icons, Bedienung, Backend-Quelltext
  und zugehöriger libusb-Quelltext.
- **Nicht enthalten:** Gerätefirmware, modifizierte Images, Dumps, private
  Sicherungen, Serienkennungen und Reverse-Engineering-Unterlagen.

**v0.3.0-beta.3 ist ein auf Hardware unverifizierter Build-Kandidat.**
Automatisierte Tests prüfen native Abläufe, ZIP-Austausch mit JavaScript,
fehlende Bestätigungen, Zeitüberschreitungen, Rücklesefehler, GUI, HTTP-Grenzen
und simuliertes WebUSB. Alle sieben Bereichskombinationen werden mit simulierten
Antworten geprüft, einschließlich BREC-Prüfsummenkorrektur und beider Bootkopien.
Der EXE-Selbsttest prüft Ressourcen, Icons und Kopplung
ohne Gerätezugriff. Der zugrunde liegende native Schreibweg und die korrigierte
BREC-Vorbereitung stammen aus dem bestehenden Toolkit; die neue GUI-Ausführung
wurde in dieser Version nicht am Player getestet.

## Entwicklung

Python 3.11, Node.js und die Abhängigkeiten aus `package-lock.json`:

```text
npm ci --ignore-scripts
npm test
npm run test:ui
npm run lint
python -m pip install -r tools/requirements.txt
python -m unittest discover -s tests -q
python -m ruff check tools tests
```

Für den EXE-Build: PyInstaller 6.22.3, die acht hashgeprüften Runtime-Dateien aus
dem X6-Profil unter `private_dumps/toolkit/runtime/` und ARM GCC 13.3.0.
`X6_ARM_GCC` kann auf den ARM-Compiler zeigen. `python tools/build_helper.py`
baut den Ausschaltcode und die App und startet den hardwarefreien EXE-Selbsttest.

Für den zusätzlichen Installer **Inno Setup 6** installieren und `X6_ISCC` auf
`ISCC.exe` setzen. Danach `python tools/build_installer.py` ausführen. Das Setup
übernimmt ausschließlich dieselben Dateien wie die portable ZIP. Nach dem Commit
erzeugt `python tools/package_release.py` beide ZIPs und Prüfsummen einschließlich
des Installers; veraltete Installer werden abgewiesen.

`tools/install_local.ps1 -Installer <setup.exe> -InstallDir <ordner> -LogPath <log>`
wartet für lokale Updates auf die freie Gerätesperre und fordert reguläres
Schließen an. Die App schützt weiterhin laufende Aufträge und ungespeicherte
Bildänderungen. Danach startet das Skript den Installer mit Windows-UAC.
Die lokale Python-App startet über **Start X6 App.cmd**.
`python tools/build_poweroff.py` baut ihren RAM-Code ins Runtime-Verzeichnis.

Die Quelltext-Release enthält die RAM-Helfer nicht separat. Für einen eigenen
Build dürfen sie aus der Windows-Release extrahiert werden; ihre Hashes bleiben
verbindlich. Buildausgaben bleiben unter dem ignorierten `private_dumps/`.
Zum Paketieren den in `vendor/actions_flash/dependencies.json` festgelegten
libusb-Quelltext unter `private_dumps/build/backend/` bereithalten, Änderungen
committen und `python tools/package_release.py` ausführen.

[Abhängigkeiten und Lizenzen](THIRD_PARTY.md) · [Änderungen](docs/CHANGELOG.md)
