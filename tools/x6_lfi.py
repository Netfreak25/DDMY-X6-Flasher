"""Independent LFI checksum parser extracted from the original X6 toolkit."""

from __future__ import annotations

import struct
import sys
from array import array
from dataclasses import dataclass

LFI_MAGIC = 0x0FF0AA55
LFI_DIRECTORY_START = 0x200
LFI_DIRECTORY_END = 0x2000
LFI_ENTRY_SIZE = 0x20
SECTOR_SIZE = 512
X6_FILE_COUNT = 148
CATALOG_OFFSET = 0x40
CATALOG_TABLE = 0x50
CATALOG_END = 0x400
CATALOG_IMAGE_BYTES = 240 * 320 * 2

def u16(data: bytes, offset: int) -> int:
    """Read a little-endian unsigned 16-bit value."""
    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes, offset: int) -> int:
    """Read a little-endian unsigned 32-bit value."""
    return struct.unpack_from("<I", data, offset)[0]


def word_sum(data: bytes, width: int) -> int:
    """Compute the vendor's additive checksum over little-endian words."""
    if len(data) % width:
        raise ValueError(f"checksum input is not aligned to {width} bytes")
    words = array("H" if width == 2 else "I")
    words.frombytes(data)
    if sys.byteorder != "little":
        words.byteswap()
    return sum(words) & ((1 << (width * 8)) - 1)


def align4(value: int) -> int:
    """Return the next four-byte-aligned offset."""
    return (value + 3) & ~3


@dataclass(frozen=True)
class LfiEntry:
    """One file in the LFI directory."""

    name: str
    directory_offset: int
    data_offset: int
    length: int


def _entries(image: bytes) -> dict[str, LfiEntry]:
    """Read authenticated entries; allocation completeness is checked separately."""
    if len(image) < LFI_DIRECTORY_END or len(image) % SECTOR_SIZE or u32(image, 0) != LFI_MAGIC:
        raise ValueError("missing LFI magic or truncated directory")
    if word_sum(image[:0x1FE], 2) != u16(image, 0x1FE):
        raise ValueError("LFI header checksum mismatch")
    if word_sum(image[LFI_DIRECTORY_START:LFI_DIRECTORY_END], 4) != u32(image, 0x10):
        raise ValueError("LFI directory checksum mismatch")

    result: dict[str, LfiEntry] = {}
    for pos in range(LFI_DIRECTORY_START, LFI_DIRECTORY_END, LFI_ENTRY_SIZE):
        if image[pos] == 0:
            break
        raw_name = image[pos:pos + 11]
        if any(value < 32 or value > 126 for value in raw_name):
            raise ValueError("invalid LFI filename")
        base = raw_name[:8].rstrip(b" ").decode("ascii")
        ext = raw_name[8:].rstrip(b" ").decode("ascii")
        name = base + ("." + ext if ext else "")
        offset = u32(image, pos + 0x10) * SECTOR_SIZE
        length = u32(image, pos + 0x14)
        if not base or name in result or offset < LFI_DIRECTORY_END or not length or length % 4 or offset + length > len(image):
            raise ValueError(f"invalid LFI entry {name}")
        if word_sum(image[offset:offset + length], 4) != u32(image, pos + 0x1C):
            raise ValueError(f"LFI file checksum mismatch: {name}")
        result[name] = LfiEntry(name, pos, offset, length)
    if not result:
        raise ValueError("empty LFI directory")
    return result


def _allocations(entries: dict[str, LfiEntry], size: int):
    """Yield disjoint physical allocations, including each file's trailing space."""
    ordered = sorted(entries.values(), key=lambda entry: entry.data_offset)
    if ordered[0].data_offset != LFI_DIRECTORY_END:
        raise ValueError("unassigned space before first LFI file")
    for index, entry in enumerate(ordered):
        end = ordered[index + 1].data_offset if index + 1 < len(ordered) else size
        if entry.data_offset + entry.length > end:
            raise ValueError(f"overlapping LFI file: {entry.name}")
        yield entry, end


def lfi_entries(image: bytes) -> dict[str, LfiEntry]:
    """Validate checksums and the bootloader's complete sector allocation."""
    entries = _entries(image)
    for entry, end in _allocations(entries, len(image)):
        if entry.data_offset + entry.length != end or entry.length % SECTOR_SIZE:
            raise ValueError(f"LFI size mismatch: unassigned padding after {entry.name}")
    # The original BREC sums length >> 9, not byte lengths rounded upwards.
    sectors = LFI_DIRECTORY_END // SECTOR_SIZE + sum(entry.length // SECTOR_SIZE for entry in entries.values())
    if sectors != len(image) // SECTOR_SIZE:
        raise ValueError("LFI bootloader size mismatch")
    return entries


def _extend_catalog(image: bytearray, entry: LfiEntry, length: int) -> bool:
    """Keep a recognized COMMON catalog's size bound in sync with its allocation."""
    start = entry.data_offset
    if entry.name != "COMMON.STY" or entry.length < CATALOG_END or u32(image, start) != 0x19335552:
        return False
    magic, version, count, old_size, flags = struct.unpack_from("<4sHHII", image, start + CATALOG_OFFSET)
    if magic != b"X6SC" or version != 1 or old_size != entry.length or flags or not 1 <= count <= 236:
        return False
    offsets = struct.unpack_from(f"<{count}I", image, start + CATALOG_TABLE)
    if offsets[0] or any(image[start + CATALOG_TABLE + count * 4:start + CATALOG_END]):
        return False
    previous_end = CATALOG_END
    for offset in sorted(offsets[1:]):
        if offset % 2 or offset < previous_end or offset + CATALOG_IMAGE_BYTES > old_size:
            return False
        previous_end = offset + CATALOG_IMAGE_BYTES
    struct.pack_into("<I", image, start + CATALOG_OFFSET + 8, length)
    return True


def normalize_lfi(image: bytes, changes: list[dict] | None = None) -> bytes:
    """Zero unassigned padding and assign it to the preceding file allocation.

    The input must already have valid checksums and disjoint named payloads.
    Only bytes outside every named file are padding. Callers retain the original
    import separately and record the reported changes, including cleared bytes.
    """
    entries = _entries(image)
    output = bytearray(image)
    changed = False
    for entry, end in _allocations(entries, len(image)):
        start = entry.data_offset + entry.length
        if start == end:
            continue
        cleared = sum(value != 0 for value in image[start:end])
        output[start:end] = bytes(end - start)
        length = end - entry.data_offset
        catalog_updated = _extend_catalog(output, entry, length)
        struct.pack_into("<I", output, entry.directory_offset + 0x14, length)
        struct.pack_into("<I", output, entry.directory_offset + 0x1C,
                         word_sum(output[entry.data_offset:end], 4))
        changed = True
        if changes is not None:
            changes.append({"file": entry.name, "offset": start, "padding_bytes": end - start,
                            "cleared_nonzero_bytes": cleared, "length_before": entry.length,
                            "length_after": length, "catalog_size_updated": catalog_updated})
    if changed:
        struct.pack_into("<I", output, 0x10, word_sum(output[LFI_DIRECTORY_START:LFI_DIRECTORY_END], 4))
        struct.pack_into("<H", output, 0x1FE, word_sum(output[:0x1FE], 2))
    result = bytes(output) if changed else image
    lfi_entries(result)
    return result
