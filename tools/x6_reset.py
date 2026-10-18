"""Native helper reset: only ADFUS RAM bootstrap and reset, never NAND writes.

Uses the Windows WinUSB and Configuration Manager APIs, with no extra DLLs.
The packet layout and bootstrap match lib/transport.js and lib/driver.js.
API reference: https://learn.microsoft.com/en-us/windows-hardware/drivers/usbcon/using-winusb-api-to-communicate-with-a-usb-device
Interface discovery: https://learn.microsoft.com/en-us/windows/win32/api/cfgmgr32/nf-cfgmgr32-cm_get_device_interface_listw
"""

from __future__ import annotations

import ctypes
import hashlib
import struct
import time
import uuid
import winreg
from ctypes import wintypes as wt

import x6_toolkit as toolkit

CBW_SIGNATURE = 0x43425355
CSW_SIGNATURE = 0x53425355
CSW_SIZE = 13
RECEIVE_SIZE = 1024
INFO_LENGTH = 18
INFO_SIGNATURE = bytes.fromhex("004341444655443051")
WRITE_RAM = 0x13
READ_RAM = 0x93
SWITCH = 0x20
EXECUTE = 0x21
RETURN_SIZE = 0x22
READ_RETURN = 0x23
SWITCH_DELAY_S = 0.010
PIPE_TRANSFER_TIMEOUT = 3
USB_BULK = 2
USB_VENDOR_CLASS = 0xFF
USB_IN = 0x80
GENERIC_READ_WRITE = 0xC0000000
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE = ctypes.c_void_p(-1).value
PRESENT_INTERFACES = 0
UCHAR = ctypes.c_ubyte  # wintypes.BYTE is signed in Python 3.11; USB uses unsigned bytes.


class Guid(ctypes.Structure):
    _fields_ = [("data1", wt.DWORD), ("data2", wt.WORD), ("data3", wt.WORD), ("data4", UCHAR * 8)]


class InterfaceDescriptor(ctypes.Structure):
    _pack_ = 1
    _fields_ = [(name, UCHAR) for name in (
        "length", "type", "number", "alternate", "endpoints", "usb_class", "subclass", "protocol", "string")]


class PipeInformation(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("id", UCHAR), ("packet_size", wt.USHORT), ("interval", UCHAR)]


def bind(library, name, arguments, result=wt.BOOL):
    """Declare native signatures explicitly, including pointer-sized handles."""
    function = getattr(library, name)
    function.argtypes, function.restype = arguments, result
    return function


def checked(result, operation: str) -> None:
    if not result:
        error = ctypes.WinError(ctypes.get_last_error())
        raise toolkit.ToolkitError(f"{operation}: {error}") from error


def interface_path(instance: str) -> str:
    """Resolve present WinUSB interfaces for exactly the selected PnP instance."""
    key_path = rf"SYSTEM\CurrentControlSet\Enum\{instance}\Device Parameters"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
        try:
            values, _ = winreg.QueryValueEx(key, "DeviceInterfaceGUIDs")
        except FileNotFoundError:
            values, _ = winreg.QueryValueEx(key, "DeviceInterfaceGUID")
    if isinstance(values, str):
        values = [values]
    cfg = ctypes.WinDLL("cfgmgr32", use_last_error=True)
    size = bind(cfg, "CM_Get_Device_Interface_List_SizeW",
                [ctypes.POINTER(wt.ULONG), ctypes.POINTER(Guid), wt.LPCWSTR, wt.ULONG], wt.ULONG)
    listing = bind(cfg, "CM_Get_Device_Interface_ListW",
                   [ctypes.POINTER(Guid), wt.LPCWSTR, wt.LPWSTR, wt.ULONG, wt.ULONG], wt.ULONG)
    paths = set()
    for value in values:
        guid = Guid.from_buffer_copy(uuid.UUID(value).bytes_le)
        count = wt.ULONG()
        result = size(ctypes.byref(count), ctypes.byref(guid), instance, PRESENT_INTERFACES)
        if result:
            raise toolkit.ToolkitError(f"WinUSB-Gerätepfad konnte nicht ermittelt werden (CM {result:#x}).")
        buffer = ctypes.create_unicode_buffer(max(count.value, 1))
        result = listing(ctypes.byref(guid), instance, buffer, len(buffer), PRESENT_INTERFACES)
        if result:
            raise toolkit.ToolkitError(f"WinUSB-Gerätezuordnung hat sich geändert (CM {result:#x}).")
        paths.update(path for path in buffer[:].split("\0") if path)
    if len(paths) != 1:
        raise toolkit.ToolkitError("Keine eindeutige WinUSB-Schnittstelle. WinUSB für ADFU 10D6:10D6 prüfen.")
    return paths.pop()


