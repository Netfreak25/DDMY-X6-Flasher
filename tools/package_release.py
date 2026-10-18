"""Package the committed source and portable helper with a strict publication allowlist."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {".gitignore", ".nojekyll", "README.md", "THIRD_PARTY.md", "site.json", "index.html",
              "app.js", "styles.css", "package.json", "package-lock.json", "eslint.config.js",
              "Start X6 Web.cmd", "Start X6 Online.cmd"}
TEXT_FOLDERS = {"tools", "tests", "web-tests", "lib", "vendor", "docs"}
TEXT_SUFFIXES = {".py", ".js", ".ps1", ".json", ".md", ".txt", ".LICENSE"}
APP_ASSETS = {"assets/x6-icon.svg", "assets/x6-icon.ico", "assets/x6-icon.png", "assets/ddmy-x6-detail.png"}


def allowed_source(name: str) -> bool:
    """Permit only app code, tests, usage docs and the explicitly approved images."""
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "private_dumps" in path.parts:
        return False
    return name in ROOT_FILES or name in APP_ASSETS or (
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
    helper = output / "DDMY-X6-Flasher-Windows-Helper.zip"
    with zipfile.ZipFile(helper, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(output / "DDMY X6 Helper.exe", "DDMY X6 Helper.exe")
        archive.write(ROOT / "docs/BEDIENUNG.md", "Bedienung.md")
        archive.write(ROOT / "THIRD_PARTY.md", "THIRD_PARTY.md")
        archive.write(ROOT / "vendor/fflate.LICENSE", "licenses/fflate.txt")
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
