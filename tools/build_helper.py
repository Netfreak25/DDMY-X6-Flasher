"""Build a portable Windows app using an explicit, hash-checked resource list."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import x6_toolkit as toolkit
import x6_web as web

ROOT = Path(__file__).resolve().parents[1]


def build() -> None:
    """Bundle exactly six authorized generic helpers, never a dump or LFI image."""
    output = ROOT / "private_dumps" / "release"
    work = ROOT / "private_dumps" / "build"
    output.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed", "--noupx",
               "--name", "DDMY X6 Helper", "--distpath", str(output), "--workpath", str(work),
               "--specpath", str(work), "--icon", str(ROOT / "assets/x6-icon.ico"),
               "--version-file", str(ROOT / "tools/version_info.txt")]
    resources = ["site.json", "index.html", "app.js", "styles.css", "assets/x6-icon.ico", "assets/x6-icon.png",
                 "assets/x6-icon.svg", "tools/x6_profile.json", "tools/x6_device_probe.ps1"]
    for folder in ("lib", "vendor"):
        resources.extend(str(path.relative_to(ROOT)) for path in sorted((ROOT / folder).glob("*")) if path.is_file())
    for name in resources:
        path = ROOT / name
        command.extend(["--add-data", f"{path}:{path.parent.relative_to(ROOT)}"])
    profile = toolkit.Profile.load()
    for name in web.PAYLOAD_NAMES:
        path = toolkit.DEFAULT_RUNTIME / name
        if toolkit.sha256_file(path) != profile.runtime_sha256[name]:
            raise toolkit.ToolkitError(f"Generic helper checksum mismatch: {name}")
        command.extend(["--add-data", f"{path}:runtime"])
    command.append(str(ROOT / "tools/x6_helper.py"))
    subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    build()
