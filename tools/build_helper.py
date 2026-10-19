"""Build a portable Windows app using an explicit, hash-checked resource list."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import build_poweroff
import x6_toolkit as toolkit

ROOT = Path(__file__).resolve().parents[1]
SELF_TEST_TIMEOUT_S = 30


def build() -> None:
    """Bundle the checked backend and approved RAM helpers, never firmware or web assets."""
    output = ROOT / "private_dumps" / "release"
    work = ROOT / "private_dumps" / "build"
    output.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed", "--noupx",
               "--name", "DDMY X6 Flasher", "--distpath", str(output), "--workpath", str(work),
               "--specpath", str(work), "--icon", str(ROOT / "assets/x6-icon.ico"),
               "--version-file", str(ROOT / "tools/version_info.txt")]
    resources = ["site.json", "assets/x6-icon.ico", "assets/x6-icon.png",
                 "assets/x6-icon.svg", "tools/x6_profile.json", "tools/x6_artwork.json", "tools/x6_device_probe.ps1"]
    for name in resources:
        path = ROOT / name
        command.extend(["--add-data", f"{path}:{path.parent.relative_to(ROOT)}"])
    profile = toolkit.Profile.load()
    poweroff = build_poweroff.build(work / "poweroff")
    command.extend(["--add-data", f"{poweroff}:runtime"])
    for name in profile.runtime_sha256:
        path = toolkit.DEFAULT_RUNTIME / name
        if toolkit.sha256_file(path) != profile.runtime_sha256[name]:
            raise toolkit.ToolkitError(f"Generic helper checksum mismatch: {name}")
        command.extend(["--add-data", f"{path}:runtime"])
    command.append(str(ROOT / "tools/x6_helper.py"))
    subprocess.run(command, cwd=ROOT, check=True)
    report = output / "self-test.json"
    # Keep the package check independent of existing app data and MSIX path
    # virtualization in the build host. Never load a user's device binding here.
    test_environment = {**os.environ, "LOCALAPPDATA": str(work / "self-test-localappdata")}
    subprocess.run([str(output / "DDMY X6 Flasher.exe"), "--self-test", str(report)],
                   check=True, timeout=SELF_TEST_TIMEOUT_S, env=test_environment)
    result = json.loads(report.read_text(encoding="utf-8"))
    required = {"Verbinden", "Sicherung erstellen", "Sicherung wiederherstellen", "Auswahl flashen",
                "Mit Gerät vergleichen", "ADFU betreten", "ADFU verlassen", "Player ausschalten", "Bilder bearbeiten"}
    if (result.get("ok") is not True or not required <= set(result.get("controls", []))
            or result.get("backend_hashes_checked") is not True or result.get("workspace_outside_resources") is not True
            or result.get("device_access") is not False or result.get("browser_started") is not False
            or result.get("artwork_slots") != 14 or result.get("artwork_formats") != ["PNG", "JPEG", "WEBP"]
            or result.get("poweroff_payload_size") != profile.protocol["poweroff"]["size"]
            or result.get("http_pairing") != {"preflight_status": 204, "pair_status": 200}):
        raise toolkit.ToolkitError(f"Die erzeugte EXE hat ihren HTTP-/Fenster-Selbsttest nicht bestanden: {result}")


if __name__ == "__main__":
    build()
