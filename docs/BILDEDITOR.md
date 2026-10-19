# Bilder bearbeiten · Windows und Web

Der Bildeditor in **v0.3.0-beta.7 (lokaler Build)** ersetzt vorhandene Bilder in deinen eigenen
Firmwaredateien. Er liefert keine Firmware oder Bildsammlung mit. Bearbeiten
und Speichern benötigen weder Player noch Windows-Helfer auf der Website.

## Dateien laden

1. **Bilder bearbeiten** in der Windows-App oder **Bildeditor öffnen** auf Pages wählen.
2. **Dateien öffnen:** LFI und BREC gemeinsam auswählen (Strg), oder ein vollständiges
   `.x6backup.zip`. **Ordner öffnen** akzeptiert bisherige Toolkit-Sicherungen.
   Auf Pages lässt sich auch die bereits geprüfte **Firmwareauswahl übernehmen**.
3. Alternativ nach **Verbinden** ausdrücklich **Vom Gerät laden** wählen. Zuerst
   wird eine vollständige Sicherung unter einem neuen Namen gespeichert. Nur ein
   vollständiges, gültiges Ergebnis wird als Arbeitsgrundlage geöffnet.

Ein LFI allein bietet 13 Bildplätze. BREC allein bietet das frühe Startbild.
Fehlende Bereiche sind mit **Nicht geladen** markiert. MBREC enthält keinen
editierbaren Bildplatz. Mehrere einzelne BINs bitte gemeinsam auswählen;
erneutes Öffnen ersetzt die gesamte Arbeitsgrundlage.

## Die 14 festen Plätze

Die Galerie zeigt die tatsächlich geladenen Bilder, unabhängig von ihrem Motiv.
Die Ressourcenindizes in der Tabelle sind nullbasiert.

| Bildplatz | Vorhandene Ressource |
| --- | --- |
| Hintergrund 1 | COMMON.STY #298 |
| Hintergrund 2 | COMMON.STY #299 |
| Hintergrund 3 | COMMON.STY #300 |
| Hintergrund 4 | COMMON.STY #301 |
| Hintergrund 5 | COMMON.STY #302 |
| Hintergrund 6 | COMMON.STY #303 |
| Hintergrund 7 | COMMON.STY #304 |
| Hintergrund 8 | COMMON.STY #305 |
| Hintergrund 9 | COMMON.STY #306 |
| Hintergrund 10 | COMMON.STY #307 |
| Hintergrund 11 | COMMON.STY #308 |
| Startbild | LOGO.STY #0 im LFI |
| Ausschaltbild | END.STY #0 im LFI |
| Frühes Startbild | BREC, beim Flashen beide Bootkopien |

Es können keine Plätze hinzugefügt oder gelöscht werden. Die Firmware entscheidet
weiterhin, welchen der 11 Hintergründe der Screensaver verwendet.
Bei Screensaver v7 ist der erste Platz fest schwarz und nicht bearbeitbar.
Die zehn Motive dahinter bleiben bearbeitbar. Die vorbereitete variable
Bilderliste ist die Grundlage einer späteren Erweiterung des Editors.

## Motiv und Ausschnitt

- **Ersetzen:** statisches PNG, JPEG oder WebP wählen; höchstens **25 MiB / 40 MP**.
- **Bearbeiten:** Ausschnitt des gewählten Motivs nachträglich ändern.
- Standard: mittig und bildfüllend zuschneiden. **Einpassen** erhält das ganze Bild
  und ergänzt schwarze Ränder. Zoom, Ziehen oder die Positionsregler verschieben
  den Ausschnitt. Drehung erfolgt in 90°-Schritten.
- **Übernehmen** wendet den Ausschnitt an. **Abbrechen** verändert keine Bildbytes.
- **Änderung zurücksetzen** stellt das Originalmotiv dieses Platzes wieder her;
  bei unterstützter Kontur-Firmware mit der unten beschriebenen Aufbereitung.

