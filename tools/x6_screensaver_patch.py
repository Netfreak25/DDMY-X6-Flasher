"""Opt-in offline upgrade of recognized screensaver code; no device operations."""

import hashlib
import struct

import x6_lfi as lfi
import x6_screensaver as saver

LABELS = {
    "install": "Screensaver nachrüsten",
    "update": "Screensaver aktualisieren",
    "current": "Screensaver bereits aktuell",
    "unsupported": "Dieser Firmwarestand wird für den Screensaver-Patch nicht unterstützt.",
    "missing": "Für den Screensaver-Patch ein LFI laden.",
}


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _variants(recipe):
    variants = recipe["variants"]
    if recipe["schema"] == 1:
        return [{"state": state, "hashes": hashes, "target_hashes": variants["current"]}
                for state, hashes in variants.items()]
    return variants


def _match(data, entries, recipe):
    hashes = {name: _digest(data[entry.data_offset:entry.data_offset + entry.length])
              for name, entry in entries.items() if name in recipe["writes"]}
    return next((variant for variant in _variants(recipe) if hashes == variant["hashes"]), None)


def _resource(data, entries, name):
    entry = entries[name]
    return data[entry.data_offset:entry.data_offset + entry.length]


def inspect(data: bytes | None, config: dict) -> str:
    """Recognize complete versions, independently of replaceable wallpapers."""
    if data is None:
        return "missing"
    profile = config.get("screensaver", {})
    recipe = profile.get("patch", {})
    if (recipe.get("schema") not in (1, 2) or profile.get("revision") != saver.REVISION
            or len(data) != recipe.get("lfi_size")):
        return "unsupported"
    entries = lfi.lfi_entries(data)
    for name, guards in recipe["guards"].items():
        entry = entries.get(name)
        if entry is None:
            return "unsupported"
        for guard in guards:
            offset, size = guard["offset"], guard["size"]
            if offset < 0 or size <= 0 or offset + size > entry.length:
                return "unsupported"
            if _digest(data[entry.data_offset + offset:entry.data_offset + offset + size]) != guard["sha256"]:
                return "unsupported"
    variant = _match(data, entries, recipe)
    if variant is None:
        return "unsupported"
    if recipe["schema"] == 2 and variant["state"] == "current":
        try:
            offsets = saver.catalog_offsets(_resource(data, entries, "COMMON.STY"))
            native = _resource(data, entries, "CONFIG.BIN")
            count = lfi.u16(native, saver.config_count_offset(native))
        except (KeyError, ValueError):
            return "unsupported"
        if count != len(offsets):
            return "update"
    return variant["state"]


def _backgrounds(data, entry, config):
    """Validate every target rectangle before applying the optional treatment."""
    sty, profile = config["sty"], config["screensaver"]
    raw = data[entry.data_offset:entry.data_offset + entry.length]
    count = lfi.u16(raw, sty["count_offset"])
    table_end = sty["table_offset"] + count * sty["record_size"]
    if lfi.u32(raw, 0) != sty["magic"] or table_end > len(raw):
        raise ValueError("Ungültige Hintergrund-Bildtabelle.")
    records = [struct.unpack_from("<IIHHH", raw, sty["table_offset"] + i * sty["record_size"]) for i in range(count)]
    result = []
    for slot in range(1, profile["background_count"] + 1):
        index = profile["background_base"] + slot - 1
        if index >= count:
            raise ValueError("Hintergrundplatz fehlt.")
        start, size, fmt, width, height = records[index]
        if ((size, fmt, width, height) != (config["pixel_bytes"], sty["format"], config["width"], config["height"])
                or start < table_end or start + size > len(raw)
                or any(i != index and length and at < start + size and at + length > start
                       for i, (at, length, *_rest) in enumerate(records))):
            raise ValueError("Ungültiger oder überlappender Hintergrundplatz.")
        result.append((slot, entry.data_offset + start, size))
    return result


def apply(data: bytes, config: dict) -> bytes:
    """Return a checked copy; current code is a byte-identical no-op."""
    state = inspect(data, config)
    if state == "current":
        return data
    if state not in ("install", "update"):
        raise ValueError(LABELS[state])
    profile, entries = config["screensaver"], lfi.lfi_entries(data)
    recipe = profile["patch"]
    variant = _match(data, entries, recipe)
    backgrounds = _backgrounds(data, entries["COMMON.STY"], config)
    output = bytearray(data)
    for name, writes in recipe["writes"].items():
        entry = entries[name]
        end = 0
        for write in writes:
            raw = bytes.fromhex(recipe["blobs"][write["blob"]])
            offset = write["offset"]
            if _digest(raw) != write["blob"] or offset < end or offset + len(raw) > entry.length:
                raise ValueError("Ungültige Screensaver-Patchdaten.")
            end = offset + len(raw)
            output[entry.data_offset + offset:entry.data_offset + end] = raw
        if _digest(output[entry.data_offset:entry.data_offset + entry.length]) != variant["target_hashes"][name]:
            raise ValueError(f"Screensaver-Zielprüfung fehlgeschlagen: {name}")
    changed_files = {*recipe["writes"], "COMMON.STY"}
    if recipe["schema"] == 2:
        common = entries["COMMON.STY"]
        style = _resource(data, entries, "COMMON.STY")
        if any(style[saver.CATALOG_OFFSET:saver.CATALOG_LIMIT]):
            offsets = saver.catalog_offsets(style)
        else:
            black = backgrounds[0]
            if any(data[black[1]:black[1] + black[2]]):
                raise ValueError("Der feste schwarze Screensaver-Platz enthält ein Bild.")
            offsets = (0, *(start - common.data_offset for _slot, start, _size in backgrounds[1:]))
            saver.CATALOG_HEADER.pack_into(output, common.data_offset + saver.CATALOG_OFFSET,
                                           b"X6SC", 1, len(offsets), common.length, 0)
            struct.pack_into(f"<{len(offsets)}I", output, common.data_offset + saver.CATALOG_ENTRIES, *offsets)
        native = _resource(data, entries, "CONFIG.BIN")
        count_at = entries["CONFIG.BIN"].data_offset + saver.config_count_offset(native)
        struct.pack_into("<H", output, count_at, len(offsets))
        changed_files.add("CONFIG.BIN")
    for slot, start, size in backgrounds:
        output[start:start + size] = saver.process(data[start:start + size], profile, slot)
    for name in changed_files:
        entry = entries[name]
        struct.pack_into("<I", output, entry.directory_offset + 28,
                         lfi.word_sum(output[entry.data_offset:entry.data_offset + entry.length], 4))
    struct.pack_into("<I", output, 16, lfi.word_sum(output[lfi.LFI_DIRECTORY_START:lfi.LFI_DIRECTORY_END], 4))
    struct.pack_into("<H", output, 510, lfi.word_sum(output[:510], 2))
    result = lfi.normalize_lfi(bytes(output))
    if inspect(result, config) != "current":
        raise ValueError("Screensaver-Patch konnte nicht verifiziert werden.")
    return result