class WindowsUsb:
    """Exclusive, synchronous bulk transport with bounded pipe timeouts."""

    def __init__(self, instance: str, timeout_ms: int) -> None:
        self.file = None
        self.handle = wt.HANDLE()
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.usb = ctypes.WinDLL("winusb", use_last_error=True)
        bind(self.kernel, "CreateFileW", [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID,
                                         wt.DWORD, wt.DWORD, wt.HANDLE], wt.HANDLE)
        bind(self.kernel, "CloseHandle", [wt.HANDLE])
        bind(self.usb, "WinUsb_Initialize", [wt.HANDLE, ctypes.POINTER(wt.HANDLE)])
        bind(self.usb, "WinUsb_Free", [wt.HANDLE])
        bind(self.usb, "WinUsb_GetCurrentAlternateSetting", [wt.HANDLE, ctypes.POINTER(UCHAR)])
        bind(self.usb, "WinUsb_QueryInterfaceSettings", [wt.HANDLE, UCHAR, ctypes.POINTER(InterfaceDescriptor)])
        bind(self.usb, "WinUsb_QueryPipe", [wt.HANDLE, UCHAR, UCHAR, ctypes.POINTER(PipeInformation)])
        bind(self.usb, "WinUsb_SetPipePolicy", [wt.HANDLE, UCHAR, wt.ULONG, wt.ULONG, wt.LPVOID])
        for name in ("WinUsb_ReadPipe", "WinUsb_WritePipe"):
            bind(self.usb, name, [wt.HANDLE, UCHAR, wt.LPVOID, wt.ULONG, ctypes.POINTER(wt.ULONG), wt.LPVOID])
        try:
            # Share mode zero refuses access while Chrome or another app owns USB.
            self.file = self.kernel.CreateFileW(interface_path(instance), GENERIC_READ_WRITE, 0,
                                                None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
            if self.file == INVALID_HANDLE:
                self.file = None
                checked(False, "X6 kann nicht exklusiv geöffnet werden. Bitte die USB-Verbindung im Flasher trennen")
            checked(self.usb.WinUsb_Initialize(self.file, ctypes.byref(self.handle)), "WinUSB öffnen")
            self.input, self.output = self.configure_pipes(timeout_ms)
        except BaseException:
            self.close()
            raise

    def configure_pipes(self, timeout_ms: int) -> tuple[int, int]:
        alternate, descriptor = UCHAR(), InterfaceDescriptor()
        checked(self.usb.WinUsb_GetCurrentAlternateSetting(self.handle, ctypes.byref(alternate)), "USB-Einstellung lesen")
        checked(self.usb.WinUsb_QueryInterfaceSettings(self.handle, alternate, ctypes.byref(descriptor)), "USB-Schnittstelle lesen")
        if descriptor.usb_class != USB_VENDOR_CLASS:
            raise toolkit.ToolkitError("Unerwartete USB-Schnittstelle; Reset abgebrochen.")
        endpoints = []
        for index in range(descriptor.endpoints):
            pipe = PipeInformation()
            checked(self.usb.WinUsb_QueryPipe(self.handle, alternate, index, ctypes.byref(pipe)), "USB-Endpunkt lesen")
            if pipe.type == USB_BULK:
                endpoints.append(pipe.id)
        incoming = [pipe for pipe in endpoints if pipe & USB_IN]
        outgoing = [pipe for pipe in endpoints if not pipe & USB_IN]
        if len(incoming) != 1 or len(outgoing) != 1:
            raise toolkit.ToolkitError("Keine eindeutigen ADFU-Bulk-Endpunkte.")
        timeout = wt.ULONG(timeout_ms)
        for pipe in endpoints:
            checked(self.usb.WinUsb_SetPipePolicy(self.handle, pipe, PIPE_TRANSFER_TIMEOUT,
                                                ctypes.sizeof(timeout), ctypes.byref(timeout)), "USB-Zeitlimit setzen")
        return incoming[0], outgoing[0]

    def write(self, data: bytes) -> None:
        buffer, transferred = ctypes.create_string_buffer(data), wt.ULONG()
        checked(self.usb.WinUsb_WritePipe(self.handle, self.output, buffer, len(data),
                                        ctypes.byref(transferred), None), "USB senden")
        if transferred.value != len(data):
            raise toolkit.ToolkitError("USB-Schreibbefehl wurde nur teilweise übertragen; kein Wiederholungsversuch.")

    def read(self, length: int) -> bytes:
        buffer, transferred = ctypes.create_string_buffer(length), wt.ULONG()
        checked(self.usb.WinUsb_ReadPipe(self.handle, self.input, buffer, length,
                                       ctypes.byref(transferred), None), "USB empfangen")
        if not 0 < transferred.value <= length:
            raise toolkit.ToolkitError("Leere oder ungültige USB-Antwort.")
        return buffer.raw[:transferred.value]

    def close(self) -> None:
        if self.handle.value:
            self.usb.WinUsb_Free(self.handle)
            self.handle = wt.HANDLE()
        if self.file is not None:
            self.kernel.CloseHandle(self.file)
            self.file = None


class ResetSession:
    """The minimal Actions subset required for reset, with no command retries."""

    def __init__(self, usb: WindowsUsb, profile: toolkit.Profile) -> None:
        self.usb, self.profile, self.failed = usb, profile, False

    def transaction(self, cdb: bytes, incoming: int = 0, outgoing: bytes = b"") -> bytes:
        if self.failed:
            raise toolkit.ToolkitError("Die USB-Sitzung ist beendet; kein weiterer Befehl wurde gesendet.")
        try:
            deadline = time.monotonic() + self.profile.usb_timeout_ms / 1000
            packet = struct.pack("<IIIBBB", CBW_SIGNATURE, 0, incoming or len(outgoing), USB_IN if incoming else 0, 0, 16)
            self.usb.write(packet + cdb)
            if outgoing:
                self.usb.write(outgoing)
            result = bytearray()
            expected = incoming + CSW_SIZE
            while len(result) < expected:
                if time.monotonic() >= deadline:
                    raise toolkit.ToolkitError("USB-Antwort hat das Zeitlimit überschritten.")
                block = self.usb.read(RECEIVE_SIZE)
                if not block:
                    raise toolkit.ToolkitError("USB-Antwort ist leer.")
                result.extend(block)
            if len(result) != expected or struct.unpack("<IIIB", result[incoming:]) != (CSW_SIGNATURE, 0, 0, 0):
                raise toolkit.ToolkitError("USB-Bestätigung fehlt oder ist ungültig; kein Wiederholungsversuch.")
            return bytes(result[:incoming])
        except BaseException:
            self.failed = True
            raise

    def command(self, command: int, address: int, length: int = 0, outgoing: bytes = b"") -> bytes:
        cdb = struct.pack("<BIII3x", 0xCD, command, length, address)
        # READ_RAM plus the native EXEC return-size and return-data commands.
        return self.transaction(cdb, length if command in (READ_RAM, RETURN_SIZE, READ_RETURN) else 0, outgoing)

    def bootstrap(self, payload: bytes) -> None:
        """Reuse matching ADFUS RAM; only the ROM accepts the INFO command."""
        address = int(self.profile.protocol["adfus_address"], 0)
        block_size = self.profile.sector_size
        # CD/93 RAM reads are supported by ROM and ADFUS. ADFUS only accepts
        # CD/B0 CDBs (payload_arm/adfus.c); sending CC there poisons the session.
        if self.command(READ_RAM, address, block_size) == payload[:block_size]:
            return
        info_cdb = bytearray(16)
        info_cdb[0], info_cdb[7] = 0xCC, INFO_LENGTH
        identity = self.transaction(bytes(info_cdb), INFO_LENGTH)
        if not identity.startswith(INFO_SIGNATURE):
            raise toolkit.ToolkitError("Kein bestätigter ATJ2157-X6; kein Reset gesendet.")
        for offset in range(0, len(payload), block_size):
            block = payload[offset:offset + block_size]
            self.command(WRITE_RAM, address + offset, len(block), block)
        self.command(SWITCH, address)
        time.sleep(SWITCH_DELAY_S)
        if self.command(READ_RAM, address, block_size) != payload[:block_size]:
            raise toolkit.ToolkitError("ADFUS-RAM-Prüfung fehlgeschlagen; kein Reset gesendet.")

    def reset(self, payload: bytes) -> None:
        """Prepare or reuse ADFUS, then send exactly one acknowledged reset."""
        self.bootstrap(payload)
        try:
            self.transaction(bytes([0xB0]) + bytes(15))
        except (OSError, toolkit.ToolkitError) as error:
            raise toolkit.ToolkitError(f"Reset wurde nicht bestätigt. Kein automatischer Wiederholungsversuch. {error}") from error


def open_adfu(profile: toolkit.Profile, device: toolkit.Device) -> WindowsUsb:
    """Open only the selected X6 ADFU instance with exclusive WinUSB access."""
    if device.mode != "adfu" or not device.instance_id:
        raise toolkit.ToolkitError("Kein X6 im ADFU-Modus gefunden.")
    parts = device.instance_id.upper().split("\\")
    if len(parts) != 3 or parts[0] != "USB" or parts[1] != profile.adfu_usb_id.upper():
        raise toolkit.ToolkitError("Unpassende ADFU-Gerätekennung; Zugriff abgebrochen.")
    return WindowsUsb(device.instance_id, profile.usb_timeout_ms)


def leave_adfu(profile: toolkit.Profile, payload: bytes) -> str:
    """Called only by the helper button; success requires normal USB enumeration."""
    if len(payload) < profile.sector_size or hashlib.sha256(payload).hexdigest() != profile.runtime_sha256["adfus.bin"]:
        raise toolkit.ToolkitError("ADFUS-RAM-Helfer ist beschädigt; Gerät wurde nicht angesprochen.")
    device = toolkit.probe_device(profile)
    if device.mode == "normal":
        return "Der X6 ist bereits im Normalmodus."
    usb = open_adfu(profile, device)
    try:
        ResetSession(usb, profile).reset(payload)
    finally:
        usb.close()
    try:
        toolkit.wait_for_mode(profile, "normal")
    except toolkit.ToolkitError as error:
        raise toolkit.ToolkitError(f"Reset bestätigt, aber die Rückkehr in den Normalmodus wurde nicht erkannt. {error}") from error
    return "ADFU verlassen – X6 im Normalmodus erkannt."
