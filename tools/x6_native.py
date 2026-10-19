"""Desktop operations composed from the existing, checked Actions toolkit."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import x6_archive as archive
import x6_images as images
import x6_poweroff
import x6_reset
import x6_toolkit as tk
from x6_brec_transfer import prepare_transfer


def compare_images(expected: Path, actual: Path) -> dict:
    """Count all changed bytes, even when device content is structurally invalid."""
    if expected.stat().st_size != actual.stat().st_size:
        raise tk.ToolkitError("Der Gerätebereich wurde nicht vollständig gelesen.")
    differences, offsets, offset = 0, [], 0
    with expected.open("rb") as left, actual.open("rb") as right:
        while first := left.read(archive.COPY_BLOCK):
            second = right.read(len(first))
            if first != second:
                for index, (a, b) in enumerate(zip(first, second, strict=True)):
                    if a != b:
                        differences += 1
                        if len(offsets) < 32:
                            offsets.append(offset + index)
            offset += len(first)
    return {"identical": differences == 0, "different": differences, "first_offsets": offsets,
            "expected_sha256": tk.sha256_file(expected), "device_sha256": tk.sha256_file(actual)}


class NativeOperations:
    """One worker calls one operation while holding the application's device lock."""

    def __init__(self, profile: tk.Profile, runtime: Path, notify) -> None:
        self.profile = profile
        self.runtime = runtime
        self.notify = notify
        self.serial: str | None = None
        self.log: tk.RunLog | None = None
        self.pending_backup: Path | None = None
        self.selection: images.Selection | None = None

    def begin(self, name: str) -> tk.RunLog:
        self.log = tk.new_run(name)
        self.log.notify = self.notify
        self.log.event("operation", name=name)
        return self.log

    def step(self, label: str) -> None:
        self.log.event("phase", label=label)

    def connect(self) -> dict:
        """Explicit discovery only; never launch a flash driver on Connect."""
        self.begin("connect")
        device = tk.probe_device(self.profile)
        if device.mode == "missing":
            raise tk.ToolkitError("Kein X6 gefunden. Bitte den Player anschließen.")
        if device.mode == "normal":
            self.serial = device.serial
        return {"message": "X6 erkannt · " + ("Normalmodus" if device.mode == "normal" else "ADFU"), "connected": True}

    def session(self) -> tk.ActionsBackend:
        """Validate helpers first, then identify and enter the known driver path."""
        backend = tk.ActionsBackend(self.profile, self.runtime, self.log)
        self.step("Player vorbereiten")
        device = tk.probe_device(self.profile)
        if device.mode == "normal":
            if self.serial and self.serial != device.serial:
                raise tk.ToolkitError("Der angeschlossene X6 hat sich geändert. Bitte erneut verbinden.")
            self.serial = device.serial
        tk.enter_adfu(self.profile)
        tk.start_adfus(backend)
        tk.start_fwsc(backend)
        return backend

    def reboot(self, backend: tk.ActionsBackend) -> bool:
        self.step("Neustarten")
        try:
            tk.reset_after_verify(backend, self.serial)
            self.log.event("normal_mode_restored")
            return True
        except (tk.ToolkitError, OSError, subprocess.SubprocessError) as error:
            self.log.event("restart_unconfirmed", error=str(error))
            return False

    def stage_lfi(self, image: Path) -> Path:
        self.step("Firmwaredatei prüfen")
        if image.stat().st_size != self.profile.lfi_size:
            raise tk.ToolkitError("Die Datei hat nicht die erwartete X6-Firmwaregröße.")
        staged = self.log.directory / "candidate.bin"
        shutil.copyfile(image, staged)
        tk.validate_lfi(staged, self.profile)
        return staged

    def inspect(self, sources: tuple[Path, ...]) -> dict:
        """Identify and stage a user's selection entirely offline."""
        self.selection = None
        self.begin("select-images")
        self.selection = images.select_images(sources, self.log.directory / "selection", self.profile)
        self.log.event("selection", areas=self.selection.areas, hashes=self.selection.hashes)
        return {"message": f"Erkannt: {self.selection.label}. Dateien geprüft.", "selection_ready": True}

    def check_selection_device(self, selection: images.Selection) -> None:
        if selection.serial and self.serial and selection.serial.upper() != self.serial.upper():
            raise tk.ToolkitError("Die Sicherung gehört zu einem anderen X6.")

    def compare_selection(self, selection: images.Selection) -> dict:
        """Compare exactly the selected areas, including both physical boot copies."""
        self.begin("compare-components")
        selected = images.restage(selection, self.log.directory / "selection", self.profile)
        backend = self.session()
        self.check_selection_device(selected)
        after = self.log.directory / "readback"
        after.mkdir()
        if "lfi" in selected.files:
            self.step("Geräte-LFI vergleichen")
            tk.read_lfi(backend, after / "lfi.bin", "compare_read", validate=False)
        if selected.has_boot:
            self.step("Beide Bootkopien vergleichen")
            tk.read_boot(backend, after)
        results = {name: compare_images(selected.files[area], after / name)
                   for area in selected.areas for name in images.AREA_FILES[area]}
        different = sum(item["different"] for item in results.values())
        self.log.event("component_comparison", files=results, different=different)
        return {"message": f"{selected.label}: " + ("identisch." if not different else f"{different:,} unterschiedliche Bytes."),
                "identical": not different, "different": different, "files": results, "warning": bool(different)}

    def backup_before_boot_write(self, backend: tk.ActionsBackend, destination: Path) -> Path:
        """Save a complete, compatible recovery package before changing boot areas."""
        self.step("Vollständige Rücksicherung erstellen")
        before = self.log.directory / "before"
        before.mkdir()
        tk.read_lfi(backend, before / "lfi.bin", "read_before")
        tk.read_boot(backend, before)
        manifest = {"schema": 1, "model": self.profile.model, "nand_id": self.profile.nand_id.hex(),
                    "status": "complete", "device_serial": self.serial,
                    "steps": {name: {"status": "ok"} for name in ("lfi", "brec0", "brec1")},
                    "files": {name: {"size": (before / name).stat().st_size, "sha256": tk.sha256_file(before / name)}
                              for name in tk.BUNDLE_FILES}}
        tk.write_manifest(before, manifest)
        tk.verify_bundle(before, self.profile)
        self.pending_backup = before
        archive.export_backup(before, destination)
        self.pending_backup = None
        self.log.event("backup_saved", path=str(destination))
        return before

    def flash_selection(self, selection: images.Selection, destination: Path | None) -> dict:
        """Write only detected areas in MBREC → BREC → LFI order, without retries."""
        self.begin("flash-components")
        if destination is not None and any(
                destination.resolve() == source.resolve() or source.is_dir() and source.resolve() in destination.resolve().parents
                for source in selection.sources):
            raise tk.ToolkitError("Die Rücksicherung darf die ausgewählten Quelldateien nicht überschreiben.")
        selected = images.restage(selection, self.log.directory / "selection", self.profile)
        if not selected.has_boot:
            return self.flash(selected.files["lfi"], destination)
        if not self.profile.boot_write_mapping_verified:
            raise tk.ToolkitError("Boot-Schreibweg nicht freigegeben. Bitte die Notfall-Wiederherstellung verwenden.")
        # Preflight the BREC transfer before MBREC or any other flash command.
        checked = tk.ActionsBackend(self.profile, self.runtime, self.log)
        if "brec" in selected.files:
            prepare_transfer(selected.files["brec"].read_bytes(), checked.file("fwsc.bin").read_bytes(), self.profile)
        backend = self.session()
        self.check_selection_device(selected)
        if destination is not None:
            before = self.backup_before_boot_write(backend, destination)
        else:
            self.log.event("backup_skipped", requested_by_user=True)
            self.step("Bootgeometrie und unveränderte Bootbereiche prüfen")
            before = self.log.directory / "preflight"
            before.mkdir()
            if "lfi" in selected.files:
                self.read_lfi_header(backend, before / "lfi.bin")
            tk.read_boot(backend, before)
        if "lfi" in selected.files:
            tk.check_preserved_header(selected.files["lfi"], before / "lfi.bin", self.profile)
        if "mbrec" in selected.files and selected.files["mbrec"].read_bytes()[3:16] != (before / "mbrec.bin").read_bytes()[3:16]:
            raise tk.ToolkitError("MBREC-Bootpositionen oder NAND-Geometrie passen nicht zum Gerät.")
        # The NAND boot reader occupies the same RAM as FWSC: reload before writing.
        tk.start_fwsc(backend)
        for area in selected.areas:
            self.step(f"{area.upper()} schreiben")
            tk.write_area(backend, f"write_{area}", self.profile.protocol[f"{area}_sector"], selected.files[area])
        tk.finalize_flash(backend)
        self.step("Geschriebene Bereiche und beide Bootkopien prüfen")
        after = self.log.directory / "readback"
        after.mkdir()
        if "lfi" in selected.files:
            tk.read_lfi(backend, after / "lfi.bin", "read_after")
            tk.compare_files(selected.files["lfi"], after / "lfi.bin")
        tk.read_boot(backend, after)
        for area in ("mbrec", "brec"):
            for name in images.AREA_FILES[area]:
                tk.compare_files(selected.files.get(area, before / name), after / name)
        self.log.event("components_verified", areas=selected.areas)
        normal = self.reboot(backend)
        return {"message": f"{selected.label} geschrieben und vollständig verifiziert. " +
                ("Player normal gestartet." if normal else "Neustart noch nicht bestätigt."),
                "areas": selected.areas, "normal_mode": normal, "warning": not normal}

    def compare(self, image: Path) -> dict:
        self.begin("compare")
        staged = self.stage_lfi(image)
        backend = self.session()
        self.step("Vollständiges Geräte-LFI lesen")
        current = self.log.directory / "device_lfi.bin"
        tk.read_lfi(backend, current, "compare_read", validate=False)
        result = compare_images(staged, current)
        self.log.event("comparison", **result)
        result["message"] = "Firmware ist identisch." if result["identical"] else f"Firmware weicht ab: {result['different']:,} unterschiedliche Bytes."
        result["warning"] = not result["identical"]
        return result

    def read_lfi_header(self, backend: tk.ActionsBackend, path: Path) -> None:
        """Read only one sector for identity checks, never a full recovery image."""
        self.step("Gerätespezifischen LFI-Header prüfen")
        backend.run("read_header", "read_lfi", "0", hex(self.profile.sector_size), str(path), timeout_s=30)
        if not path.is_file() or path.stat().st_size != self.profile.sector_size:
            raise tk.ToolkitError("LFI-Header wurde nicht vollständig gelesen.")

    def flash(self, image: Path, backup_destination: Path | None) -> dict:
        self.begin("flash")
        if backup_destination is not None and image.resolve() == backup_destination.resolve():
            raise tk.ToolkitError("Die Sicherung darf die ausgewählte Firmware nicht überschreiben.")
        staged = self.stage_lfi(image)
        backend = self.session()
        before = self.log.directory / "before_lfi.bin"
        if backup_destination is not None:
            self.step("Bisheriges LFI sichern")
            tk.read_lfi(backend, before, "read_before", validate=False)
        else:
            self.log.event("backup_skipped", requested_by_user=True)
            before = self.log.directory / "device_header.bin"
            self.read_lfi_header(backend, before)
        tk.check_preserved_header(staged, before, self.profile)
        if backup_destination is not None:
            self.pending_backup = before
            archive.save_checked(before, backup_destination)
            self.pending_backup = None
            self.log.event("backup_saved", path=str(backup_destination), sha256=tk.sha256_file(before))
        self.step("LFI schreiben")
        tk.write_area(backend, "write_lfi", self.profile.protocol["lfi_sector"], staged)
        tk.finalize_flash(backend)
        self.step("Vollständig zurücklesen und prüfen")
        after = self.log.directory / "after_lfi.bin"
        tk.read_lfi(backend, after, "read_after")
        tk.compare_files(staged, after)
        self.log.event("lfi_verified", sha256=tk.sha256_file(after))
        normal = self.reboot(backend)
        return {"message": "LFI geschrieben und vollständig verifiziert. " +
                ("Player normal gestartet." if normal else "Neustart noch nicht bestätigt."),
                "normal_mode": normal, "warning": not normal}

    def dump(self, destination: Path) -> dict:
        log = self.begin("dump")
        manifest = {"schema": 1, "model": self.profile.model, "nand_id": self.profile.nand_id.hex(),
                    "status": "incomplete", "files": {}, "steps": {}}
        tk.write_manifest(log.directory, manifest)
        backend = None
        try:
            backend = self.session()
            manifest["device_serial"] = self.serial
            reads = [("lfi", lambda: tk.read_lfi(backend, log.directory / "lfi.bin", "read_lfi")),
                     ("brec0", lambda: tk.read_boot_copy(backend, log.directory, 0)),
                     ("brec1", lambda: tk.read_boot_copy(backend, log.directory, 1))]
            for name, read in reads:
                self.step(f"Sichern · {name.upper()}")
                try:
                    read()
                    manifest["steps"][name] = {"status": "ok"}
                except (tk.ToolkitError, OSError, subprocess.SubprocessError) as error:
                    manifest["steps"][name] = {"status": "failed", "error": str(error)}
                    if backend.failed or isinstance(error, (OSError, subprocess.SubprocessError)):
                        raise
        except (tk.ToolkitError, OSError, subprocess.SubprocessError) as error:
            manifest["error"] = str(error)
            log.event("dump_stopped", error=str(error))
        # Preserve all bytes actually read, including truncated/invalid files.
        for name in tk.BUNDLE_FILES:
            path = log.directory / name
            if path.is_file():
                manifest["files"][name] = {"size": path.stat().st_size, "sha256": tk.sha256_file(path)}
        if len(manifest["steps"]) == 3 and all(item["status"] == "ok" for item in manifest["steps"].values()):
            manifest["status"] = "complete"
            tk.write_manifest(log.directory, manifest)
            try:
                tk.verify_bundle(log.directory, self.profile)
            except tk.ToolkitError as error:
                manifest.update(status="incomplete", error=str(error))
        tk.write_manifest(log.directory, manifest)
        self.step("Sicherung speichern")
        self.pending_backup = log.directory
        archive.export_backup(log.directory, destination)
        self.pending_backup = None
        complete = manifest["status"] == "complete"
        normal = self.reboot(backend) if complete else False
        return {"message": ("Vollständige Sicherung gespeichert." if complete else
                            "Unvollständige Sicherung gespeichert. Verfügbare Daten sind enthalten; kein Full Restore möglich.") +
                (" Player normal gestartet." if normal else " Player bleibt gegebenenfalls in ADFU."),
                "complete": complete, "normal_mode": normal, "warning": not complete or not normal}

    def save_pending(self, destination: Path) -> dict:
        """Retry only a local save, never read from or write to the player."""
        if self.pending_backup is None:
            raise tk.ToolkitError("Keine noch zu speichernde Sicherung vorhanden.")
        if self.pending_backup.is_dir():
            archive.export_backup(self.pending_backup, destination)
        else:
            archive.save_checked(self.pending_backup, destination)
        self.pending_backup = None
        return {"message": "Bereits gelesene Sicherung gespeichert. Kein erneuter Gerätezugriff."}

    def restore(self, source: Path, emergency: bool = False) -> dict:
        self.begin("restore")
        self.step("Sicherung vollständig prüfen")
        staged = self.log.directory / "source"
        manifest = archive.import_backup(source, staged, self.profile)
        if not self.profile.boot_write_mapping_verified and not emergency:
            raise tk.ToolkitError("Boot-Schreibweg nicht freigegeben; ausdrückliche Notfall-Wiederherstellung erforderlich.")
        # Check BREC transfer preparation before any device write, including MBREC.
        checked = tk.ActionsBackend(self.profile, self.runtime, self.log)
        prepare_transfer((staged / "brec0.bin").read_bytes(), checked.file("fwsc.bin").read_bytes(), self.profile)
        backend = self.session()
        if self.serial and manifest.get("device_serial") and self.serial.upper() != manifest["device_serial"].upper():
            raise tk.ToolkitError("Die Sicherung gehört zu einem anderen X6.")
        for area, name in (("mbrec", "mbrec.bin"), ("brec", "brec0.bin"), ("lfi", "lfi.bin")):
            self.step(f"Wiederherstellen · {area.upper()}")
            tk.write_area(backend, f"write_{area}", self.profile.protocol[f"{area}_sector"], staged / name)
        tk.finalize_flash(backend)
        self.step("Alle Bereiche zurücklesen und prüfen")
        after = self.log.directory / "readback"
        after.mkdir()
        tk.read_lfi(backend, after / "lfi.bin", "read_after")
        tk.compare_files(staged / "lfi.bin", after / "lfi.bin")
        try:
            tk.read_boot(backend, after)
            for name in tk.BUNDLE_FILES:
                tk.compare_files(staged / name, after / name)
        except (tk.ToolkitError, OSError, subprocess.SubprocessError) as error:
            self.log.event("boot_readback_unverified", error=str(error))
            if not emergency:
                raise
            return {"message": "LFI verifiziert; Boot-Rücklesung offen. Wiederherstellung unverifiziert, kein Neustart.",
                    "boot_verified": False, "warning": True}
        self.log.event("full_restore_verified")
        normal = self.reboot(backend)
        return {"message": "Alle fünf Bereiche bytegleich zurückgelesen und verifiziert. " +
                ("Player normal gestartet." if normal else "Neustart noch nicht bestätigt."),
                "boot_verified": True, "normal_mode": normal, "warning": not normal}

    def mode(self, action: str) -> dict:
        self.begin(action)
        self.step({"enter": "ADFU betreten", "leave": "ADFU verlassen", "poweroff": "Ausschaltprogramm laden"}[action])
        if action == "enter":
            if tk.enter_adfu(self.profile).mode != "adfu":
                raise tk.ToolkitError("Der ADFU-Wechsel wurde nicht bestätigt.")
            message = "X6 im ADFU-Modus erkannt."
        elif action == "leave":
            x6_reset.leave_adfu(self.profile, (self.runtime / "adfus.bin").read_bytes())
            message = "X6 im Normalmodus erkannt."
        else:
            message = x6_poweroff.power_off(self.profile, self.runtime)
        return {"message": message}
