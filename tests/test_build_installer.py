"""Check release payload isolation and rejection of stale installer artifacts."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_installer as installer


class InstallerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "tools").mkdir()
        (self.root / "site.json").write_text('{"version":"0.3.0-beta.3"}', encoding="utf-8")
        (self.root / "tools/windows_installer.iss").write_text("test script", encoding="utf-8")
        self.payload = self.root / "app.exe"
        self.payload.write_bytes(b"approved executable")
        (self.root / "private-firmware.bin").write_bytes(b"never publish")
        self.output = self.root / "release"
        for target, value in (("ROOT", self.root), ("OUTPUT", self.output),
                              ("BUILD_RECORD", self.output / "installer-build.json")):
            patcher = mock.patch.object(installer, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(installer, "windows_files", return_value=[(self.payload, "DDMY X6 Flasher.exe")])
        patcher.start()
        self.addCleanup(patcher.stop)

    def compile(self, command, **_kwargs):
        stage = Path(next(arg.removeprefix("/DPayloadDir=") for arg in command if arg.startswith("/DPayloadDir=")))
        self.assertEqual([path.name for path in stage.iterdir()], ["DDMY X6 Flasher.exe"])
        self.assertEqual((stage / "DDMY X6 Flasher.exe").read_bytes(), self.payload.read_bytes())
        (self.output / installer.SETUP_NAME).write_bytes(b"compiled installer")

    def build(self):
        with mock.patch.object(installer, "compiler_path", return_value=Path("ISCC.exe")), \
                mock.patch.object(installer.subprocess, "run", side_effect=self.compile):
            return installer.build()

    def test_only_approved_payload_is_staged_and_verified(self):
        setup = self.build()
        self.assertEqual(installer.verify_installer(), setup)
        record = json.loads(installer.BUILD_RECORD.read_text(encoding="utf-8"))
        self.assertEqual(list(record["files"]), ["DDMY X6 Flasher.exe"])

    def test_changed_payload_version_script_or_setup_rejects_stale_installer(self):
        for path in (self.payload, self.root / "site.json", self.root / "tools/windows_installer.iss",
                     self.output / installer.SETUP_NAME):
            with self.subTest(path=path.name):
                self.build()
                original = path.read_bytes()
                try:
                    path.write_bytes(b'{"version":"changed"}' if path.name == "site.json" else original + b"changed")
                    with self.assertRaisesRegex(ValueError, "Installer passt nicht"):
                        installer.verify_installer()
                finally:
                    path.write_bytes(original)

    def test_failed_compilation_propagates_without_success_record(self):
        with mock.patch.object(installer, "compiler_path", return_value=Path("ISCC.exe")), \
                mock.patch.object(installer.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "ISCC")), \
                self.assertRaises(subprocess.CalledProcessError):
            installer.build()
        self.assertFalse(installer.BUILD_RECORD.exists())

    def test_explicit_missing_compiler_does_not_silently_use_another(self):
        with mock.patch.dict(installer.os.environ, {"X6_ISCC": str(self.root / "missing.exe")}), \
                self.assertRaisesRegex(FileNotFoundError, "X6_ISCC"):
            installer.compiler_path()


if __name__ == "__main__":
    unittest.main()
