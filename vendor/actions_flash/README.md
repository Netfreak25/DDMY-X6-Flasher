# Actions-Backend

`actions_dump.c` basiert auf [actions_flash, Commit 0a7d9cf6](https://github.com/ilyakurdyukov/actions_flash/tree/0a7d9cf6c7dbe343db4dc61c62716e95249e6afb).
Der vollständige ursprüngliche Haftungshinweis steht am Anfang der C-Datei.
Die lokalen Ergänzungen begrenzen die Schreibpause über `ACTIONS_FLASH_DELAY_MS`
und melden bestätigte Schreibfortschritte. Das X6-Profil verwendet 25 ms und
16-KiB-Blöcke. Die veröffentlichte Quelle enthält keine Gerätefirmware.

Die App verwendet das bisherige, per SHA-256 festgelegte Backend. Ein erneuter
Build mit MSYS2 UCRT64 GCC 16.1.0 ergibt dieselben `.text`- und `.rdata`-Abschnitte;
die Release ersetzt den geprüften Backend-Build nicht.

## Build

In einer MSYS2-UCRT64-Umgebung mit GCC und libusb:

```sh
gcc -O2 -Wall -Wextra -std=c99 -pedantic -Wno-unused -DUSE_LIBUSB=1 actions_dump.c -lusb-1.0 -o actions_dump.exe
```

Das Release verwendet die unveränderte `libusb-1.0.dll` 1.0.28.11946 aus
[libusb 1.0.28](https://github.com/libusb/libusb/releases/tag/v1.0.28), dynamisch
neben `actions_dump.exe` geladen. Lizenz: LGPL-2.1-or-later; der vollständige
Text wird als `libusb.txt` beziehungsweise `vendor/libusb.LICENSE` mitgeliefert.
Der zugehörige offizielle Quelltext einschließlich Buildskripten ist im
**Source-ZIP derselben Release** unter `vendor/libusb-1.0.28.tar.bz2` enthalten.
Download und Prüfsumme für erneutes Paketieren stehen in `dependencies.json`.

Eigene Builds und Änderungen sind möglich. Für eine selbst gebaute App die
eigenen Backend-/Bibliotheks-Hashes in `tools/x6_profile.json` eintragen und
neu paketieren. Die Integritätsprüfung der offiziellen App wird nicht umgangen.
Die Rechte zum Ändern und Debuggen der LGPL-Bibliothek werden nicht eingeschränkt.
