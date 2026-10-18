"""Prepare a dumped X6 BREC for the pinned FWSC driver's header adjustment.

The output is a transport input, not an independently bootable BREC. Keep the
original backup: FWSC adds twice the header sum to the first checksum word.
Sending an already finalized NAND dump directly applies that adjustment again.
"""

from __future__ import annotations

import argparse
import hashlib
import struct
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from x6_toolkit import Profile

HEADER_SIZE = 512
CHECKSUM_SEED = 0x1234
WORD_MASK = 0xFFFF
CODE_CHECK_LIMIT = 0x10000
DRIVER_HEADER_START = 0x11E4C2
DRIVER_HEADER_END = 0x11E4D4
DRIVER_HEADER_CODE = bytes.fromhex("fe21201d03f09ffa6080218801eb40002080")


def word_sum(data: bytes) -> int:
    """Sum aligned little-endian halfwords modulo 16 bits."""
    return sum(value[0] for value in struct.iter_unpack("<H", data)) & WORD_MASK


def validate_brec(original: bytes, profile: Profile) -> None:
    """Reject damaged or already adjusted transfer files before selecting BREC."""
    if len(original) != profile.brec_size or original[0x80:0x85] != profile.nand_id:
        raise ValueError("not a complete BREC for the X6 NAND")
    checksum, header_sum, code_sectors, total_sectors = struct.unpack_from("<4H", original)
    if not 0 < code_sectors <= total_sectors or total_sectors * profile.sector_size != len(original):
        raise ValueError("invalid BREC sector counts")
    checked_size = min(code_sectors * profile.sector_size, CODE_CHECK_LIMIT)
    expected = (word_sum(original[2:checked_size]) + CHECKSUM_SEED) & WORD_MASK
    if checksum != expected:
        raise ValueError("invalid BREC code checksum; input may already be prepared")
    if header_sum != word_sum(original[4:HEADER_SIZE]):
        raise ValueError("invalid finalized BREC header checksum")


def prepare_transfer(original: bytes, driver: bytes, profile: Profile) -> bytes:
    """Undo the driver's checksum addition in a separate transfer copy.

    This requires a finalized X6 boot header. Keep the original for readback.
    """
    if hashlib.sha256(driver).hexdigest() != profile.runtime_sha256["fwsc.bin"]:
        raise ValueError("FWSC hash differs from the pinned X6 driver")
    base = int(profile.protocol["work_address"], 0)
    if driver[DRIVER_HEADER_START - base:DRIVER_HEADER_END - base] != DRIVER_HEADER_CODE:
        raise ValueError("FWSC BREC checksum routine differs from the audited code")
    validate_brec(original, profile)
    checksum, header_sum = struct.unpack_from("<2H", original)
    result = bytearray(original)
    struct.pack_into("<H", result, 0, (checksum - 2 * header_sum) & WORD_MASK)
    return bytes(result)


def main() -> None:
    """Write a private transfer file without modifying the original dump."""
    from x6_toolkit import Profile, private_path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path)
    parser.add_argument("driver", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    source, output = private_path(args.original), private_path(args.output)
    prepared = prepare_transfer(source.read_bytes(), args.driver.read_bytes(), Profile.load())
    with output.open("xb") as destination:
        destination.write(prepared)
    print(f"Transfer input: {output}; SHA256 {hashlib.sha256(prepared).hexdigest()}")


if __name__ == "__main__":
    main()
