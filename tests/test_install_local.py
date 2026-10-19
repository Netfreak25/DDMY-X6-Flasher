"""Exercise the updater's real Windows lock check without touching any device."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "tools/install_local.ps1"


@unittest.skipUnless(os.name == "nt", "Windows file locks")
class LocalUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.lock = self.root / "device.lock"
        self.lock.write_bytes(b"0")
        self.harness = self.root / "lock-probe.ps1"
        self.harness.write_text('''param([string]$Source, [string]$LockPath)
$ErrorActionPreference = 'Stop'
$tree = [Management.Automation.Language.Parser]::ParseFile($Source, [ref]$null, [ref]$null)
$function = $tree.Find({param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Test-DeviceIdle'
}, $true)
. ([scriptblock]::Create($function.Extent.Text))
Test-DeviceIdle
''', encoding="utf-8")

    def probe(self):
        return subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                               str(self.harness), "-Source", str(SCRIPT), "-LockPath", str(self.lock)],
                              capture_output=True, timeout=15, check=False)

    def test_read_or_write_job_lock_defers_update_then_allows_idle(self):
        import msvcrt

        with self.lock.open("r+b") as handle:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                busy = self.probe()
                self.assertEqual(busy.returncode, 0, busy.stderr)
                self.assertEqual(busy.stdout.strip(), b"False")
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        idle = self.probe()
        self.assertEqual(idle.returncode, 0, idle.stderr)
        self.assertEqual(idle.stdout.strip(), b"True")

    def test_missing_lock_never_means_idle(self):
        self.lock.unlink()
        result = self.probe()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(b"True", result.stdout)


if __name__ == "__main__":
    unittest.main()
