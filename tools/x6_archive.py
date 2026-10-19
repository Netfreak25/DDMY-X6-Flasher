"""Bounded, flat ZIP interchange with the WebUSB flasher and CLI backups."""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import x6_lfi
import x6_toolkit as toolkit

MANIFEST_LIMIT = 65536
COPY_BLOCK = 1024 * 1024


def limits(profile: toolkit.Profile) -> dict[str, int]:
    """Only these six names may enter a staged backup."""
    return {"manifest.json": MANIFEST_LIMIT, "lfi.bin": profile.lfi_size,
            "mbrec.bin": profile.mbrec_size, "mbrec_copy.bin": profile.mbrec_size,
            "brec0.bin": profile.brec_size, "brec1.bin": profile.brec_size}


def import_backup(source: Path, target: Path, profile: toolkit.Profile) -> dict:
    """Copy a ZIP or legacy folder into a new private folder, then validate it."""
    toolkit.private_path(target).mkdir(parents=True, exist_ok=False)
    allowed = limits(profile)
    if source.is_dir():
        for name, limit in allowed.items():
            path = source / name
            if path.is_file():
                if path.stat().st_size > limit:
                    raise toolkit.ToolkitError(f"Sicherungsdatei zu groß: {name}")
                shutil.copyfile(path, target / name)
    else:
        if source.stat().st_size > profile.lfi_size * 2:
            raise toolkit.ToolkitError("Sicherungsarchiv ist zu groß.")
        try:
            with zipfile.ZipFile(source) as archive:
                seen = set()
                for entry in archive.infolist():
                    name = entry.filename
                    if name not in allowed:
                        continue  # Never extract arbitrary or nested archive paths.
                    if name in seen or entry.file_size > allowed[name] or entry.flag_bits & 1:
                        raise toolkit.ToolkitError(f"Ungültiger oder doppelter Archiveintrag: {name}")
                    seen.add(name)
                    with archive.open(entry) as stream, (target / name).open("xb") as output:
                        data = stream.read(allowed[name] + 1)
                        if len(data) > allowed[name]:
                            raise toolkit.ToolkitError(f"Archiveintrag zu groß: {name}")
                        output.write(data)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
            raise toolkit.ToolkitError(f"Ungültiges Sicherungsarchiv: {error}") from error
    # Authenticate the imported hashes before changing any staged bytes.
    manifest = toolkit.verify_bundle(target, profile, allow_padding=True)
    changes = []
    source = (target / "lfi.bin").read_bytes()
    result = x6_lfi.normalize_lfi(source, changes)
    if changes:
        shutil.copyfile(target / "lfi.bin", target / "original-lfi.bin")
        shutil.copyfile(target / "manifest.json", target / "original-manifest.json")
        (target / "lfi.bin").write_bytes(result)
        previous = manifest["files"]["lfi.bin"]["sha256"]
        manifest["files"]["lfi.bin"] = {"size": len(result), "sha256": toolkit.sha256_file(target / "lfi.bin")}
        manifest["lfi_normalization"] = {"source_sha256": previous, "changes": changes}
        manifest.update(hardware_verified=False, kind="firmware_candidate")
        toolkit.write_manifest(target, manifest)
    return toolkit.verify_bundle(target, profile)


def stage_lfi(source: Path, target: Path, profile: toolkit.Profile) -> list[dict]:
    """Keep original import bytes beside the normalized, checksum-checked LFI."""
    if source.stat().st_size != profile.lfi_size:
        raise toolkit.ToolkitError("Falsche X6-Firmwaregröße.")
    original = source.read_bytes()
    changes = []
    try:
        result = x6_lfi.normalize_lfi(original, changes)
    except ValueError as error:
        raise toolkit.ToolkitError(f"Ungültiges X6-LFI: {error}") from error
    if changes:
        target.with_name(target.stem + ".original.bin").write_bytes(original)
        report = {"source_sha256": toolkit.sha256_file(source), "changes": changes}
        target.with_suffix(".normalization.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    target.write_bytes(result)
    toolkit.validate_lfi(target, profile)
    return changes


def save_checked(source: Path, destination: Path) -> None:
    """Copy without changing input, flush to disk, then compare every byte."""
    import os

    if source.resolve() == destination.resolve():
        raise toolkit.ToolkitError("Quelle und Speicherziel müssen verschieden sein.")
    with source.open("rb") as incoming, destination.open("wb") as outgoing:
        shutil.copyfileobj(incoming, outgoing, COPY_BLOCK)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    toolkit.compare_files(source, destination)


def export_backup(directory: Path, destination: Path) -> None:
    """Export only manifest and actually captured files, including partial data."""
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    staged = directory / "export.x6backup.zip"
    with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in toolkit.BUNDLE_FILES:
            if name in manifest["files"] and (directory / name).is_file():
                archive.write(directory / name, name)
        archive.write(directory / "manifest.json", "manifest.json")
    save_checked(staged, destination)
