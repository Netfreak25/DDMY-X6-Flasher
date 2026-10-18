"""Send the X6's documented ADFU switch through Windows USBSTOR.

Only data-in SCSI commands are used. The target volume is supplied explicitly;
the standard INQUIRY response is checked before any vendor command is sent.
"""

from __future__ import annotations

import argparse
import ctypes
import re
from ctypes import wintypes

IOCTL_SCSI_PASS_THROUGH = 0x4D004
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
SCSI_IOCTL_DATA_IN = 1
SENSE_LENGTH = 32
COMMAND_TIMEOUT_SECONDS = 10
INQUIRY_LENGTH = 36
VENDOR_SIGNATURE = b"ACTIONSUSBD"
REBOOT_REPLY = b"\xff\x00"


class ScsiPassThrough(ctypes.Structure):
    """Windows SCSI_PASS_THROUGH with the native pointer-sized offset."""

    _fields_ = [
        ("Length", wintypes.USHORT),
        ("ScsiStatus", wintypes.BYTE),
        ("PathId", wintypes.BYTE),
        ("TargetId", wintypes.BYTE),
        ("Lun", wintypes.BYTE),
        ("CdbLength", wintypes.BYTE),
        ("SenseInfoLength", wintypes.BYTE),
        ("DataIn", wintypes.BYTE),
        ("DataTransferLength", wintypes.ULONG),
        ("TimeOutValue", wintypes.ULONG),
        ("DataBufferOffset", ctypes.c_size_t),
        ("SenseInfoOffset", wintypes.ULONG),
        ("Cdb", wintypes.BYTE * 16),
    ]


def make_cdb(opcode: int, response_length: int) -> bytes:
    """Build the same 16-byte vendor CDB as actions_flash adfu_reboot."""
    cdb = bytearray(16)
    cdb[0] = opcode
    cdb[7] = response_length
    if opcode == 0xCB:
        cdb[1] = 0x21
    return bytes(cdb)


def send_data_in(handle: int, cdb: bytes, response_length: int) -> bytes:
    """Submit a buffered SCSI data-in request and reject device errors."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.DeviceIoControl.restype = wintypes.BOOL
    sense_offset = ctypes.sizeof(ScsiPassThrough)
    data_offset = (sense_offset + SENSE_LENGTH + 7) & ~7
    packet = (ctypes.c_ubyte * (data_offset + response_length))()
    request = ScsiPassThrough.from_buffer(packet)
    request.Length = sense_offset
    request.CdbLength = len(cdb)
    request.SenseInfoLength = SENSE_LENGTH
    request.DataIn = SCSI_IOCTL_DATA_IN
    request.DataTransferLength = response_length
    request.TimeOutValue = COMMAND_TIMEOUT_SECONDS
    request.DataBufferOffset = data_offset
    request.SenseInfoOffset = sense_offset
    request.Cdb[: len(cdb)] = cdb
    returned = wintypes.DWORD()
    ok = kernel32.DeviceIoControl(
        handle,
        IOCTL_SCSI_PASS_THROUGH,
        packet,
        len(packet),
        packet,
        len(packet),
        ctypes.byref(returned),
        None,
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    if request.ScsiStatus:
        sense = bytes(packet[sense_offset : sense_offset + request.SenseInfoLength])
        raise OSError(f"SCSI status {request.ScsiStatus:#x}; sense={sense.hex()}")
    if request.DataTransferLength != response_length:
        raise OSError(
            f"SCSI short response: {request.DataTransferLength}/{response_length} bytes"
        )
    return bytes(packet[data_offset : data_offset + response_length])


def open_volume(drive: str) -> tuple[int, object]:
    """Open only the explicitly named Windows volume for SCSI pass-through."""
    if not re.fullmatch(r"[A-Za-z]:", drive):
        raise ValueError("--drive must be one drive letter followed by a colon")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(
        rf"\\.\{drive.upper()}",
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle, kernel32


def inquiry(handle: int) -> bytes:
    """Read the standard SCSI identity before vendor commands."""
    return send_data_in(handle, bytes([0x12, 0, 0, 0, INQUIRY_LENGTH, 0]), INQUIRY_LENGTH)


def reboot_to_adfu(drive: str) -> None:
    """Request the X6's vendor ADFU reboot on a previously identified volume."""
    handle, kernel32 = open_volume(drive)
    try:
        identity = inquiry(handle)
        if identity[8:16].strip() != b"ACTIONS":
            raise OSError("selected volume does not identify as ACTIONS")
        handshake = send_data_in(handle, make_cdb(0xCC, len(VENDOR_SIGNATURE)), len(VENDOR_SIGNATURE))
        if handshake != VENDOR_SIGNATURE:
            raise OSError(f"unexpected vendor handshake: {handshake.hex()}")
        reply = send_data_in(handle, make_cdb(0xCB, len(REBOOT_REPLY)), len(REBOOT_REPLY))
        if reply != REBOOT_REPLY:
            raise OSError(f"unexpected ADFU reboot reply: {reply.hex()}")
    finally:
        kernel32.CloseHandle(handle)


def main() -> None:
    """Probe the selected X6 volume or request its built-in ADFU reboot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drive", required=True, help="verified X6 volume, e.g. I:")
    parser.add_argument("command", choices=("inquiry", "adfu-reboot"))
    args = parser.parse_args()
    if args.command == "adfu-reboot":
        reboot_to_adfu(args.drive)
        print("ADFU reboot command acknowledged")
        return
    handle, kernel32 = open_volume(args.drive)
    try:
        identity = inquiry(handle)
        print(f"inquiry={identity.hex()} ({identity[8:36].decode('ascii', 'replace').strip()})")
    finally:
        kernel32.CloseHandle(handle)


if __name__ == "__main__":
    main()