EXIF-Ausrichtung wird berücksichtigt, Farben werden nach sRGB gebracht und
Transparenz auf Schwarz verrechnet. Animierte Dateien werden abgewiesen. Das
Ergebnis wird auf **240 × 320 Pixel und RGB565** reduziert. Vorschau und
Originalvergleich zeigen die daraus dekodierten Pixel einschließlich der
begrenzten Farbabstufungen. Die Interpolation kann zwischen Pillow und Chrome
geringfügig variieren; RGB565-Kanalrundung und Bytefolge sind identisch festgelegt.

Sind LFI und BREC geladen, ist **Startbilder gemeinsam ändern** standardmäßig
aktiv. Eine Änderung oder ein Zurücksetzen betrifft dann beide Startphasen.
Für unterschiedliche Motive die Kopplung vor der Bearbeitung lösen. Das Aktivieren
der Kopplung allein verändert noch kein Bild.

## Automatische Hintergrundaufbereitung

Bei erkanntem Kontur-Zeichenpfad erhalten die bearbeitbaren
Bildschirmschoner-Hintergründe nach Ausschnitt und Drehung einen dezenten,
an Bildschirmkoordinaten gebundenen Helligkeitsverlauf. Vorschau und Export
verwenden dieselben RGB565-Pixel. Uhr, Datum und Kontur zeichnet der Player;
sie werden niemals in das Hintergrundmotiv eingebrannt.

Dies gilt für Upload, Bearbeiten, Übernehmen und beide Reset-Wege.
**Ausschnitt zurücksetzen** setzt die Geometrie zurück. **Änderung zurücksetzen**
verarbeitet die unveränderten Originalpixel erneut. Der Änderungsstatus folgt
dem Bytevergleich: Braucht ein ursprünglich unbehandeltes Bild den Verlauf,
bleibt es auch nach Reset als geändert markiert. Wiederholte Aufbereitung
dunkelt das Motiv nicht weiter ab. Die Uploadquelle bleibt während der Sitzung
erhalten; ein erneutes Öffnen kann frühere Ausschnitte nicht rekonstruieren.

Bloßes Laden installiert keinen Screensaver und verändert keine Bildpixel.
Bei älteren unterstützten Images bietet der Editor ausdrücklich
**Bildschirmschoner nachrüsten/aktualisieren** an. Erst die Auswahl dieser
Funktion übernimmt v7 in die Arbeitskopie. Bereits aktueller Code wird erkannt
und nicht erneut gepatcht; Hello X6 bleibt bei einem Upgrade erhalten.
Bei Hintergrundänderungen ohne passenden Zeichner weist der Editor auf das
optionale Upgrade hin. Unbekannter Code wird nicht gepatcht.
Startbild, frühes BREC-Startbild und Ausschaltbild erhalten keinen Verlauf.

Das versionierte Profil liegt in `tools/x6_artwork.json` (Paket:
`profiles/x6_artwork.json`). Python und JavaScript teilen Testvektoren:
oberer Smoothstep `(0,255) → (78,210) → (190,255)`, für Hintergrund 1 um
49 Pixel verschoben; unten `(225,255) → (319,225)`. Ganzzahlige Berechnung,
Abrunden erst nach Smoothstep, RGB565-Kanalbegrenzung ohne erneute Abdunklung.
Code-/Font-Hashes erkennen die unterstützte Firmware unabhängig von Bildern.

## LFI-Größe und Nullpadding

Beim Import, Export und Vorbereiten eines Schreibauftrags wird auch die
Größenregel des Bootloaders geprüft: Verzeichnisbereich plus zugeordnete
Dateisektoren müssen die gesamte LFI-Größe ergeben. Unzugeordnete Lücken hinter
Dateien werden der vorhergehenden Datei zugerechnet und mit Nullbytes gefüllt;
Dateilängen und Prüfsummen werden entsprechend erneuert. Bei einem gültigen
v7-Katalog wird auch dessen gespeicherte Dateigröße angepasst, falls COMMON.STY
zusätzliches Padding erhält. Bilder, Programmcode und Quelldatei bleiben erhalten.
Reparaturen werden im Protokoll bzw.
Paketmanifest dokumentiert. Bereits gültige Images bleiben bytegleich.

