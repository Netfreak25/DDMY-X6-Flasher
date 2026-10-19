"""Versioned RGB565 background treatment; never modifies executable firmware."""

from __future__ import annotations

import hashlib
import struct
from itertools import pairwise

REVISION = "v4-native-contour"
MAXIMA = (31, 63, 31)


def supported(data: bytes, entries: dict, profile: dict | None) -> bool:
    """Recognize immutable code/font resources independently of picture pixels."""
    if not profile or profile.get("revision") != REVISION or not profile.get("supported_code"):
        return False
    for name, expected in profile["supported_code"].items():
        entry = entries.get(name)
        if entry is None or hashlib.sha256(data[entry.data_offset:entry.data_offset + entry.length]).hexdigest() != expected:
            return False
    return True


def curve(y: int, points: list[list[int]]) -> int:
    """Integer smoothstep; floor once, including descending segments."""
    for (start, low), (end, high) in pairwise(points):
        if start <= y <= end:
            length, position = end - start, y - start
            denominator = length ** 3
            smooth = 3 * position ** 2 * length - 2 * position ** 3
            return (low * denominator + (high - low) * smooth) // denominator
    return 255


def limits(profile: dict, slot: int) -> list[int]:
    if not 1 <= slot <= profile["background_count"]:
        raise ValueError("Unbekannter Hintergrundplatz.")
    shift = profile["first_slot_shift_y"] if slot == 1 else 0
    upper = [[y + shift, value] for y, value in profile["upper"]]
    return [min(curve(y, upper), curve(y, profile["lower"])) for y in range(profile["height"])]


def limit_pixel(word: int, ceiling: int) -> int:
    """Idempotent brightness ceiling using exact native channel ratios."""
    channels = (word >> 11, word >> 5 & 63, word & 31)
    peak, peak_max = channels[0], MAXIMA[0]
    for value, maximum in zip(channels[1:], MAXIMA[1:]):
        if value * peak_max > peak * maximum:
            peak, peak_max = value, maximum
    if peak * 255 <= ceiling * peak_max:
        return word
    numerator, denominator = ceiling * peak_max, 255 * peak
    red, green, blue = [value * numerator // denominator for value in channels]
    return red << 11 | green << 5 | blue


def process(raw: bytes, profile: dict, slot: int) -> bytes:
    """Apply after crop/rotation and RGB565 encoding, in screen coordinates."""
    width = profile["width"]
    if len(raw) != width * profile["height"] * 2:
        raise ValueError("Unvollständiges RGB565-Hintergrundbild.")
    ceiling = limits(profile, slot)
    words = [limit_pixel(word, ceiling[index // width]) for index, (word,) in enumerate(struct.iter_unpack("<H", raw))]
    return struct.pack(f"<{len(words)}H", *words)
