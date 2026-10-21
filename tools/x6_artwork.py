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
import x6_screensaver as screensaver
import x6_storage as storage
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
            table, count = storage.bitmap_table(raw)
            table_end = table + count * sty["record_size"]
            if slot["index"] >= count or table_end > len(raw):
                raise ValueError("Bildtabelle ist unvollständig.")
            ranges = []
            for index in range(count):
                pos = table + index * sty["record_size"]
                offset, size = struct.unpack_from("<II", raw, pos)
                if size and (offset < sty["table_offset"] or offset + size > len(raw)
                             or offset < table_end and table < offset + size):
                    raise ValueError("Bildbereich liegt außerhalb der STY-Datei.")
                ranges.append((offset, offset + size))
            pos = table + slot["index"] * sty["record_size"]
            offset, size, fmt, width, height = struct.unpack_from("<IIHHH", raw, pos)
            if (size, fmt, width, height) != (config["pixel_bytes"], sty["format"], config["width"], config["height"]):
                raise ValueError(f"Unbekanntes Bildformat: {slot['id']}")
            if any(index != slot["index"] and start < offset + size and end > offset for index, (start, end) in enumerate(ranges)):
                raise ValueError("Der Bildplatz überlappt eine andere Ressource.")
            start = entry.data_offset + offset
            if slot["file"] == "COMMON.STY" and slot["index"] == config["screensaver"]["background_base"]:
                slot = {**slot, "fixed_black": True}
            result[slot["id"]] = PixelRange(start, start + size, slot)
        managed = storage.inspect_storage(data, profile)
        if managed["supported"]:
            for background in managed["backgrounds"][managed["standard_count"]:]:
                key = background["id"]
                slot = {"id": key, "label": f"Zusatzbild {background['index'] - managed['standard_count'] + 1}",
                        "area": "lfi", "file": "COMMON.STY", "endian": "little",
                        "background_index": background["index"], "custom": True}
                start = background["absolute_offset"]
                result[key] = PixelRange(start, start + background["size"], slot)
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
    """Immutable originals plus prepared replacements; opening never changes pixels."""

    def __init__(self, selection: images.Selection, profile: tk.Profile):
        self.profile, self.selection, self.config = profile, selection, catalog()
        self.normalization = tuple(getattr(selection, "normalization", ()))
        self.originals = {area: path.read_bytes() for area, path in selection.files.items()}
        for area, data in self.originals.items():
            if hashlib.sha256(data).hexdigest() != selection.hashes[area]:
                raise ValueError("Die Arbeitsgrundlage wurde geändert.")
        self.ranges = locate(self.originals, profile, self.config)
        if not self.ranges:
            raise ValueError("LFI oder BREC mit Bildern fehlt.")
        self.edits: dict[str, bytes] = {}
        self.coupled = all(key in self.ranges for key in self.config["startup_ids"])
        data = self.originals.get("lfi")
        self.storage = storage.inspect_storage(data, profile) if data else {"supported": False, "capacity": 0, "free_bytes": 0}
        self.contour = self.storage["supported"]
        self.patch_state = "disabled"
        self.patched_lfi: bytes | None = None
        self.working_lfi: bytes | None = None
        self.original_pixels = {key: self.pixels(key) for key in self.ranges}

    @property
    def has_changes(self) -> bool:
        return bool(self.edits) or self.working_lfi is not None or bool(self.normalization)

    def apply_screensaver(self) -> None:
        """Code upgrades are deliberately unavailable while the device bug is open."""
        raise ValueError("Screensaver-Nachrüstung ist in dieser Version deaktiviert.")

    def targets(self, key: str) -> list[str]:
        if key not in self.ranges:
            raise ValueError("Dieser Bildplatz ist nicht geladen.")
        if self.ranges[key].slot.get("fixed_black"):
            raise ValueError("Schwarz ist der feste erste Screensaver-Platz.")
        return self.config["startup_ids"] if self.coupled and key in self.config["startup_ids"] else [key]

    def pixels(self, key: str, original: bool = False) -> bytes:
        item = self.ranges[key]
        if original:
            return self.original_pixels[key]
        if not original and key in self.edits:
            return self.edits[key]
        source = self.originals[item.slot["area"]]
        if item.slot["area"] == "lfi" and self.working_lfi is not None:
            source = self.working_lfi
        return source[item.start:item.end]

    def rgb(self, key: str, original: bool = False) -> bytes:
        return decode_rgb565(self.pixels(key, original), self.ranges[key].slot["endian"])

    def prepare_pixels(self, key: str, raw: bytes) -> bytes:
        """Treat only supported screensaver slots; starts and shutdown stay exact."""
        slot = self.ranges[key].slot
        profile = self.config.get("screensaver")
        index = slot.get("index", -1) - (profile or {}).get("background_base", 0) + 1
        if self.contour and slot.get("file") == "COMMON.STY":
            if slot.get("custom"):
                index = slot["background_index"] + 1
            if index >= 1:
                return screensaver.process(raw, profile, index)
        return raw

    def prepare_new(self, rgb: bytes) -> bytes:
        """New motifs use the same unshifted, idempotent import treatment."""
        if len(rgb) != self.config["width"] * self.config["height"] * 3:
            raise ValueError("Das Bild muss genau 240 × 320 RGB-Pixel enthalten.")
        return screensaver.process(encode_rgb565(rgb, "little"), self.config["screensaver"], 2)

    def add_background(self, rgb: bytes) -> str:
        """Commit one validated structural edit only after the repack succeeds."""
        raw = self.prepare_new(rgb)
        data = storage.add_background(self.working_lfi or self.originals.get("lfi"), raw, self.profile)
        ranges = locate({**self.originals, "lfi": data}, self.profile, self.config)
        report = storage.inspect_storage(data, self.profile)
        key = report["backgrounds"][-1]["id"]
        self.working_lfi, self.ranges, self.storage = data, ranges, report
        self.original_pixels[key] = raw
        return key

    def delete_background(self, key: str) -> dict[str, str]:
        """Return ID remapping so upload sources stay with their image after compaction."""
        slot = self.ranges[key].slot
        if not slot.get("custom"):
            raise ValueError("Standardbilder und Schwarz können nicht gelöscht werden.")
        index = slot["background_index"]
        data = storage.delete_background(self.working_lfi or self.originals["lfi"], index, self.profile)
        ranges = locate({**self.originals, "lfi": data}, self.profile, self.config)
        report = storage.inspect_storage(data, self.profile)
        remap = {}
        for old, item in self.ranges.items():
            if old == key:
                continue
            position = item.slot.get("background_index", -1)
            remap[old] = f"background-{position:02d}" if item.slot.get("custom") and position > index else old
        originals = {remap[old]: raw for old, raw in self.original_pixels.items() if old in remap}
        edits = {remap[old]: raw for old, raw in self.edits.items() if old in remap}
        self.working_lfi, self.ranges, self.storage = data, ranges, report
        self.original_pixels, self.edits = originals, edits
        if data == self.originals["lfi"]:
            self.working_lfi = None
        return remap

    def prepare(self, key: str, rgb: bytes) -> bytes:
        """One crop -> native pixels pipeline for both preview and Apply."""
        if len(rgb) != self.config["width"] * self.config["height"] * 3:
            raise ValueError("Das Bild muss genau 240 × 320 RGB-Pixel enthalten.")
        return self.prepare_pixels(key, encode_rgb565(rgb, self.ranges[key].slot["endian"]))

    def preview_rgb(self, key: str, rgb: bytes) -> bytes:
        return decode_rgb565(self.prepare(key, rgb), self.ranges[key].slot["endian"])

    def store(self, key: str, data: bytes) -> None:
        """Determine change state by the actual exported bytes."""
        if data == self.pixels(key, original=True):
            self.edits.pop(key, None)
        else:
            self.edits[key] = data

    def replace(self, key: str, rgb: bytes) -> None:
        if len(rgb) != self.config["width"] * self.config["height"] * 3:
            raise ValueError("Das Bild muss genau 240 × 320 RGB-Pixel enthalten.")
        for target in self.targets(key):
            self.store(target, self.prepare(target, rgb))

    def reset(self, key: str) -> None:
        for target in self.targets(key):
            self.store(target, self.prepare_pixels(target, self.pixels(target, original=True)))

    @property
    def changed_areas(self) -> tuple[str, ...]:
        return tuple(area for area in ("brec", "lfi") if (area == "lfi" and (self.working_lfi is not None or self.normalization))
                     or any(self.ranges[key].slot["area"] == area for key in self.edits))

    def build(self) -> dict[str, bytes]:
        """Patch pixels and affected checksums only, then independently validate."""
        output = dict(self.originals)
        if self.working_lfi is not None:
            output["lfi"] = self.working_lfi
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
        if "lfi" in output:
            output["lfi"] = lfi.normalize_lfi(output["lfi"])
        locate(output, self.profile, self.config)
        return output

    def stage(self, directory: Path) -> images.Selection:
        """Create a checked selection containing precisely the changed components."""
        if not self.has_changes:
            raise ValueError("Keine Änderungen vorbereitet.")
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
        if set(self.originals) != set(images.AREA_FILES) or not self.has_changes:
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
        if self.working_lfi is not None:
            manifest.update(artwork_storage_changed=True, hardware_verified=False)
        if self.normalization:
            manifest["lfi_normalization"] = {"changes": list(self.normalization)}
        if self.selection.serial:
            manifest["device_serial"] = self.selection.serial
        tk.write_manifest(staging, manifest)
        tk.verify_bundle(staging, self.profile)
        # Reserve the user output exclusively before the archive writer copies it.
        destination.touch(exist_ok=False)
        archive.export_backup(staging, destination)
        archive.import_backup(destination, staging / "reopened", self.profile)
