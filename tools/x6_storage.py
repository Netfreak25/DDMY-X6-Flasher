"""Pure, bounded LFI allocation and catalog edits; never install executable code.

Only a named, entirely zero-filled X6FREE.BIN represents usable capacity. The
existing sector ownership invariant remains true after every returned change.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import x6_lfi as lfi

# PyInstaller stores profiles in tools/, while imported Python modules live at
# the extraction root. Keep the source and packaged paths explicit.
PROFILE_DIRECTORY = Path(sys._MEIPASS) / "tools" if getattr(sys, "frozen", False) else Path(__file__).parent
LAYOUT = json.loads((PROFILE_DIRECTORY / "x6_storage.json").read_text(encoding="utf-8"))
SKIN_LAYOUT = struct.Struct("<4sHHIHHIIII")
SKIN_PALETTE_HEADER_BYTES = 40
SKIN_PALETTE_BYTES = 128
SKIN_COUNT = 8
MAX_SKIN_ROLES = 512
NATIVE_BITMAP_LIMIT = 0x1000
BITMAP_TABLE_POINTER = 0x10


def bitmap_table(raw: bytes) -> tuple[int, int]:
    """Follow the native header pointer, including tables relocated after pixels."""
    if len(raw) < LAYOUT["catalog_end"] or lfi.u32(raw, 0) != LAYOUT["sty_magic"]:
        raise ValueError("Ungültige STY-Datei.")
    table, count = lfi.u32(raw, BITMAP_TABLE_POINTER), lfi.u16(raw, LAYOUT["bitmap_count_offset"])
    if table < LAYOUT["catalog_end"] or table % 4 or table + count * LAYOUT["bitmap_record"] > len(raw):
        raise ValueError("Bildtabelle ist unvollständig.")
    return table, count


def _overlap(first, second):
    return first[0] < second[1] and second[0] < first[1]


def _skin_layout(raw: bytes, extension: int, count: int) -> dict:
    """Independent v2 parser: protect metadata and immutable skin pixel aliases."""
    if extension < LAYOUT["catalog_end"] or extension % 4 or extension + SKIN_LAYOUT.size > len(raw):
        raise ValueError("Ungültiger Skin-Metadatenverweis.")
    magic, version, size, prefix, skins, roles, mapping, map_bytes, flags, reserved = SKIN_LAYOUT.unpack_from(raw, extension)
    expected_size = {1: SKIN_LAYOUT.size, 2: SKIN_PALETTE_HEADER_BYTES}.get(version)
    if (magic != b"X6SK" or size != expected_size or flags or reserved
            or skins != SKIN_COUNT or not 1 <= roles <= MAX_SKIN_ROLES or map_bytes != skins * roles * 2
            or prefix % LAYOUT["sector_size"] or extension + size > prefix
            or prefix + (count - LAYOUT["standard_count"]) * LAYOUT["pixel_bytes"] != len(raw)
            or mapping < extension + size or mapping % 2 or mapping + map_bytes > prefix):
        raise ValueError("Ungültiger Skin-Grundbereich.")
    table, bitmap_count = bitmap_table(raw)
    table_span = (table, table + bitmap_count * LAYOUT["bitmap_record"])
    protected = [(extension, extension + size), (mapping, mapping + map_bytes)]
    palette_offset = palette_bytes = 0
    if version == 2:
        palette_offset, palette_bytes = struct.unpack_from("<II", raw, extension + SKIN_LAYOUT.size)
        if (palette_bytes != SKIN_PALETTE_BYTES or palette_offset % 2
                or palette_offset < mapping + map_bytes or palette_offset + palette_bytes > prefix):
            raise ValueError("Ungültige geschützte Skin-Farbtabelle.")
        protected.append((palette_offset, palette_offset + palette_bytes))
    if bitmap_count > NATIVE_BITMAP_LIMIT or table_span[1] > prefix or any(_overlap(table_span, span) for span in protected):
        raise ValueError("Skin-Bildtabelle überlappt Metadaten oder Zusatzbilder.")
    ids = struct.unpack_from(f"<{skins * roles}H", raw, mapping)
    standard = []
    for index in range(LAYOUT["standard_count"]):
        at = table + (LAYOUT["bitmap_base"] + index) * LAYOUT["bitmap_record"]
        if at + LAYOUT["bitmap_record"] > table_span[1]:
            raise ValueError("Standardbilder fehlen im Skin-Grundbereich.")
        offset, length = struct.unpack_from("<II", raw, at)
        standard.append((offset, offset + length))
    for index in set(ids):
        if index >= bitmap_count or index >= NATIVE_BITMAP_LIMIT:
            raise ValueError("Skin-Bild-ID liegt außerhalb der nativen Tabelle.")
        at = table + index * LAYOUT["bitmap_record"]
        offset, length, fmt, width, height = struct.unpack_from("<IIHHH", raw, at)
        if (fmt != LAYOUT["bitmap_format"] or not width or not height or length != width * height * 2
                or any(_overlap((offset, offset + length), span) for span in standard)):
            raise ValueError("Skin-Bild teilt einen editierbaren Bildbereich oder hat ein falsches Format.")
    return {"prefix_bytes": prefix, "protected": protected, "skin_ids": sorted(set(ids)),
            "extension_offset": extension, "role_count": roles, "layout_version": version,
            "palette_offset": palette_offset, "palette_bytes": palette_bytes}


def _profile_value(profile, name: str) -> int:
    if profile is None:
        return LAYOUT[name]
    return profile[name] if isinstance(profile, dict) else getattr(profile, name)


def _entries(image: bytes, profile=None) -> dict:
    if len(image) != _profile_value(profile, "lfi_size") or _profile_value(profile, "sector_size") != lfi.SECTOR_SIZE:
        raise ValueError("Falsche X6-Firmwaregröße oder Sektorgröße.")
    entries = lfi.lfi_entries(image)
    reserve = entries.get(LAYOUT["reserve_name"])
    if reserve and any(image[reserve.data_offset:reserve.data_offset + reserve.length]):
        raise ValueError("X6FREE.BIN enthält Daten und ist kein freier Speicher.")
    return entries


def _payload(image: bytes, entry) -> bytes:
    return image[entry.data_offset:entry.data_offset + entry.length]


def _config_offset(raw: bytes) -> int:
    descriptor, record_size = LAYOUT["config_descriptor"], LAYOUT["config_record_size"]
    if raw[:4] != b"cfg\0" or len(raw) < descriptor + record_size:
        raise ValueError("Ungültige Screensaver-Konfiguration.")
    offset, size = struct.unpack_from("<II", raw, descriptor)
    if size != record_size or offset < descriptor + record_size or offset + size > len(raw):
        raise ValueError("Ungültiger Screensaver-Anzahleintrag.")
    return offset


def inspect_storage(image: bytes, profile=None) -> dict:
    """Inspect ownership and editable slots without changing imported bytes.

    Missing catalogs are supported for legacy fixed-slot editors only. A catalog
    bearing our signature but inconsistent metadata is an error, not free space.
    """
    entries = _entries(image, profile)
    reserve = entries.get(LAYOUT["reserve_name"])
    free = reserve.length if reserve else 0
    report = {"supported": False, "reason": "Kein unterstützter Bilderkatalog vorhanden.",
              "free_bytes": free, "capacity": 0, "count": 0,
              "standard_count": LAYOUT["standard_count"], "maximum": LAYOUT["maximum"],
              "common_size": 0, "backgrounds": [],
              "entries": [{"name": name, "offset": entry.data_offset, "size": entry.length}
                          for name, entry in entries.items()]}
    common = entries.get(LAYOUT["common_name"])
    if common is None:
        return report
    raw = _payload(image, common)
    report["common_size"] = len(raw)
    header, table, end = (LAYOUT[key] for key in ("catalog_offset", "catalog_table", "catalog_end"))
    if raw[header:header + 4] != LAYOUT["catalog_magic"].encode("ascii"):
        return report
    if len(raw) < end or lfi.u32(raw, 0) != LAYOUT["sty_magic"]:
        raise ValueError("Ungültige COMMON.STY für den Bilderkatalog.")
    _, version, count, size, flags = struct.unpack_from("<4sHHII", raw, header)
    if (version not in (LAYOUT["catalog_version"], 2) or (version == 1 and flags) or size != len(raw)
            or not LAYOUT["standard_count"] <= count <= LAYOUT["maximum"]):
        raise ValueError("Ungültiger oder unbekannter Bilderkatalog.")
    if any(raw[table + count * 4:end]):
        raise ValueError("Reservierte Katalogbytes sind nicht null.")
    offsets = list(struct.unpack_from(f"<{count}I", raw, table))
    skin = _skin_layout(raw, flags, count) if version == 2 else None
    base_size = skin["prefix_bytes"] if skin else LAYOUT["common_base_size"]
    expected_size = base_size + (count - LAYOUT["standard_count"]) * LAYOUT["pixel_bytes"]
    if len(raw) != expected_size:
        raise ValueError("Unbekannter Zusatzbereich von COMMON.STY.")
    bitmap_start, bitmap_count = bitmap_table(raw)
    table_end = bitmap_start + bitmap_count * LAYOUT["bitmap_record"]
    protected = [(bitmap_start, table_end), *(skin["protected"] if skin else [])]
    if bitmap_count < LAYOUT["bitmap_base"] + LAYOUT["standard_count"] or table_end > base_size:
        raise ValueError("Unbekannte Standard-Bildtabelle.")
    for index in range(bitmap_count):
        at = bitmap_start + index * LAYOUT["bitmap_record"]
        offset, length = struct.unpack_from("<II", raw, at)
        if length and (offset < LAYOUT["catalog_end"] or offset % 2 or offset + length > base_size
                       or any(_overlap((offset, offset + length), span) for span in protected)):
            raise ValueError("Eine Standardressource greift in den verwalteten Zusatzbereich.")
    standard_offsets = []
    for index in range(LAYOUT["standard_count"]):
        at = bitmap_start + (LAYOUT["bitmap_base"] + index) * LAYOUT["bitmap_record"]
        offset, length, fmt, width, height = struct.unpack_from("<IIHHH", raw, at)
        if ((length, fmt, width, height) != (LAYOUT["pixel_bytes"], LAYOUT["bitmap_format"], LAYOUT["width"], LAYOUT["height"])
                or offset < LAYOUT["catalog_end"] or offset % 2 or offset + length > base_size):
            raise ValueError("Unbekannter Standard-Hintergrundbereich.")
        standard_offsets.append(offset)
    expected = [0, *standard_offsets[1:]] + [base_size + index * LAYOUT["pixel_bytes"]
                                            for index in range(count - LAYOUT["standard_count"])]
    if offsets != expected:
        raise ValueError("Katalogoffsets entsprechen keinem verwalteten Bildlayout.")
    previous = LAYOUT["catalog_end"]
    for offset in sorted(standard_offsets):
        if offset < previous:
            raise ValueError("Überlappende Standard-Hintergründe.")
        previous = offset + LAYOUT["pixel_bytes"]
    config = entries.get(LAYOUT["config_name"])
    if config is None:
        raise ValueError("CONFIG.BIN fehlt zum Bilderkatalog.")
    config_raw = _payload(image, config)
    if lfi.u16(config_raw, _config_offset(config_raw)) != count:
        raise ValueError("Bilderkatalog und konfigurierte Bildanzahl widersprechen sich.")
    report.update(supported=True, reason="", count=count, catalog_version=version, prefix_bytes=base_size,
                  skin=skin,
                  capacity=min(free // LAYOUT["pixel_bytes"], LAYOUT["maximum"] - count),
                  backgrounds=[{"index": index, "id": f"background-{index + 1:02d}", "offset": offset,
                                "absolute_offset": common.data_offset + (standard_offsets[0] if index == 0 else offset),
                                "size": LAYOUT["pixel_bytes"], "standard": index < LAYOUT["standard_count"],
                                "fixed_black": index == 0}
                               for index, offset in enumerate(offsets)])
    return report


def repack(image: bytes, replacements: dict[str, bytes] | None = None, remove=(), profile=None) -> bytes:
    """Repack named resources, preserving records and accounting for every byte.

    Retained resources keep physical and directory relative order. The explicit
    reserve moves to the end. New arbitrary resources are intentionally refused.
    """
    entries = _entries(image, profile)
    replacements, removed = replacements or {}, set(remove)
    reserve_name = LAYOUT["reserve_name"]
    if reserve_name in replacements or reserve_name in removed or set(replacements) & removed:
        raise ValueError("Widersprüchliche Änderungen am verwalteten Speicher.")
    if (set(replacements) | removed) - entries.keys():
        raise ValueError("Unbekannte LFI-Datei für die Speicheränderung.")
    payloads = {name: replacements.get(name, _payload(image, entry)) for name, entry in entries.items()
                if name not in removed and name != reserve_name}
    if any(not data or len(data) % lfi.SECTOR_SIZE for data in payloads.values()):
        raise ValueError("LFI-Dateien müssen vollständig sektorweise zugeordnet bleiben.")
    free = len(image) - lfi.LFI_DIRECTORY_END - sum(map(len, payloads.values()))
    if free < 0:
        raise ValueError("Nicht genügend freier Firmware-Speicher.")
    if free:
        payloads[reserve_name] = bytes(free)
    names = [name for name in entries if name in payloads and name != reserve_name]
    if free:
        names.append(reserve_name)
    capacity = (lfi.LFI_DIRECTORY_END - lfi.LFI_DIRECTORY_START) // lfi.LFI_ENTRY_SIZE
    if len(names) >= capacity:
        raise ValueError("Das LFI-Verzeichnis hat keinen Platz für den Abschlusseintrag.")
    output = bytearray(len(image))
    output[:lfi.LFI_DIRECTORY_START] = image[:lfi.LFI_DIRECTORY_START]
    physical = [name for name in sorted(entries, key=lambda name: entries[name].data_offset)
                if name in payloads and name != reserve_name]
    if free:
        physical.append(reserve_name)
    positions, cursor = {}, lfi.LFI_DIRECTORY_END
    for name in physical:
        data = payloads[name]
        positions[name] = cursor
        output[cursor:cursor + len(data)] = data
        cursor += len(data)
    for index, name in enumerate(names):
        pos = lfi.LFI_DIRECTORY_START + index * lfi.LFI_ENTRY_SIZE
        if name in entries:
            original = entries[name].directory_offset
            output[pos:pos + lfi.LFI_ENTRY_SIZE] = image[original:original + lfi.LFI_ENTRY_SIZE]
        else:
            base, extension = name.split(".")
            output[pos:pos + 11] = (base.ljust(8) + extension.ljust(3)).encode("ascii")
        data = payloads[name]
        struct.pack_into("<II", output, pos + 0x10, positions[name] // lfi.SECTOR_SIZE, len(data))
        struct.pack_into("<I", output, pos + 0x1C, lfi.word_sum(data, 4))
    struct.pack_into("<I", output, 0x10, lfi.word_sum(output[lfi.LFI_DIRECTORY_START:lfi.LFI_DIRECTORY_END], 4))
    struct.pack_into("<H", output, 0x1FE, lfi.word_sum(output[:0x1FE], 2))
    result = bytes(output)
    _entries(result, profile)
    return result


def remove_dictionaries(image: bytes, profile=None) -> bytes:
    """Explicitly remove only the four approved dictionary names into reserve."""
    entries = _entries(image, profile)
    names = set(LAYOUT["dictionaries"]) & entries.keys()
    return repack(image, remove=names, profile=profile) if names else image


def _rewrite_backgrounds(image: bytes, extras: list[bytes], report: dict, profile) -> bytes:
    entries = _entries(image, profile)
    base_size = report["prefix_bytes"]
    common = bytearray(_payload(image, entries[LAYOUT["common_name"]])[:base_size])
    for raw in extras:
        common.extend(raw)
    offsets = [item["offset"] for item in report["backgrounds"][:LAYOUT["standard_count"]]]
    offsets += [base_size + index * LAYOUT["pixel_bytes"] for index in range(len(extras))]
    table, end = LAYOUT["catalog_table"], LAYOUT["catalog_end"]
    common[table:end] = bytes(end - table)
    struct.pack_into("<H", common, LAYOUT["catalog_offset"] + 6, len(offsets))
    struct.pack_into("<I", common, LAYOUT["catalog_offset"] + 8, len(common))
    struct.pack_into(f"<{len(offsets)}I", common, table, *offsets)
    config = bytearray(_payload(image, entries[LAYOUT["config_name"]]))
    struct.pack_into("<H", config, _config_offset(config), len(offsets))
    result = repack(image, {LAYOUT["common_name"]: bytes(common), LAYOUT["config_name"]: bytes(config)}, profile=profile)
    inspect_storage(result, profile)
    return result


def _extras(image: bytes, report: dict) -> list[bytes]:
    return [image[item["absolute_offset"]:item["absolute_offset"] + item["size"]]
            for item in report["backgrounds"][LAYOUT["standard_count"]:]]


def add_background(image: bytes, pixels: bytes, profile=None) -> bytes:
    """Append already prepared RGB565 LE pixels; processing belongs to the editor."""
    report = inspect_storage(image, profile)
    if not report["supported"]:
        raise ValueError(report["reason"])
    if len(pixels) != LAYOUT["pixel_bytes"]:
        raise ValueError("Das Hintergrundbild muss genau 240 × 320 RGB565-Pixel enthalten.")
    if not report["capacity"]:
        raise ValueError("Kein Platz für weitere Hintergrundbilder.")
    return _rewrite_backgrounds(image, [*_extras(image, report), bytes(pixels)], report, profile)


def delete_background(image: bytes, index: int, profile=None) -> bytes:
    """Remove one extra and compact only managed appended image bytes."""
    report = inspect_storage(image, profile)
    if not report["supported"]:
        raise ValueError(report["reason"])
    if not isinstance(index, int) or not LAYOUT["standard_count"] <= index < report["count"]:
        raise ValueError("Schwarz und die Standardbilder können nicht gelöscht werden.")
    extras = _extras(image, report)
    del extras[index - LAYOUT["standard_count"]]
    return _rewrite_backgrounds(image, extras, report, profile)