Das gilt auch nach späteren Ergänzungen oder Löschungen von Ressourcen.
Beschädigte Prüfsummen, überlappende Dateien oder ungültige Adressen werden
abgewiesen: Daraus lässt sich der beabsichtigte Inhalt nicht rekonstruieren.
Gespeicherte Gerätedumps bleiben unveränderte Messdaten.

## Speichern

**Geänderte BINs speichern** exportiert genau die geänderten Firmwarebereiche.
Bei einem Bereich wird ein neuer Dateiname gewählt, bei BREC + LFI ein Zielordner
für `x6-bilder-brec.bin` und `x6-bilder-lfi.bin`. Vorhandene Dateien werden nicht
überschrieben; gegebenenfalls einen neuen Namen oder leeren Zielordner wählen.

Wenn MBREC, BREC und LFI als vollständige Arbeitsgrundlage vorliegen, bietet
**Firmwarepaket speichern** zusätzlich ein kompatibles `.x6backup.zip` an:
MBREC unverändert, zwei konsistente BREC-Kopien, bearbeitetes LFI und aktualisierte
Größen/Hashes im Manifest. Das Manifest kennzeichnet es als **edited-firmware**.
Dieses Paket ist eine bearbeitete Firmware, kein Ersatz für die Originalsicherung.

Gespeicherte Dateien werden erneut gelesen und geprüft. Sie können später wieder
im Editor geöffnet werden. Auch nach dem Speichern bleibt **Zurücksetzen** auf
die zu Beginn geladene Arbeitsgrundlage bezogen. Die Rückfrage beim Öffnen anderer
Dateien oder Schließen schützt noch nicht gespeicherte Änderungen.

## Änderungen flashen

1. Player ausdrücklich verbinden.
2. **Änderungen flashen** wählen. Die Bestätigung nennt die Bildplätze und
   genau die betroffenen Bereiche: LFI, BREC oder BREC + LFI.
3. Mit **Vor dem Flashen sichern** die Rücksicherung wählen. Die Checkbox ist
   mit der Hauptansicht synchronisiert und zunächst aktiviert. Abgewählt entfällt
   der vollständige Vorabdump und der Speicherdialog; Geräteprüfungen bleiben aktiv.
4. Schreiben, vollständiges Rücklesen und Neustart abwarten.

Der Editor schreibt **kein MBREC**. Für BREC werden beide Kopien zurückgelesen.
Er verwendet den vorhandenen Komponenten-Flasher einschließlich der getrennten
BREC-Übertragungsvorbereitung. Gespeicherte BREC-BINs sind normale, fertige Images,
keine für den NAND-Treiber vorab veränderten Transportdateien.

Bildänderungen betreffen die ausgewählten Pixelbereiche und Prüfsummen;
ein ausdrücklich gewähltes Screensaver-Upgrade ergänzt dessen Code und Katalog.
Eine nötige Padding-Korrektur aktualisiert außerdem die LFI-Dateilängen.
Alle vorhandenen Dateiprüfsummen werden geprüft (im aktuellen Kandidaten 148).
Ein unbestätigter
Schreibbefehl stoppt ohne automatische Wiederholung. Neustart erst nach
erfolgreichem Rücklesen. Browser und Windows-App teilen die bestehende Gerätesperre.

## Prüfstand

**Auf Hardware unverifizierter Build-Kandidat.** Die neue Funktion wurde mit
synthetischen Dateien, vorhandenen lokalen Images, simulierten Geräteantworten
und echten Browser-/Fenstertests geprüft. Während Entwicklung und Prüfung fand
kein Zugriff auf den physischen X6 statt.

Der gemeinsame Katalog liegt in `tools/x6_artwork.json`. Python und JavaScript
prüfen das vorhandene Layout statt einen bestimmten Originalbild-Hash zu verlangen.
STY verwendet RGB565 Little Endian, BREC Big Endian. Die Bilddaten bleiben lokal;
weder der Helfer noch ein Server empfängt hochgeladene Bilder.
