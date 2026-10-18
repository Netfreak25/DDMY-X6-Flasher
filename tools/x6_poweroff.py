"""Load, read back and execute our X6 RAM power-off code through native WinUSB."""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import x6_reset as reset
import x6_toolkit as toolkit

PAYLOAD_NAME = "x6_poweroff.bin"
REPORT_FORMAT = "<8s8I"


def load_payload(profile: toolkit.Profile, runtime: Path) -> bytes:
    """Check the exact pinned power-off program before any device access."""
    data = (runtime / PAYLOAD_NAME).read_bytes()
    expected = profile.protocol["poweroff"]
    if (len(data) != expected["size"] or not 0 < len(data) <= profile.sector_size
            or hashlib.sha256(data).hexdigest() != expected["sha256"]):
        raise toolkit.ToolkitError("Der Ausschaltcode ist beschädigt; Gerät wurde nicht angesprochen.")
    return data


def validate_report(report: bytes, profile: toolkit.Profile) -> None:
    """Verify the returned register values without claiming physical power loss."""
    config = profile.protocol["poweroff"]
    if len(report) != struct.calcsize(REPORT_FORMAT) or len(report) != config["report_size"]:
        raise toolkit.ToolkitError("Unpassende Rückmeldelänge der Ausschaltfunktion.")
    magic, *values = struct.unpack(REPORT_FORMAT, report)
    if magic != config["report_magic"].encode("ascii"):
        raise toolkit.ToolkitError("Unbekannte Rückmeldung der Ausschaltfunktion.")
    before, after = values[:4], values[4:]
    expected = [before[0] & ~3, 0x15, 0x01F00E1F, (before[3] & ~0x1C0) | 0xC0]
    if after != expected:
        raise toolkit.ToolkitError("Die Rückmeldung bestätigt die erwarteten Ausschaltwerte nicht.")


def power_off(profile: toolkit.Profile, runtime: Path) -> str:
    """Switch to ADFU if needed, verify RAM, execute once and check its report."""
    payload = load_payload(profile, runtime)
    adfus = (runtime / "adfus.bin").read_bytes()
    if len(adfus) < profile.sector_size or hashlib.sha256(adfus).hexdigest() != profile.runtime_sha256["adfus.bin"]:
        raise toolkit.ToolkitError("ADFUS-RAM-Helfer ist beschädigt; Gerät wurde nicht angesprochen.")
    usb = reset.open_adfu(profile, toolkit.enter_adfu(profile))
    try:
        session = reset.ResetSession(usb, profile)
        session.bootstrap(adfus)
        address = int(profile.protocol["work_address"], 0)
        session.command(reset.WRITE_RAM, address, len(payload), payload)
        if session.command(reset.READ_RAM, address, len(payload)) != payload:
            raise toolkit.ToolkitError("Ausschaltcode im RAM weicht ab; er wurde nicht ausgeführt.")
        try:
            session.command(reset.EXECUTE, address)
            size = struct.unpack("<I", session.command(reset.RETURN_SIZE, 0, 4))[0]
            if size != profile.protocol["poweroff"]["report_size"]:
                raise toolkit.ToolkitError("Unpassende Rückmeldelänge der Ausschaltfunktion.")
            report = session.command(reset.READ_RETURN, 0, size)
        except (OSError, toolkit.ToolkitError) as error:
            raise toolkit.ToolkitError(
                "Ausschaltaufruf nicht bestätigt. USB kann dabei getrennt worden sein. "
                f"Kein automatischer Wiederholungsversuch. Details: {error}"
            ) from error
        validate_report(report, profile)
    finally:
        usb.close()
    return "Ausschaltcode ausgeführt. USB ggf. abziehen."
