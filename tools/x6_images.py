"""Content-based selection of X6 firmware components, without USB access."""

from __future__ import annotations

import shutil
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path

import x6_archive as archive
import x6_toolkit as tk
from x6_brec_transfer import validate_brec

AREA_FILES = {"mbrec": ("mbrec.bin", "mbrec_copy.bin"),
              "brec": ("brec0.bin", "brec1.bin"), "lfi": ("lfi.bin",)}
MAX_NAND_PAGE = 0x4000


def identify(path: Path, profile: tk.Profile) -> str:
    """Use exact size, headers and checksums; never infer an area from a name."""
    size = path.stat().st_size
    if size == profile.lfi_size:
        tk.validate_lfi(path, profile)
        return "lfi"
    if size == profile.brec_size:
        try:
            validate_brec(path.read_bytes(), profile)
        except ValueError as error:
            raise tk.ToolkitError(f"Ungültiger BREC: {error}") from error
        return "brec"
    if size == profile.mbrec_size:
        tk.validate_boot(path, "mbrec", profile)
        data = path.read_bytes()
        pages, page_size = struct.unpack_from("<2H", data, 0x0C)
        if not pages or not profile.sector_size <= page_size <= MAX_NAND_PAGE or page_size & (page_size - 1):
            raise tk.ToolkitError("Ungültige MBREC-NAND-Geometrie.")
        return "mbrec"
    raise tk.ToolkitError(f"{path.name}: kein vollständiger X6-MBREC, BREC oder LFI erkannt.")


@dataclass(frozen=True)
class Selection:
    """Private, validated input copies; hashes bind confirmation to these bytes."""

    files: dict[str, Path]
    hashes: dict[str, str]
    sources: tuple[Path, ...]
    serial: str | None = None
    normalization: tuple[dict, ...] = ()

    @property
    def areas(self) -> tuple[str, ...]:
        return tuple(area for area in AREA_FILES if area in self.files)

    @property
    def label(self) -> str:
        return " + ".join(area.upper() for area in self.areas)

    @property
    def has_boot(self) -> bool:
        return any(area in self.files for area in ("mbrec", "brec"))


def select_images(sources: tuple[Path, ...], target: Path, profile: tk.Profile) -> Selection:
    """Stage BINs or one complete ZIP/folder. Conflicting duplicate copies stop."""
    if not sources:
        raise tk.ToolkitError("Bitte mindestens eine Firmwaredatei auswählen.")
    target.mkdir(parents=True, exist_ok=False)
    inputs, serial, normalization = [], None, []
    for index, source in enumerate(sources):
        if source.is_dir() or zipfile.is_zipfile(source):
            folder = target / f"package-{index}"
            manifest = archive.import_backup(source, folder, profile)
            if (folder / "original-lfi.bin").is_file():
                normalization.extend(manifest["lfi_normalization"]["changes"])
            identity = manifest.get("device_serial")
            if serial and identity and serial.upper() != identity.upper():
                raise tk.ToolkitError("Die Pakete gehören zu unterschiedlichen Geräten.")
            serial = serial or identity
            inputs.extend(folder / name for name in tk.BUNDLE_FILES)
        else:
            if source.stat().st_size not in (profile.mbrec_size, profile.brec_size, profile.lfi_size):
                raise tk.ToolkitError(f"{source.name}: unbekannte Firmwaregröße.")
            staged = target / f"input-{index}.bin"
            if source.stat().st_size == profile.lfi_size:
                normalization.extend(archive.stage_lfi(source, staged, profile))
            else:
                shutil.copyfile(source, staged)
            inputs.append(staged)
    files, hashes = {}, {}
    for path in inputs:
        area, digest = identify(path, profile), tk.sha256_file(path)
        if area in files and hashes[area] != digest:
            raise tk.ToolkitError(f"Widersprüchliche {area.upper()}-Dateien ausgewählt.")
        files[area], hashes[area] = path, digest
    return Selection(files, hashes, sources, serial, tuple(normalization))


def restage(selection: Selection, target: Path, profile: tk.Profile) -> Selection:
    """Recheck selected bytes before connecting; input changes need reselection."""
    checked = select_images(tuple(selection.files[area] for area in selection.areas), target, profile)
    if checked.hashes != selection.hashes:
        raise tk.ToolkitError("Die ausgewählten Dateien wurden geändert. Bitte erneut auswählen.")
    return Selection(checked.files, checked.hashes, selection.sources, selection.serial, selection.normalization)
