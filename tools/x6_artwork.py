"""Offline, fixed-slot artwork editing. No device commands or transport patches."""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path

import x6_archive as archive
import x6_images as images
import x6_lfi as lfi
import x6_toolkit as tk
from x6_brec_transfer import validate_brec


def catalog() -> dict:
    """Load the single catalog also used by the website."""
    return json.loads((tk.ROOT / "tools/x6_artwork.json").read_text(encoding="utf-8"))


def encode_rgb565(rgb: bytes, endian: str) -> bytes:
    """Round sRGB channels to 5/6/5 bits; never apply a transport adjustment."""
    if len(rgb) % 3 or endian not in ("little", "big"):
        raise ValueError("Ungültige RGB-Daten oder Bytefolge.")
    result = bytearray(len(rgb) // 3 * 2)
    order = "<H" if endian == "little" else ">H"
    for index in range(0, len(rgb), 3):
        r, g, b = rgb[index:index + 3]
        word = ((r * 31 + 127) // 255 << 11) | ((g * 63 + 127) // 255 << 5) | ((b * 31 + 127) // 255)
        struct.pack_into(order, result, index // 3 * 2, word)
    return bytes(result)


def decode_rgb565(data: bytes, endian: str) -> bytes:
    """Decode actual firmware pixels for previews and original comparisons."""
    if len(data) % 2 or endian not in ("little", "big"):
        raise ValueError("Ungültige RGB565-Daten.")
    result = bytearray(len(data) // 2 * 3)
    for index, (word,) in enumerate(struct.iter_unpack("<H" if endian == "little" else ">H", data)):
        result[index * 3:index * 3 + 3] = bytes(((word >> 11) * 255 // 31, (word >> 5 & 63) * 255 // 63, (word & 31) * 255 // 31))
    return bytes(result)


@dataclass(frozen=True)
class PixelRange:
    """One validated, nonoverlapping pixel range in its complete component."""

    start: int
    end: int
    slot: dict


def locate(components: dict[str, bytes], profile: tk.Profile, config: dict) -> dict[str, PixelRange]:
    """Validate complete files and resource tables before exposing any slot."""
    result = {}
    if "lfi" in components:
        data = components["lfi"]
        if len(data) != profile.lfi_size:
            raise ValueError("Falsche X6-LFI-Größe.")
        entries = lfi.lfi_entries(data)
        if len(entries) != lfi.X6_FILE_COUNT:
            raise ValueError("Das LFI muss 148 Dateien enthalten.")
        ordered = sorted(entries.values(), key=lambda item: item.data_offset)
        previous = lfi.LFI_DIRECTORY_END
        for entry in ordered:
            if entry.data_offset < previous:
                raise ValueError("Überlappende LFI-Dateien.")
            previous = entry.data_offset + entry.length
        sty = config["sty"]
        for slot in config["slots"]:
            if slot["area"] != "lfi":
                continue
            entry = entries.get(slot["file"])
            if entry is None:
                raise ValueError(f"Bildressource fehlt: {slot['file']}")
            raw = data[entry.data_offset:entry.data_offset + entry.length]
            if len(raw) < sty["table_offset"] or lfi.u32(raw, 0) != sty["magic"]:
                raise ValueError(f"Ungültige STY-Datei: {entry.name}")
            count = lfi.u16(raw, sty["count_offset"])
            table_end = sty["table_offset"] + count * sty["record_size"]
            if slot["index"] >= count or table_end > len(raw):
                raise ValueError("Bildtabelle ist unvollständig.")
            ranges = []
            for index in range(count):
                pos = sty["table_offset"] + index * sty["record_size"]
                offset, size = struct.unpack_from("<II", raw, pos)
                if size and (offset < table_end or offset + size > len(raw)):
                    raise ValueError("Bildbereich liegt außerhalb der STY-Datei.")
                ranges.append((offset, offset + size))
            pos = sty["table_offset"] + slot["index"] * sty["record_size"]
            offset, size, fmt, width, height = struct.unpack_from("<IIHHH", raw, pos)
            if (size, fmt, width, height) != (config["pixel_bytes"], sty["format"], config["width"], config["height"]):
                raise ValueError(f"Unbekanntes Bildformat: {slot['id']}")
            if any(index != slot["index"] and start < offset + size and end > offset for index, (start, end) in enumerate(ranges)):
                raise ValueError("Der Bildplatz überlappt eine andere Ressource.")
            start = entry.data_offset + offset
            result[slot["id"]] = PixelRange(start, start + size, slot)
    if "brec" in components:
        data = components["brec"]
        validate_brec(data, profile)
        code, total = struct.unpack_from("<HH", data, 4)
        start, end = code * profile.sector_size, len(data) - profile.sector_size
        if (code, total) != (config["brec"]["code_sectors"], config["brec"]["total_sectors"]):
            raise ValueError("Unbekanntes BREC-Bildlayout.")
        if (end - start != config["pixel_bytes"] or data[end:end + 4] != bytes(config["brec"]["descriptor_magic"])
                or struct.unpack_from(">HH", data, end + 4) != (config["width"], config["height"])):
            raise ValueError("Ungültiger BREC-Bilddeskriptor.")
        slot = next(item for item in config["slots"] if item["area"] == "brec")
        result[slot["id"]] = PixelRange(start, end, slot)
    return result


class Artwork:
    """Immutable originals plus pixel replacements; reset never re-encodes originals."""

    def __init__(self, selection: images.Selection, profile: tk.Profile):
        self.profile, self.selection, self.config = profile, selection, catalog()
        self.originals = {area: path.read_bytes() for area, path in selection.files.items()}
        for area, data in self.originals.items():
            if hashlib.sha256(data).hexdigest() != selection.hashes[area]:
                raise ValueError("Die Arbeitsgrundlage wurde geändert.")
        self.ranges = locate(self.originals, profile, self.config)
        if not self.ranges:
            raise ValueError("LFI oder BREC mit Bildern fehlt.")
        self.edits: dict[str, bytes] = {}
        self.coupled = all(key in self.ranges for key in self.config["startup_ids"])

    def targets(self, key: str) -> list[str]:
        if key not in self.ranges:
            raise ValueError("Dieser Bildplatz ist nicht geladen.")
        return self.config["startup_ids"] if self.coupled and key in self.config["startup_ids"] else [key]

    def pixels(self, key: str, original: bool = False) -> bytes:
        item = self.ranges[key]
        return (self.originals[item.slot["area"]][item.start:item.end]
                if original or key not in self.edits else self.edits[key])

    def rgb(self, key: str, original: bool = False) -> bytes:
        return decode_rgb565(self.pixels(key, original), self.ranges[key].slot["endian"])

    def replace(self, key: str, rgb: bytes) -> None:
        if len(rgb) != self.config["width"] * self.config["height"] * 3:
            raise ValueError("Das Bild muss genau 240 × 320 RGB-Pixel enthalten.")
        for target in self.targets(key):
            data = encode_rgb565(rgb, self.ranges[target].slot["endian"])
            if data == self.pixels(target, original=True):
                self.edits.pop(target, None)
            else:
                self.edits[target] = data

    def reset(self, key: str) -> None:
        for target in self.targets(key):
            self.edits.pop(target, None)

    @property
    def changed_areas(self) -> tuple[str, ...]:
        return tuple(area for area in ("brec", "lfi") if any(self.ranges[key].slot["area"] == area for key in self.edits))

    def build(self) -> dict[str, bytes]:
        """Patch pixels and affected checksums only, then independently validate."""
        output = dict(self.originals)
        for area in self.changed_areas:
            data = bytearray(output[area])
            changed_files = set()
            for key, pixels in self.edits.items():
                item = self.ranges[key]
                if item.slot["area"] == area:
                    data[item.start:item.end] = pixels
                    if area == "lfi":
                        changed_files.add(item.slot["file"])
            if area == "lfi":
                entries = lfi.lfi_entries(output[area])
                for name in changed_files:
                    entry = entries[name]
                    checksum = lfi.word_sum(data[entry.data_offset:entry.data_offset + entry.length], 4)
                    struct.pack_into("<I", data, entry.directory_offset + 0x1C, checksum)
                struct.pack_into("<I", data, 0x10, lfi.word_sum(data[lfi.LFI_DIRECTORY_START:lfi.LFI_DIRECTORY_END], 4))
                struct.pack_into("<H", data, 0x1FE, lfi.word_sum(data[:0x1FE], 2))
            output[area] = bytes(data)
        locate(output, self.profile, self.config)
        return output

    def stage(self, directory: Path) -> images.Selection:
        """Create a checked selection containing precisely the changed components."""
        if not self.edits:
            raise ValueError("Keine Bilder geändert.")
        output = self.build()
        directory.mkdir(parents=True, exist_ok=False)
        files = {}
        for area in self.changed_areas:
            path = directory / f"x6-bilder-{area}.bin"
            path.write_bytes(output[area])
            if images.identify(path, self.profile) != area:
                raise ValueError("Gespeicherter Firmwarebereich stimmt nicht überein.")
            if path.read_bytes() != output[area]:
                raise ValueError("Gespeicherte Firmwarebytes weichen ab.")
            files[area] = path
        return images.Selection(files, {area: tk.sha256_file(path) for area, path in files.items()}, self.selection.sources, self.selection.serial)

    def save_bins(self, destinations: dict[str, Path], staging: Path) -> list[str]:
        """Never overwrite any existing file, including the immutable source."""
        if set(destinations) != set(self.changed_areas):
            raise ValueError("Speicherziele passen nicht zu den geänderten Bereichen.")
        if len({path.resolve() for path in destinations.values()}) != len(destinations) or any(path.exists() for path in destinations.values()):
            raise ValueError("Bitte neue Dateinamen verwenden. Vorhandene Dateien werden nicht überschrieben.")
        selection = self.stage(staging)
        saved = []
        for area, target in destinations.items():
            with target.open("xb") as stream:
                stream.write(selection.files[area].read_bytes())
            tk.compare_files(selection.files[area], target)
            images.identify(target, self.profile)
            saved.append(str(target))
        return saved

    def save_package(self, destination: Path, staging: Path) -> None:
        """Export an edited schema-1 package; source MBREC stays byte-identical."""
        if set(self.originals) != set(images.AREA_FILES) or not self.edits:
            raise ValueError("Ein bearbeitetes Paket benötigt MBREC, BREC und LFI.")
        if destination.exists():
            raise ValueError("Bitte einen neuen Paketnamen verwenden.")
        output = self.build()
        staging.mkdir(parents=True, exist_ok=False)
        records = {}
        for area, names in images.AREA_FILES.items():
            for name in names:
                path = staging / name
                path.write_bytes(output[area])
                records[name] = {"size": len(output[area]), "sha256": tk.sha256_file(path)}
        manifest = {"schema": 1, "model": self.profile.model, "nand_id": self.profile.nand_id.hex(),
                    "status": "complete", "kind": "edited-firmware", "files": records,
                    "edited_images": list(self.edits), "steps": {"artwork": {"status": "ok"}}}
        if self.selection.serial:
            manifest["device_serial"] = self.selection.serial
        tk.write_manifest(staging, manifest)
        tk.verify_bundle(staging, self.profile)
        # Reserve the user output exclusively before the archive writer copies it.
        destination.touch(exist_ok=False)
        archive.export_backup(staging, destination)
        archive.import_backup(destination, staging / "reopened", self.profile)
