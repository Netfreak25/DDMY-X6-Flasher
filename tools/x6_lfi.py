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


def lfi_entries(image: bytes) -> dict[str, LfiEntry]:
    """Validate the LFI header, directory and every file checksum."""
    if len(image) < LFI_DIRECTORY_END or u32(image, 0) != LFI_MAGIC:
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
        base = raw_name[:8].rstrip(b" ").decode("ascii")
        ext = raw_name[8:].rstrip(b" ").decode("ascii")
        name = base + ("." + ext if ext else "")
        offset = u32(image, pos + 0x10) * SECTOR_SIZE
        length = u32(image, pos + 0x14)
        if name in result or offset + length > len(image):
            raise ValueError(f"invalid LFI entry {name}")
        if word_sum(image[offset:offset + length], 4) != u32(image, pos + 0x1C):
            raise ValueError(f"LFI file checksum mismatch: {name}")
        result[name] = LfiEntry(name, pos, offset, length)
    return result
