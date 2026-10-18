"""Synthetic files and a simulated backend exercise the real native workflows."""

import dataclasses
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import x6_archive as archive
import x6_lfi
import x6_native as native
import x6_toolkit as toolkit
from test_x6_brec_transfer import synthetic_inputs
from x6_jobs import DeviceJobs
from x6_process import run_streamed


def synthetic_lfi() -> bytes:
    """148 tiny files; valid directory, header and file checksums, no firmware."""
    data = bytearray(1024 * 1024)  # Keep ZIP overhead + both boot copies below twice the test LFI size.
    struct.pack_into("<I", data, 0, x6_lfi.LFI_MAGIC)
    for index in range(x6_lfi.X6_FILE_COUNT):
        position, offset = 0x200 + index * 32, 0x2000 + index * 512
        data[position:position + 11] = f"T{index:07d}BIN".encode("ascii")
        struct.pack_into("<II", data, position + 0x10, offset // 512, 4)
        struct.pack_into("<I", data, offset, index + 1)
        struct.pack_into("<I", data, position + 0x1C, index + 1)
    struct.pack_into("<I", data, 0x10, x6_lfi.word_sum(data[0x200:0x2000], 4))
    struct.pack_into("<H", data, 0x1FE, x6_lfi.word_sum(data[:0x1FE], 2))
    return bytes(data)


class NativeTests(unittest.TestCase):
    def setUp(self):
        toolkit.PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=toolkit.PRIVATE_ROOT)
        self.base = Path(self.temp.name)
        self.runtime = self.base / "runtime"
        self.runtime.mkdir()
        brec, driver, profile = synthetic_inputs()
        payloads = {"fwsc.bin": driver, "adfus.bin": b"A" * 1012, "nandinfo.bin": b"N" * 128,
                    "nandhwsc.bin": b"H", "nandread.bin": b"R", "actions_dump.exe": b"test only",
                    "vram_last.bin": bytes(510) + b"\xaa\xbb"}
        for name, data in payloads.items():
            (self.runtime / name).write_bytes(data)
        self.image = synthetic_lfi()
        self.profile = dataclasses.replace(profile, lfi_size=len(self.image),
                                          runtime_sha256={name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()})
        self.source = self.base / "source"
        self.source.mkdir()
        mbrec = bytearray(1024)
        mbrec[2] = 0x5A
        self.files = {"lfi.bin": self.image, "brec0.bin": brec, "brec1.bin": brec,
                      "mbrec.bin": bytes(mbrec), "mbrec_copy.bin": bytes(mbrec)}
        for name, data in self.files.items():
            (self.source / name).write_bytes(data)
        self.manifest()
        self.calls = []
        self.events = []
        self.device_image = self.image
        self.ops = native.NativeOperations(self.profile, self.runtime, self.events.append)
        self.patches = [mock.patch.object(toolkit, "RUN_ROOT", self.base / "runs"),
                        mock.patch.object(toolkit, "probe_device", return_value=toolkit.Device("normal", "TEST")),
                        mock.patch.object(toolkit, "enter_adfu", return_value=toolkit.Device("adfu")),
                        mock.patch.object(toolkit, "wait_for_mode", return_value=toolkit.Device("normal", "TEST"))]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.temp.cleanup()

    def manifest(self):
        toolkit.write_manifest(self.source, {"schema": 1, "model": self.profile.model, "nand_id": self.profile.nand_id.hex(),
            "status": "complete", "files": {name: {"size": path.stat().st_size, "sha256": toolkit.sha256_file(path)}
                                            for name in toolkit.BUNDLE_FILES for path in [self.source / name]}})

    def backend(self, _backend, name, *args, **_kwargs):
        self.calls.append(name)
        if name.startswith("adfus_"):
            Path(args[-1]).write_bytes((self.runtime / "adfus.bin").read_bytes()[:512])
        elif name == "nand_scan":
            data = bytearray(0x9C)
            data[:2], data[16:21] = b"HW", self.profile.nand_id
            return f"result ({len(data)}):\n{data.hex(' ')}\n"
        elif name == "fwsc_start":
            data = bytes(6) + b"NAFWSC"
            return f"result ({len(data)}):\n{data.hex(' ')}\n"
        elif "read_lfi" in args:
            Path(args[-1]).write_bytes(self.device_image)
        elif args[0] == "read_brec":
            index = int(args[3])
            Path(args[2]).write_bytes(self.files["mbrec.bin" if index == 0 else "mbrec_copy.bin"])
            Path(args[4]).write_bytes(self.files[f"brec{index}.bin"])
        elif "write_flash" in args:
            data = Path(args[-1]).read_bytes()
            if name == "write_lfi":
                self.device_image = data
            sectors = len(data) // 512
            return f"write_flash: {sectors}/{sectors} sectors\n"
        return ""

    def simulated(self, replacement=None):
        run = replacement or self.backend
        return mock.patch.object(toolkit.ActionsBackend, "run", autospec=True, side_effect=run)

    def test_real_lfi_validation_counts_all_148_files(self):
        result = toolkit.validate_lfi(self.source / "lfi.bin", self.profile)
        self.assertEqual(result["files"], 148)
        self.assertEqual(result["sha256"], hashlib.sha256(self.image).hexdigest())

    def test_flash_saves_checked_backup_before_writes_and_full_readback(self):
        saved = self.base / "saved.bin"
        original = self.backend

        def run(backend, name, *args, **kwargs):
            if name == "write_lfi":
                self.assertEqual(saved.read_bytes(), self.image)
            return original(backend, name, *args, **kwargs)

        with self.simulated(run):
            result = self.ops.flash(self.source / "lfi.bin", saved)
        self.assertTrue(result["normal_mode"])
        self.assertEqual(self.calls, ["adfus_existing", "nand_scan", "fwsc_start", "read_before", "write_lfi", "finalize", "read_after", "reset"])
        self.assertEqual((self.source / "lfi.bin").read_bytes(), self.image)

    def test_save_failure_prevents_every_flash_write(self):
        with self.simulated(), mock.patch.object(archive, "save_checked", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.ops.flash(self.source / "lfi.bin", self.base / "saved.bin")
        self.assertNotIn("write_lfi", self.calls)
        before = list(self.calls)
        self.ops.save_pending(self.base / "recovered.bin")
        self.assertEqual((self.base / "recovered.bin").read_bytes(), self.image)
        self.assertEqual(self.calls, before)
        self.assertIsNone(self.ops.pending_backup)

    def test_missing_write_ack_stops_before_finish_readback_and_reset(self):
        def run(backend, name, *args, **kwargs):
            result = self.backend(backend, name, *args, **kwargs)
            return "" if name == "write_lfi" else result

        with self.simulated(run), self.assertRaisesRegex(toolkit.ToolkitError, "not confirmed"):
            self.ops.flash(self.source / "lfi.bin", self.base / "saved.bin")
        self.assertEqual(self.calls[-1], "write_lfi")
        self.assertEqual(self.calls.count("write_lfi"), 1)

    def test_corrupt_device_contents_are_comparable(self):
        damaged = bytearray(self.image)
        for index in (0, 30, len(damaged) - 1):
            damaged[index] ^= 1
        self.device_image = bytes(damaged)
        with self.simulated():
            result = self.ops.compare(self.source / "lfi.bin")
        self.assertEqual(result["different"], 3)
        self.assertFalse(any(name.startswith("write") for name in self.calls))

    def test_restore_checks_transfer_before_mbrec_and_reads_both_copies(self):
        with self.simulated():
            result = self.ops.restore(self.source)
        self.assertTrue(result["boot_verified"])
        self.assertEqual(self.calls, ["adfus_existing", "nand_scan", "fwsc_start", "write_mbrec", "write_brec", "write_lfi",
                                     "finalize", "read_after", "read_brec0", "read_brec1", "reset"])
        transfer = self.ops.log.directory / "write_brec-transfer.bin"
        self.assertNotEqual(transfer.read_bytes(), self.files["brec0.bin"])
        self.assertEqual((self.source / "brec0.bin").read_bytes(), self.files["brec0.bin"])

    def test_uncertain_mbrec_stops_restore_including_emergency(self):
        def run(backend, name, *args, **kwargs):
            if name == "write_mbrec":
                self.calls.append(name)
                raise toolkit.ToolkitError("write unconfirmed")
            return self.backend(backend, name, *args, **kwargs)

        for emergency in (False, True):
            self.calls.clear()
            with self.simulated(run), self.assertRaisesRegex(toolkit.ToolkitError, "unconfirmed"):
                self.ops.restore(self.source, emergency)
            self.assertEqual(self.calls[-1], "write_mbrec")
            self.assertEqual(self.calls.count("write_mbrec"), 1)

    def test_readback_mismatch_never_resets(self):
        def run(backend, name, *args, **kwargs):
            result = self.backend(backend, name, *args, **kwargs)
            if name == "read_brec1":
                data = bytearray(Path(args[4]).read_bytes())
                data[-1] ^= 1
                Path(args[4]).write_bytes(data)
            return result

        with self.simulated(run), self.assertRaisesRegex(toolkit.ToolkitError, "readback differs"):
            self.ops.restore(self.source)
        self.assertNotIn("reset", self.calls)

    def test_partial_dump_preserves_prefix_and_independent_second_copy(self):
        def run(backend, name, *args, **kwargs):
            result = self.backend(backend, name, *args, **kwargs)
            if name == "read_brec0":
                Path(args[4]).write_bytes(self.files["brec0.bin"][:65536])
                raise toolkit.ToolkitError("bad brec checksum")
            return result

        output = self.base / "partial.x6backup.zip"
        with self.simulated(run):
            result = self.ops.dump(output)
        self.assertFalse(result["complete"])
        with zipfile.ZipFile(output) as zipped:
            self.assertEqual(len(zipped.read("brec0.bin")), 65536)
            self.assertEqual(zipped.read("brec1.bin"), self.files["brec1.bin"])
        with self.assertRaisesRegex(toolkit.ToolkitError, "not complete"):
            archive.import_backup(output, self.base / "imported", self.profile)

    def test_complete_zip_and_folder_imports_match_and_malformed_zip_rejected(self):
        output = self.base / "backup.x6backup.zip"
        archive.export_backup(self.source, output)
        for source, target in ((output, "from_zip"), (self.source, "from_folder")):
            archive.import_backup(source, self.base / target, self.profile)
            for name in toolkit.BUNDLE_FILES:
                self.assertEqual((self.base / target / name).read_bytes(), self.files[name])
        with zipfile.ZipFile(output, "w") as zipped:
            zipped.writestr("../escape.bin", b"no extraction")
            zipped.writestr("lfi.bin", bytes(self.profile.lfi_size + 1))
        with self.assertRaisesRegex(toolkit.ToolkitError, "Archiveintrag"):
            archive.import_backup(output, self.base / "bad", self.profile)
        self.assertFalse((self.base / "escape.bin").exists())

    def test_corrupt_bundle_prevents_device_preparation(self):
        (self.source / "brec1.bin").write_bytes(b"bad")
        with self.simulated(), self.assertRaises(toolkit.ToolkitError):
            self.ops.restore(self.source)
        self.assertEqual(self.calls, [])

    @unittest.skipUnless(shutil.which("node"), "Node is required for cross-language ZIP interchange")
    def test_native_and_web_archives_interoperate_in_both_directions(self):
        source, output = self.base / "native.zip", self.base / "web.zip"
        archive.export_backup(self.source, source)
        profile = dataclasses.asdict(self.profile)
        profile["nand_id"] = self.profile.nand_id.hex()
        config = self.base / "profile.json"
        config.write_text(json.dumps(profile), encoding="utf-8")
        script = """
import {readFileSync, writeFileSync} from 'node:fs';
import {unpackBackup, packBackup} from './lib/archive.js';
import {validateBundle} from './lib/formats.js';
const [input, output, config] = process.argv.slice(2);
const profile = JSON.parse(readFileSync(config));
const files = unpackBackup(new Uint8Array(readFileSync(input)), profile);
const manifest = await validateBundle(files, profile);
writeFileSync(output, packBackup(files, manifest));
"""
        result = subprocess.run([shutil.which("node"), "--input-type=module", "-", str(source), str(output), str(config)],
                                input=script, text=True, capture_output=True, check=False, timeout=30,
                                cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0, result.stderr)
        archive.import_backup(output, self.base / "web-import", self.profile)

    def test_invalid_brec_checksum_stops_before_mbrec_even_with_updated_manifest(self):
        data = bytearray(self.files["brec0.bin"])
        data[0x210] ^= 1
        for name in ("brec0.bin", "brec1.bin"):
            (self.source / name).write_bytes(data)
        self.manifest()
        with self.simulated(), self.assertRaisesRegex(ValueError, "checksum"):
            self.ops.restore(self.source)
        self.assertEqual(self.calls, [])

    def test_dump_transport_failure_preserves_bytes_without_more_commands(self):
        def run(backend, name, *args, **kwargs):
            result = self.backend(backend, name, *args, **kwargs)
            if name == "read_lfi":
                Path(args[-1]).write_bytes(self.image[:16384])
                backend.failed = True
                raise toolkit.ToolkitError("USB timed out")
            return result

        output = self.base / "interrupted.zip"
        with self.simulated(run):
            result = self.ops.dump(output)
        self.assertFalse(result["complete"])
        self.assertEqual(self.calls[-1], "read_lfi")
        with zipfile.ZipFile(output) as zipped:
            self.assertEqual(len(zipped.read("lfi.bin")), 16384)
            self.assertNotIn("brec0.bin", zipped.namelist())

    def test_backend_timeout_and_zero_exit_protocol_error_end_session(self):
        for failure in (subprocess.TimeoutExpired("test", 1, output=b"partial transcript"),
                        subprocess.CompletedProcess([], 0, "unexpected response", "")):
            log = toolkit.new_run("backend-test")
            backend = toolkit.ActionsBackend(self.profile, self.runtime, log)
            with mock.patch.object(toolkit.subprocess, "run") as run:
                if isinstance(failure, Exception):
                    run.side_effect = failure
                else:
                    run.return_value = failure
                with self.assertRaises(toolkit.ToolkitError):
                    backend.run("uncertain", "write_flash", write=True)
                with self.assertRaisesRegex(toolkit.ToolkitError, "session stopped"):
                    backend.run("must_not_run", "reset")
                run.assert_called_once()


class ProcessTests(unittest.TestCase):
    def test_streamed_output_and_timeout_without_hardware(self):
        lines = []
        result = run_streamed([sys.executable, "-c", "import sys; sys.stdout.write('one\\rtwo\\n')"], os.environ.copy(), 5, lines.append)
        self.assertEqual(lines, ["one", "two"])
        self.assertEqual(result.returncode, 0)
        with self.assertRaises(subprocess.TimeoutExpired):
            run_streamed([sys.executable, "-c", "import time; time.sleep(10)"], os.environ.copy(), 0.1, lines.append)

    def test_other_app_instance_cannot_acquire_device(self):
        first, second = DeviceJobs(), DeviceJobs()
        token = first.acquire("test")
        try:
            with self.assertRaises(toolkit.ToolkitError):
                second.acquire("other app")
        finally:
            first.release(token)
        second.release(second.acquire("now available"))


if __name__ == "__main__":
    unittest.main()
