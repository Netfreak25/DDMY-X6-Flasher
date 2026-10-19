"""Compile an Inno Setup installer from exactly the portable Windows payload."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from package_release import ROOT, windows_files

OUTPUT = ROOT / "private_dumps/release"
SETUP_NAME = "DDMY-X6-Flasher-Setup.exe"
BUILD_RECORD = OUTPUT / "installer-build.json"


def payload_hashes() -> dict[str, str]:
    """Fingerprint every file that is installed; exclude private runtime data."""
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() for path, name in windows_files()}


def compiler_path() -> Path:
    """Find the compiler without downloading or installing tools implicitly."""
    configured = os.environ.get("X6_ISCC")
    if configured:
        candidates = [Path(configured)]
    else:
        candidates = [Path(shutil.which("ISCC.exe") or "ISCC.exe")]
        for folder in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            if folder in os.environ:
                base = Path(os.environ[folder])
                candidates += [base / "Inno Setup 6/ISCC.exe", base / "Programs/Inno Setup 6/ISCC.exe"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("Inno Setup 6 fehlt. X6_ISCC auf den vollständigen Pfad zu ISCC.exe setzen.")


def verify_installer() -> Path:
    """Reject a stale setup after the EXE, docs, installer script or version changes."""
    record = json.loads(BUILD_RECORD.read_text(encoding="utf-8"))
    setup = OUTPUT / SETUP_NAME
    version = json.loads((ROOT / "site.json").read_text(encoding="utf-8"))["version"]
    script_hash = hashlib.sha256((ROOT / "tools/windows_installer.iss").read_bytes()).hexdigest()
    if (record["version"] != version or record["files"] != payload_hashes()
            or record["script_sha256"] != script_hash
            or record["setup_sha256"] != hashlib.sha256(setup.read_bytes()).hexdigest()):
        raise ValueError("Installer passt nicht zum aktuellen Release. build_installer.py erneut ausführen.")
    return setup


def build() -> Path:
    """Stage only approved files in a new directory, compile and record provenance."""
    compiler = compiler_path()
    version = json.loads((ROOT / "site.json").read_text(encoding="utf-8"))["version"]
    work = ROOT / "private_dumps/build"
    work.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    before = payload_hashes()
    with tempfile.TemporaryDirectory(prefix="installer-", dir=work) as directory:
        stage = Path(directory)
        for source, name in windows_files():
            destination = stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        subprocess.run([str(compiler), f"/DAppVersion={version}", f"/DPayloadDir={stage}",
                        f"/DReleaseDir={OUTPUT}", f"/DProjectDir={ROOT}",
                        str(ROOT / "tools/windows_installer.iss")], check=True, cwd=ROOT)
    if before != payload_hashes():
        raise ValueError("Release-Dateien wurden während des Installer-Builds verändert.")
    setup = OUTPUT / SETUP_NAME
    record = {"version": version, "files": before,
              "script_sha256": hashlib.sha256((ROOT / "tools/windows_installer.iss").read_bytes()).hexdigest(),
              "setup_sha256": hashlib.sha256(setup.read_bytes()).hexdigest()}
    BUILD_RECORD.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return verify_installer()


if __name__ == "__main__":
    print(build())
