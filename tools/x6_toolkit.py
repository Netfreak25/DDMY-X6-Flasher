"""Windows toolkit for DDMY X6 NAND boot and firmware areas."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import x6_lfi
import x6_spti
from x6_brec_transfer import prepare_transfer

ROOT = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
PRIVATE_ROOT = ROOT / "private_dumps"
PROFILE_PATH = ROOT / "tools" / "x6_profile.json"
PROBE_PATH = ROOT / "tools" / "x6_device_probe.ps1"
DEFAULT_RUNTIME = PRIVATE_ROOT / "toolkit" / "runtime"
RUN_ROOT = PRIVATE_ROOT / "toolkit" / "runs"
BINDING_PATH = PRIVATE_ROOT / "toolkit" / "device.json"
BUNDLE_FILES = ("mbrec.bin", "mbrec_copy.bin", "brec0.bin", "brec1.bin", "lfi.bin")


class ToolkitError(RuntimeError):
    """A rejected or failed operation with an actionable explanation."""


@dataclass(frozen=True)
class Profile:
    """Geometry, USB identity and pinned helper hashes for this X6."""

    model: str
    chip: int
    normal_usb_id: str
    adfu_usb_id: str
    disk_size: int
    nand_id: bytes
    lfi_size: int
    mbrec_size: int
    brec_size: int
    sector_size: int
    transfer_size: int
    write_delay_ms: int
    usb_timeout_ms: int
    mode_timeout_s: int
    boot_write_mapping_verified: bool
    protocol: dict[str, Any]
    runtime_sha256: dict[str, str]
    bound_serial: str | None = None

    @classmethod
    def load(cls, path: Path = PROFILE_PATH) -> Profile:
        """Load the reviewed profile from one central config file."""
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["nand_id"] = bytes.fromhex(raw["nand_id"])
        if BINDING_PATH.is_file():
            binding = json.loads(BINDING_PATH.read_text(encoding="utf-8"))
            serial = binding.get("usb_serial")
            if not isinstance(serial, str) or not re.fullmatch(r"[A-Za-z0-9]+", serial):
                raise ToolkitError(f"invalid private X6 device binding: {BINDING_PATH}")
            raw["bound_serial"] = serial
        return cls(**raw)


@dataclass(frozen=True)
class Device:
    """One unambiguous X6 USB state."""

    mode: str
    serial: str | None = None
    instance_id: str | None = None
    drive: str | None = None
    disk_size: int | None = None


@dataclass(frozen=True)
class RestoreOutcome:
    """Describe what was actually verified after a full restore attempt."""

    directory: Path
    boot_verified: bool
    lfi_verified: bool
    normal_mode: bool


def sha256_file(path: Path) -> str:
    """Hash a large image without loading it all into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def private_path(path: Path) -> Path:
    """Keep private images, logs and helper binaries outside tracked files."""
    resolved = path.resolve()
    if not resolved.is_relative_to(PRIVATE_ROOT.resolve()):
        raise ToolkitError(f"path must be below {PRIVATE_ROOT}: {path}")
    return resolved


def parse_probe(raw: dict[str, Any], profile: Profile) -> Device:
    """Select the internal X6 LUN; the second ACTIONS LUN is its SD card."""
    normal = raw.get("normal") or []
    adfu = raw.get("adfu") or []
    disks = raw.get("disks") or []
    if len(normal) > 1 or len(adfu) > 1 or (normal and adfu):
        raise ToolkitError("multiple matching Actions USB devices are present")
    if adfu:
        instance = adfu[0]["instance_id"]
        if profile.adfu_usb_id.upper() not in instance.upper():
            raise ToolkitError("unexpected ADFU USB identity")
        return Device("adfu", instance.rsplit("\\", 1)[-1], instance)
    if not normal:
        return Device("missing")
    instance = normal[0]["instance_id"]
    if profile.normal_usb_id.upper() not in instance.upper():
        raise ToolkitError("unexpected normal-mode USB identity")
    serial = instance.rsplit("\\", 1)[-1]
    if profile.bound_serial and serial.upper() != profile.bound_serial.upper():
        raise ToolkitError("normal USB serial differs from the bound X6")
    matches = [
        disk for disk in disks
        if disk.get("usb_serial", "").upper() == serial.upper()
        and disk.get("disk_size") == profile.disk_size
    ]
    if len(matches) != 1 or len(matches[0].get("drives", [])) != 1:
        raise ToolkitError("X6 internal disk or drive letter is ambiguous")
    drive = matches[0]["drives"][0]
    if not re.fullmatch(r"[A-Za-z]:", drive):
        raise ToolkitError("invalid X6 drive letter from Windows")
    return Device("normal", serial, instance, drive.upper(), profile.disk_size)


