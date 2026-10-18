"""Rebuild our small RAM power-off helper; fail if the pinned bytes change."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import x6_toolkit as toolkit

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_NAME = "x6_poweroff.bin"


def build(output: Path) -> Path:
    """Use ARM GCC from PATH or X6_ARM_GCC, including its sibling runtime DLLs."""
    compiler = os.environ.get("X6_ARM_GCC") or shutil.which("arm-none-eabi-gcc")
    if not compiler:
        raise toolkit.ToolkitError("ARM GCC fehlt. arm-none-eabi-gcc in PATH aufnehmen oder X6_ARM_GCC setzen.")
    compiler = Path(compiler).resolve()
    objcopy = compiler.with_name("arm-none-eabi-objcopy" + compiler.suffix)
    environment = dict(os.environ)
    environment["PATH"] = str(compiler.parent) + os.pathsep + environment.get("PATH", "")
    output.mkdir(parents=True, exist_ok=True)
    elf, binary = output / "x6_poweroff.elf", output / PAYLOAD_NAME
    profile = toolkit.Profile.load()
    subprocess.run([str(compiler), "-mcpu=cortex-m4", "-mthumb", "-Os", "-Wall", "-Wextra", "-Werror",
                    "-ffreestanding", "-fno-builtin", "-nostdlib", "-T", str(ROOT / "tools/x6_ram.ld"),
                    f"-Wl,--defsym,X6_RAM_ADDRESS={profile.protocol['work_address']}",
                    str(ROOT / "tools/x6_poweroff.c"), "-o", str(elf)], env=environment, check=True)
    subprocess.run([str(objcopy), "-O", "binary", str(elf), str(binary)], env=environment, check=True)
    expected = profile.protocol["poweroff"]
    if binary.stat().st_size != expected["size"] or toolkit.sha256_file(binary) != expected["sha256"]:
        raise toolkit.ToolkitError("Der gebaute Ausschaltcode weicht vom geprüften RAM-Kandidaten ab.")
    return binary


if __name__ == "__main__":
    print(build(toolkit.DEFAULT_RUNTIME))
