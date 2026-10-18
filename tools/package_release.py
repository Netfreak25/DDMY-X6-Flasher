"""Package the committed source and portable helper with a strict publication allowlist."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {".gitignore", ".nojekyll", "README.md", "THIRD_PARTY.md", "site.json", "index.html",
              "app.js", "styles.css", "package.json", "package-lock.json", "eslint.config.js",
              "Start X6 App.cmd"}
TEXT_FOLDERS = {"tools", "tests", "web-tests", "lib", "vendor", "docs"}
TEXT_SUFFIXES = {".py", ".js", ".ps1", ".json", ".md", ".txt", ".LICENSE"}
APP_ASSETS = {"assets/x6-icon.svg", "assets/x6-icon.ico", "assets/x6-icon.png", "assets/ddmy-x6-detail.png"}
RAM_SOURCES = {"tools/x6_poweroff.c", "tools/x6_ram.ld", "vendor/actions_flash/actions_dump.c"}


def allowed_source(name: str) -> bool:
    """Permit only app code, tests, usage docs and the explicitly approved images."""
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "private_dumps" in path.parts:
        return False
    return name in ROOT_FILES or name in APP_ASSETS or name in RAM_SOURCES or (
        len(path.parts) > 1 and path.parts[0] in TEXT_FOLDERS and path.suffix in TEXT_SUFFIXES
    )


def package() -> None:
    """Produce only two release ZIPs and their SHA-256 checksums."""
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().rstrip("\0").split("\0")
    rejected = [name for name in names if not allowed_source(name)]
    if rejected:
        raise ValueError(f"Not approved for release: {rejected}")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip():
        raise ValueError("Commit the reviewed source before packaging.")
    output = ROOT / "private_dumps" / "release"
    source = output / "DDMY-X6-Flasher-Source.zip"
    subprocess.run(["git", "archive", "--format=zip", "--prefix=DDMY-X6-Flasher/", "-o", str(source), "HEAD"], cwd=ROOT, check=True)
    # Ship the exact upstream library source alongside the linked DLL, without
    # tracking a dependency archive or any private firmware in the repository.
    dependency = json.loads((ROOT / "vendor/actions_flash/dependencies.json").read_text(encoding="utf-8"))["libusb_source"]
    library_source = ROOT / "private_dumps/build/backend" / dependency["filename"]
    if hashlib.sha256(library_source.read_bytes()).hexdigest() != dependency["sha256"]:
        raise ValueError("libusb source checksum mismatch")
    with zipfile.ZipFile(source, "a", zipfile.ZIP_DEFLATED) as archive:
        archive.write(library_source, "DDMY-X6-Flasher/vendor/" + dependency["filename"])
    helper = output / "DDMY-X6-Flasher-Windows.zip"
    with zipfile.ZipFile(helper, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(output / "DDMY X6 Flasher.exe", "DDMY X6 Flasher.exe")
        archive.write(ROOT / "docs/BEDIENUNG.md", "Bedienung.md")
        archive.write(ROOT / "THIRD_PARTY.md", "THIRD_PARTY.md")
        archive.write(ROOT / "vendor/fflate.LICENSE", "licenses/fflate.txt")
        archive.write(ROOT / "vendor/libusb.LICENSE", "licenses/libusb.txt")
        archive.write(ROOT / "vendor/actions_flash/README.md", "licenses/Actions-Backend.md")
        python = Path(sys.base_prefix)
        for path, name in [(python / "LICENSE.txt", "Python.txt"),
                           (ROOT / "vendor/Tcl.LICENSE", "Tcl.txt"),
                           (python / "tcl/tk8.6/license.terms", "Tk.txt")]:
            archive.write(path, "licenses/" + name)
    lines = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}" for path in (helper, source)]
    (output / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="ascii")
    print("\n".join(lines))


if __name__ == "__main__":
    package()