def probe_device(profile: Profile) -> Device:
    """Query Windows PnP and disk associations without opening a volume."""
    if os.name != "nt":
        raise ToolkitError("USB operations require Windows")
    command = [
        "powershell.exe", "-NoProfile", "-File", str(PROBE_PATH),
        "-ExpectedDiskSize", str(profile.disk_size),
        "-NormalId", profile.normal_usb_id, "-AdfuId", profile.adfu_usb_id,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise ToolkitError("Windows device probe timed out") from exc
    if result.returncode:
        raise ToolkitError(f"Windows device probe failed: {result.stderr.strip()}")
    try:
        return parse_probe(json.loads(result.stdout), profile)
    except (ValueError, KeyError, TypeError) as exc:
        raise ToolkitError(f"invalid Windows device probe output: {exc}") from exc


def wait_for_mode(profile: Profile, mode: str, serial: str | None = None) -> Device:
    """Wait through USB re-enumeration and verify the expected destination."""
    deadline = time.monotonic() + profile.mode_timeout_s
    while time.monotonic() < deadline:
        device = probe_device(profile)
        if device.mode == mode:
            if mode == "normal" and serial and device.serial != serial:
                raise ToolkitError("a different X6 serial appeared after reset")
            return device
        time.sleep(1)
    raise ToolkitError(f"USB did not switch to {mode} within {profile.mode_timeout_s}s")


def enter_adfu(profile: Profile) -> Device:
    """Send the X6's verified Windows SCSI vendor reboot command."""
    device = probe_device(profile)
    if device.mode == "adfu":
        return device
    if device.mode != "normal" or device.drive is None:
        raise ToolkitError("X6 is not available as a normal USB volume")
    try:
        x6_spti.reboot_to_adfu(device.drive)
    except OSError as exc:
        raise ToolkitError(f"X6 ADFU reboot request failed: {exc}") from exc
    return wait_for_mode(profile, "adfu")


class RunLog:
    """Persist each step and its complete backend transcript privately."""

    def __init__(self, directory: Path) -> None:
        self.directory = private_path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.events = self.directory / "events.jsonl"

    def event(self, event_type: str, **values: object) -> None:
        """Append an event before or after a device operation."""
        entry = {"time": datetime.now(timezone.utc).isoformat(), "event": event_type, **values}
        with self.events.open("a", encoding="utf-8") as output:
            output.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def transcript(self, name: str, content: str) -> None:
        """Save one untruncated backend response."""
        (self.directory / f"{name}.log").write_text(content, encoding="utf-8")


def new_run(name: str) -> RunLog:
    """Create a unique run directory under the ignored private tree."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return RunLog(RUN_ROOT / f"{stamp}-{os.getpid()}-{name}")


class ActionsBackend:
    """Checked process wrapper around the patched local actions_dump."""

    def __init__(self, profile: Profile, runtime: Path, log: RunLog) -> None:
        self.profile = profile
        self.runtime = private_path(runtime)
        self.log = log
        for name, expected in profile.runtime_sha256.items():
            path = self.runtime / name
            if not path.is_file():
                raise ToolkitError(f"missing X6 runtime file: {path}")
            actual = sha256_file(path)
            if actual != expected:
                raise ToolkitError(f"X6 runtime hash mismatch for {name}: {actual}")

    def file(self, name: str) -> Path:
        """Resolve one hash-checked executable or RAM payload."""
        if name not in self.profile.runtime_sha256:
            raise ToolkitError(f"unlisted runtime payload: {name}")
        return self.runtime / name

    def run(self, name: str, *args: str, timeout_s: int = 90, write: bool = False) -> str:
        """Run once and stop on timeout, backend error, or USB status error."""
        command = [
            str(self.file("actions_dump.exe")),
            "chip", str(self.profile.chip),
            "timeout", str(self.profile.usb_timeout_ms), *args,
        ]
        env = os.environ.copy()
        if write:
            env["ACTIONS_FLASH_DELAY_MS"] = str(self.profile.write_delay_ms)
        self.log.event("command_start", name=name, args=list(args), write=write)
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout_s, env=env, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            self.log.event("command_timeout", name=name, timeout_s=timeout_s)
            raise ToolkitError(f"{name}: backend timed out; device state is uncertain") from exc
        output = result.stdout + result.stderr
        self.log.transcript(name, output)
        self.log.event("command_end", name=name, exit_code=result.returncode)
        failure = re.search(
            r"unexpected status|bad brec checksum|unexpected brec size|"
            r"unsupported mbrec size|(?:^|\s)(?:failed|error)(?:\s|:|$)",
            output,
            re.IGNORECASE,
        )
        if result.returncode or failure:
            raise ToolkitError(f"{name}: backend failed (exit {result.returncode}); see {self.log.directory}")
        return output


def result_bytes(output: str) -> bytes:
    """Decode one actions_dump result hexdump and check its stated size."""
    match = re.search(r"result \((\d+)\):\s*\n", output)
    if not match:
        raise ToolkitError("backend returned no result hexdump")
    result = bytearray()
    for line in output[match.end():].splitlines():
        data = line.split("|", 1)[0].strip()
        if not re.fullmatch(r"[0-9a-fA-F]{2}(?:\s+[0-9a-fA-F]{2})*", data):
            break
        result.extend(bytes.fromhex(data))
    if len(result) != int(match.group(1)):
        raise ToolkitError("backend result hexdump length mismatch")
    return bytes(result)


def start_adfus(backend: ActionsBackend) -> None:
    """Verify the ATJ2157 ROM and the first 512 loaded ADFUS bytes."""
    info = result_bytes(backend.run("adfu_info", "adfu_info"))
    if not info.startswith(b"\x00CADFUD0Q"):
        raise ToolkitError(f"unexpected ADFU ROM identity: {info.hex()}")
    address = backend.profile.protocol["adfus_address"]
    backend.run("adfus_start", "simple_switch", address, str(backend.file("adfus.bin")))
    probe = backend.log.directory / "adfus_probe.bin"
    backend.run("adfus_probe", "read_mem", address, hex(backend.profile.sector_size), str(probe))
    if probe.read_bytes() != backend.file("adfus.bin").read_bytes()[:backend.profile.sector_size]:
        raise ToolkitError("ADFUS RAM probe differs from the loaded payload")


def start_fwsc(backend: ActionsBackend) -> None:
    """Check NAND ID; load FWSC before the overlapping NAND parameter block."""
    protocol = backend.profile.protocol
    work = protocol["work_address"]
    scan = result_bytes(backend.run(
        "nand_scan", "write_mem", work, "0", "0",
        str(backend.file("nandhwsc.bin")), "exec_ret", work, protocol["scan_result_size"],
    ))
    if len(scan) < 21 or scan[:2] != b"HW" or scan[16:21] != backend.profile.nand_id:
        raise ToolkitError(f"unexpected NAND hardware scan: {scan[:32].hex()}")
    backend.log.event("nand_id", value=scan[16:21].hex())
    status = result_bytes(backend.run(
        "fwsc_start", "write_mem", work, "0", "0",
        str(backend.file("fwsc.bin")), "write_mem", protocol["nandinfo_address"], "0", "0",
        str(backend.file("nandinfo.bin")), "exec_ret", protocol["driver_entry"], "-1",
    ))
    if len(status) < 12 or status[6:12] != b"NAFWSC":
        raise ToolkitError(f"X6 flash driver rejected NAND record: {status[:32].hex()}")


def validate_lfi(path: Path, profile: Profile) -> dict[str, object]:
    """Check exact size, structure and every embedded file checksum."""
    if path.stat().st_size != profile.lfi_size:
        raise ToolkitError(f"wrong X6 LFI size: {path.stat().st_size}")
    image = path.read_bytes()
    try:
        entries = x6_lfi.lfi_entries(image)
    except ValueError as exc:
        raise ToolkitError(f"invalid X6 LFI: {exc}") from exc
    if len(entries) != x6_lfi.X6_FILE_COUNT:
        raise ToolkitError(f"wrong X6 LFI inventory: {len(entries)} files")
    return {"size": len(image), "sha256": hashlib.sha256(image).hexdigest(), "files": len(entries)}


def validate_boot(path: Path, kind: str, profile: Profile) -> dict[str, object]:
    """Check boot-record size and its X6 NAND ID where available."""
    data = path.read_bytes()
    expected = profile.mbrec_size if kind == "mbrec" else profile.brec_size
    if len(data) != expected:
        raise ToolkitError(f"{kind} has {len(data)} bytes, expected {expected}")
    if kind == "mbrec" and data[2] != 0x5A:
        raise ToolkitError("MBREC has no expected 1024-byte marker")
    if kind == "brec" and data[0x80:0x85] != profile.nand_id:
        raise ToolkitError("BREC NAND ID differs from X6 profile")
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def read_lfi(backend: ActionsBackend, path: Path, name: str) -> dict[str, object]:
    """Read and validate the complete logical firmware area."""
    backend.run(
        name, "blk_size", hex(backend.profile.transfer_size), "read_lfi",
        "0", hex(backend.profile.lfi_size), str(path), timeout_s=900,
    )
    result = validate_lfi(path, backend.profile)
    backend.log.event("file_read", name=path.name, **result)
    return result


def read_boot_copy(backend: ActionsBackend, directory: Path, index: int) -> dict[str, dict[str, object]]:
    """Read and validate one indexed BREC with its accompanying MBREC."""
    if index not in (0, 1):
        raise ToolkitError(f"invalid BREC copy index: {index}")
    mbrec_name = "mbrec.bin" if index == 0 else "mbrec_copy.bin"
    brec_name = f"brec{index}.bin"
    backend.run(
        f"read_brec{index}", "read_brec", str(backend.file("nandread.bin")),
        str(directory / mbrec_name), str(index), str(directory / brec_name),
        timeout_s=180,
    )
    records = {
        mbrec_name: validate_boot(directory / mbrec_name, "mbrec", backend.profile),
        brec_name: validate_boot(directory / brec_name, "brec", backend.profile),
    }
    for name, info in records.items():
        backend.log.event("file_read", name=name, **info)
    return records


def read_boot(backend: ActionsBackend, directory: Path) -> dict[str, dict[str, object]]:
    """Read both BREC copies and the MBREC returned with each."""
    records: dict[str, dict[str, object]] = {}
    for index in (0, 1):
        records.update(read_boot_copy(backend, directory, index))
    return records


def write_manifest(directory: Path, manifest: dict[str, object]) -> None:
    """Atomically update a private dump manifest."""
    destination = directory / "manifest.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)


def verify_bundle(directory: Path, profile: Profile) -> dict[str, Any]:
    """Validate every file in a complete X6 restore bundle."""
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolkitError(f"invalid backup manifest: {exc}") from exc
    if manifest.get("schema") != 1 or manifest.get("status") != "complete":
        raise ToolkitError("backup manifest is not complete")
    if manifest.get("model") != profile.model or manifest.get("nand_id") != profile.nand_id.hex():
        raise ToolkitError("backup does not match the X6 NAND profile")
    records = manifest.get("files", {})
    if not isinstance(records, dict) or any(name not in records for name in BUNDLE_FILES):
        raise ToolkitError("backup is missing a required boot or firmware file")
    for name in BUNDLE_FILES:
        path = directory / name
        if not path.is_file():
            raise ToolkitError(f"backup file missing: {name}")
        if not isinstance(records[name], dict):
            raise ToolkitError(f"invalid backup manifest record: {name}")
        actual = validate_lfi(path, profile) if name == "lfi.bin" else validate_boot(
            path, "mbrec" if name.startswith("mbrec") else "brec", profile,
        )
        if actual["size"] != records[name].get("size") or actual["sha256"] != records[name].get("sha256"):
            raise ToolkitError(f"backup checksum mismatch: {name}")
    if (directory / "mbrec.bin").read_bytes() != (directory / "mbrec_copy.bin").read_bytes():
        raise ToolkitError("MBREC copies in backup differ")
    if (directory / "brec0.bin").read_bytes() != (directory / "brec1.bin").read_bytes():
        raise ToolkitError("BREC copies in backup differ")
    return manifest


def compare_files(expected: Path, actual: Path) -> None:
    """Raise at the first differing byte of two complete on-disk images."""
    if expected.stat().st_size != actual.stat().st_size:
        raise ToolkitError(f"readback size differs: {actual.name}")
    offset = 0
    with expected.open("rb") as source, actual.open("rb") as readback:
        while first := source.read(1024 * 1024):
            second = readback.read(len(first))
            if len(second) != len(first):
                raise ToolkitError(f"short readback at byte {offset}: {actual.name}")
            if first != second:
                difference = next(index for index, pair in enumerate(zip(first, second)) if pair[0] != pair[1])
                raise ToolkitError(f"readback differs at byte {offset + difference}: {actual.name}")
            offset += len(first)


def check_preserved_header(candidate: Path, current: Path, profile: Profile) -> None:
    """Protect capacity and serial fields in the device-specific LFI header."""
    with candidate.open("rb") as source, current.open("rb") as device:
        candidate_header = source.read(profile.sector_size)
        current_header = device.read(profile.sector_size)
    for start, end in profile.protocol["preserve_header_ranges"]:
        if candidate_header[start:end] != current_header[start:end]:
            raise ToolkitError(f"candidate changes device-specific LFI header bytes {start:#x}:{end:#x}")


def write_area(backend: ActionsBackend, name: str, sector: str, path: Path) -> None:
    """Send every sector once and require final progress plus checked USB status."""
    if sector == backend.profile.protocol["brec_sector"]:
        try:
            prepared = prepare_transfer(path.read_bytes(), backend.file("fwsc.bin").read_bytes(), backend.profile)
        except ValueError as exc:
            raise ToolkitError(f"BREC transfer preparation failed: {exc}") from exc
        path = backend.log.directory / f"{name}-transfer.bin"
        path.write_bytes(prepared)
        backend.log.event("brec_transfer_prepared", sha256=sha256_file(path))
    size = path.stat().st_size
    if size == 0 or size % backend.profile.sector_size:
        raise ToolkitError(f"unaligned or empty flash input: {path}")
    count = size // backend.profile.sector_size
    output = backend.run(
        name, "blk_size", hex(backend.profile.transfer_size), "write_flash",
        sector, "0", "0", str(path), timeout_s=1800, write=True,
    )
    if f"write_flash: {count}/{count} sectors" not in output:
        raise ToolkitError(f"{name}: final write progress not confirmed")
    backend.log.event("write_acknowledged", area=name, bytes=size, sectors=count)


def finalize_flash(backend: ActionsBackend) -> None:
    """Send the vendor's one-sector finish marker, not an erase/fill pass."""
    marker = backend.file("vram_last.bin")
    data = marker.read_bytes()
    if data != bytes(backend.profile.sector_size - 2) + b"\xaa\xbb":
        raise ToolkitError("invalid X6 finalization marker")
    write_area(backend, "finalize", backend.profile.protocol["final_sector"], marker)


def reset_after_verify(backend: ActionsBackend, serial: str | None) -> Device:
    """Reboot only after a complete readback comparison."""
    backend.run("reset", "reset", timeout_s=45)
    return wait_for_mode(backend.profile, "normal", serial)


def flash_lfi(profile: Profile, runtime: Path, image: Path) -> Path:
    """Back up, write and byte-compare the complete LFI area."""
    candidate = private_path(image)
    candidate_info = validate_lfi(candidate, profile)
    log = new_run("flash")
    log.event("candidate", path=str(candidate), **candidate_info)
    current_device = probe_device(profile)
    serial = current_device.serial if current_device.mode == "normal" else None
    try:
        enter_adfu(profile)
        backend = ActionsBackend(profile, runtime, log)
        start_adfus(backend)
        start_fwsc(backend)
        before = log.directory / "before_lfi.bin"
        read_lfi(backend, before, "read_before")
        check_preserved_header(candidate, before, profile)
        write_area(backend, "write_lfi", profile.protocol["lfi_sector"], candidate)
        finalize_flash(backend)
        after = log.directory / "after_lfi.bin"
        read_lfi(backend, after, "read_after")
        compare_files(candidate, after)
        log.event("lfi_verified", sha256=candidate_info["sha256"])
        reset_after_verify(backend, serial)
        log.event("normal_mode_restored")
        return log.directory
    except (ToolkitError, OSError, subprocess.SubprocessError) as exc:
        log.event("flash_stopped", error=str(exc))
        raise ToolkitError(f"{exc}; run log: {log.directory}") from exc


def restore_full(profile: Profile, runtime: Path, bundle: Path, emergency: bool) -> RestoreOutcome:
    """Restore X6 boot records and LFI, tracking unresolved boot verification."""
    source = private_path(bundle)
    manifest = verify_bundle(source, profile)
    if not emergency and not profile.boot_write_mapping_verified:
        raise ToolkitError(
            "X6 MBREC/BREC write mapping is not verified; "
            "use --emergency only when recovery requires boot writes"
        )
    log = new_run("restore-full")
    log.event("restore_source", path=str(source), manifest_status=manifest["status"], emergency=emergency)
    current_device = probe_device(profile)
    serial = current_device.serial if current_device.mode == "normal" else None
    saved_serial = manifest.get("device_serial")
    if serial and saved_serial and serial.upper() != saved_serial.upper():
        raise ToolkitError("restore bundle serial differs from connected X6")
    try:
        enter_adfu(profile)
        backend = ActionsBackend(profile, runtime, log)
        start_adfus(backend)
        if not emergency:
            before_boot = log.directory / "before_boot"
            before_boot.mkdir()
            read_boot(backend, before_boot)
        start_fwsc(backend)
        if not emergency:
            read_lfi(backend, log.directory / "before_lfi.bin", "read_before")
        write_area(backend, "write_mbrec", profile.protocol["mbrec_sector"], source / "mbrec.bin")
        write_area(backend, "write_brec", profile.protocol["brec_sector"], source / "brec0.bin")
        write_area(backend, "write_lfi", profile.protocol["lfi_sector"], source / "lfi.bin")
        finalize_flash(backend)
        after_lfi = log.directory / "after_lfi.bin"
        read_lfi(backend, after_lfi, "read_after")
        compare_files(source / "lfi.bin", after_lfi)
        log.event("lfi_verified", sha256=sha256_file(after_lfi))
        boot_verified = False
        after_boot = log.directory / "after_boot"
        after_boot.mkdir()
        try:
            read_boot(backend, after_boot)
        except (ToolkitError, OSError, subprocess.SubprocessError) as exc:
            log.event("boot_readback_unverified", error=str(exc))
            if not emergency:
                raise
        else:
            for name in BUNDLE_FILES[:-1]:
                compare_files(source / name, after_boot / name)
            boot_verified = True
            log.event("boot_records_verified")
        if not boot_verified and not emergency:
            raise ToolkitError("boot records were not read back")
        if boot_verified:
            reset_after_verify(backend, serial)
            log.event("restore_verified_and_rebooted")
        else:
            log.event("restore_unverified", detail="boot record readback unavailable; no automatic reset")
        return RestoreOutcome(log.directory, boot_verified, True, boot_verified)
    except (ToolkitError, OSError, subprocess.SubprocessError) as exc:
        log.event("restore_stopped", error=str(exc))
        raise ToolkitError(f"{exc}; run log: {log.directory}") from exc


def leave_adfu(profile: Profile, runtime: Path, from_rom: bool = False) -> Device:
    """Send ADFUS reset and verify that normal USB returns."""
    device = probe_device(profile)
    if device.mode == "normal":
        return device
    if device.mode != "adfu":
        raise ToolkitError("X6 is not visible in ADFU")
    backend = ActionsBackend(profile, runtime, new_run("leave-adfu"))
    if from_rom:
        start_adfus(backend)
    backend.run("reset", "reset", timeout_s=profile.usb_timeout_ms // 1000 + 15)
    return wait_for_mode(profile, "normal")


def dump(profile: Profile, runtime: Path, destination: Path) -> Path:
    """Create a full boot-and-firmware backup or mark it incomplete."""
    target = private_path(destination)
    log = RunLog(target)
    manifest: dict[str, Any] = {
        "schema": 1, "model": profile.model, "nand_id": profile.nand_id.hex(),
        "status": "incomplete", "files": {}, "steps": {},
    }
    write_manifest(target, manifest)
    data_complete = False
    try:
        before = probe_device(profile)
        if before.mode == "normal":
            manifest["device_serial"] = before.serial
        device = enter_adfu(profile)
        manifest["device_instance"] = device.instance_id
        write_manifest(target, manifest)
        backend = ActionsBackend(profile, runtime, log)
        start_adfus(backend)
        start_fwsc(backend)
        manifest["files"]["lfi.bin"] = read_lfi(backend, target / "lfi.bin", "read_lfi")
        manifest["steps"]["lfi"] = {"status": "ok"}
        write_manifest(target, manifest)
        boot_errors = []
        for index in (0, 1):
            name = f"brec{index}"
            try:
                manifest["files"].update(read_boot_copy(backend, target, index))
            except (ToolkitError, OSError, subprocess.SubprocessError) as exc:
                boot_errors.append(f"{name}: {exc}")
                manifest["steps"][name] = {"status": "failed", "error": str(exc)}
                mbrec_name = "mbrec.bin" if index == 0 else "mbrec_copy.bin"
                mbrec_path = target / mbrec_name
                if mbrec_path.is_file():
                    try:
                        manifest["files"][mbrec_name] = validate_boot(mbrec_path, "mbrec", profile)
                    except ToolkitError:
                        pass
            else:
                manifest["steps"][name] = {"status": "ok"}
            write_manifest(target, manifest)
        if boot_errors:
            raise ToolkitError("boot read incomplete: " + "; ".join(boot_errors))
        manifest["status"] = "complete"
        write_manifest(target, manifest)
        verify_bundle(target, profile)
        data_complete = True
        log.event("dump_complete")
        reset_after_verify(backend, before.serial if before.mode == "normal" else None)
        return target
    except (ToolkitError, OSError, subprocess.SubprocessError) as exc:
        manifest["status"] = "complete" if data_complete else "incomplete"
        manifest["error"] = str(exc)
        write_manifest(target, manifest)
        log.event("dump_incomplete", error=str(exc))
        raise


def build_parser() -> argparse.ArgumentParser:
    """Declare the stable public toolkit commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime-dir", type=Path,
        default=Path(os.environ.get("X6_TOOLKIT_RUNTIME", DEFAULT_RUNTIME)),
        help="private directory with pinned Actions backend and payloads",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("enter-adfu")
    leave = sub.add_parser("leave-adfu")
    leave.add_argument("--from-rom", action="store_true", help="load ADFUS before reset")
    backup = sub.add_parser("dump")
    backup.add_argument("directory", type=Path)
    check = sub.add_parser("verify")
    check.add_argument("target", type=Path)
    firmware = sub.add_parser("flash")
    firmware.add_argument("image", type=Path)
    restore = sub.add_parser("restore-full")
    restore.add_argument("directory", type=Path)
    restore.add_argument("--emergency", action="store_true", help="allow unverified boot write path")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one command, returning nonzero when its outcome is uncertain."""
    args = build_parser().parse_args(argv)
    profile = Profile.load()
    try:
        if args.command == "status":
            print(json.dumps(probe_device(profile).__dict__, ensure_ascii=False, indent=2))
        elif args.command == "enter-adfu":
            print(f"ADFU: {enter_adfu(profile).instance_id}")
        elif args.command == "leave-adfu":
            print(f"normal: {leave_adfu(profile, args.runtime_dir, args.from_rom).instance_id}")
        elif args.command == "dump":
            print(f"complete backup: {dump(profile, args.runtime_dir, args.directory)}")
        elif args.command == "verify":
            if args.target.is_dir():
                verify_bundle(args.target, profile)
                print("complete X6 backup verified")
            else:
                print(json.dumps(validate_lfi(args.target, profile), indent=2))
        elif args.command == "flash":
            print(f"LFI written and read back: {flash_lfi(profile, args.runtime_dir, args.image)}")
        elif args.command == "restore-full":
            outcome = restore_full(profile, args.runtime_dir, args.directory, args.emergency)
            if outcome.boot_verified:
                print(f"boot records and LFI read back byte-identically: {outcome.directory}")
            else:
                print(
                    f"UNVERIFIED boot records; LFI read back, X6 left in ADFU: {outcome.directory}",
                    file=sys.stderr,
                )
                return 2
    except (ToolkitError, OSError, subprocess.SubprocessError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
