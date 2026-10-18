"""Load, read back and execute our X6 RAM power-off code through native WinUSB."""

from __future__ import annotations

import hashlib
from pathlib import Path

import x6_reset as reset
import x6_toolkit as toolkit

PAYLOAD_NAME = "x6_poweroff.bin"


def load_payload(profile: toolkit.Profile, runtime: Path) -> bytes:
    """Check the exact pinned power-off program before any device access."""
    data = (runtime / PAYLOAD_NAME).read_bytes()
    expected = profile.protocol["poweroff"]
    if (len(data) != expected["size"] or not 0 < len(data) <= profile.sector_size
            or hashlib.sha256(data).hexdigest() != expected["sha256"]):
        raise toolkit.ToolkitError("Der Ausschaltcode ist beschädigt; Gerät wurde nicht angesprochen.")
    return data


def power_off(profile: toolkit.Profile, runtime: Path) -> str:
    """Verify RAM, request its loop once and stop USB access after the start ACK.

    ADFUS SWITCH acknowledges before entering the code in its main loop.
    EXECUTE would only acknowledge after the code returns, which requires
    unplugging USB. The ACK confirms the start request, not physical power loss.
    """
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
            session.command(reset.SWITCH, address)
        except (OSError, toolkit.ToolkitError) as error:
            raise toolkit.ToolkitError(
                "Ausschaltaufruf nicht bestätigt. USB kann dabei getrennt worden sein. "
                f"Kein automatischer Wiederholungsversuch. Details: {error}"
            ) from error
    finally:
        usb.close()
    return "Start bestätigt. Jetzt USB-Kabel abziehen."
