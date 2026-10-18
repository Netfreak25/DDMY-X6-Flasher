"""Regression checks for X6 target selection and fail-closed USB orchestration."""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import x6_spti
import x6_toolkit as toolkit


class X6ToolkitTests(unittest.TestCase):
    """Protect device selection, command results, and bundle validation."""

    @classmethod
    def setUpClass(cls) -> None:
        """Load the same immutable X6 geometry as the CLI."""
        cls.profile = toolkit.Profile.load()
        toolkit.PRIVATE_ROOT.mkdir(exist_ok=True)

    def normal_probe(self) -> dict:
        """Return the internal flash and SD LUN under one USB serial."""
        serial = "X6TEST000001"
        return {
            "normal": [{"instance_id": f"USB\\VID_10D6&PID_1101\\{serial}"}],
            "adfu": [],
            "disks": [
                {"usb_serial": serial, "disk_size": self.profile.disk_size, "drives": ["I:"]},
                {"usb_serial": serial, "disk_size": 256355860480, "drives": ["J:"]},
            ],
        }

    def test_internal_flash_selected_while_sd_card_is_ignored(self) -> None:
        """The other ACTIONS LUN is the SD card of the same player."""
        device = toolkit.parse_probe(self.normal_probe(), self.profile)
        self.assertEqual((device.mode, device.drive, device.serial), (
            "normal", "I:", "X6TEST000001",
        ))

    def test_ambiguous_target_and_serial_mismatch_are_rejected(self) -> None:
        """Never send a vendor SCSI command to an unmatched volume."""
        state = self.normal_probe()
        state["disks"][0]["usb_serial"] = "OTHER"
        with self.assertRaises(toolkit.ToolkitError):
            toolkit.parse_probe(state, self.profile)
        state = self.normal_probe()
        state["disks"].append(state["disks"][0].copy())
        with self.assertRaises(toolkit.ToolkitError):
            toolkit.parse_probe(state, self.profile)
        state = self.normal_probe()
        state["normal"].append(state["normal"][0].copy())
        with self.assertRaises(toolkit.ToolkitError):
            toolkit.parse_probe(state, self.profile)
        with self.assertRaisesRegex(toolkit.ToolkitError, "bound X6"):
            toolkit.parse_probe(self.normal_probe(), dataclasses.replace(self.profile, bound_serial="OTHER"))

    def test_adfu_and_missing_modes(self) -> None:
        """ADFU can be identified without a mounted drive letter."""
        state = {"normal": [], "adfu": [{"instance_id": "USB\\VID_10D6&PID_10D6\\ABC"}], "disks": []}
        self.assertEqual(toolkit.parse_probe(state, self.profile).mode, "adfu")
        self.assertEqual(toolkit.parse_probe({"normal": [], "adfu": []}, self.profile).mode, "missing")

    def test_normal_usb_can_return_after_the_old_45_second_window(self) -> None:
        """A verified flash may re-enumerate after the previous wait expired."""
        elapsed = [0]
        normal = toolkit.Device("normal", "ABC")

        def advance(seconds: float) -> None:
            elapsed[0] += seconds

        with (
            mock.patch.object(toolkit.time, "monotonic", side_effect=lambda: elapsed[0]),
            mock.patch.object(toolkit.time, "sleep", side_effect=advance),
            mock.patch.object(toolkit, "probe_device", side_effect=lambda _profile: (
                normal if elapsed[0] >= 50 else toolkit.Device("missing")
            )),
        ):
            self.assertEqual(toolkit.wait_for_mode(self.profile, "normal", "ABC"), normal)
        self.assertEqual(elapsed[0], 50)

    def test_result_hexdump_must_match_announced_length(self) -> None:
        """A short USB response cannot be accepted as a driver handshake."""
        self.assertEqual(toolkit.result_bytes("result (3):\n41 42 43  |ABC|\n"), b"ABC")
        with self.assertRaises(toolkit.ToolkitError):
            toolkit.result_bytes("result (4):\n41 42 43  |ABC|\n")

    def test_adfu_reboot_stops_before_vendor_command_for_other_identity(self) -> None:
        """SCSI INQUIRY must precede both vendor requests."""
        fake_kernel = mock.Mock()
        with (
            mock.patch.object(x6_spti, "open_volume", return_value=(1, fake_kernel)),
            mock.patch.object(x6_spti, "inquiry", return_value=b"\0" * 36),
            mock.patch.object(x6_spti, "send_data_in") as send,
        ):
            with self.assertRaises(OSError):
                x6_spti.reboot_to_adfu("I:")
            send.assert_not_called()
            fake_kernel.CloseHandle.assert_called_once_with(1)

    def test_backend_does_not_retry_an_unconfirmed_write(self) -> None:
        """Exit zero cannot hide an unexpected USB status."""
        with tempfile.TemporaryDirectory(dir=toolkit.PRIVATE_ROOT) as temp:
            base = Path(temp)
            runtime = base / "runtime"
            runtime.mkdir()
            binary = runtime / "actions_dump.exe"
            binary.write_bytes(b"test backend")
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()
            profile = dataclasses.replace(self.profile, runtime_sha256={"actions_dump.exe": digest})
            backend = toolkit.ActionsBackend(profile, runtime, toolkit.RunLog(base / "run"))
            result = subprocess.CompletedProcess([], 0, "", "unexpected status\nwrite_flash failed\n")
            with mock.patch.object(toolkit.subprocess, "run", return_value=result) as process:
                with self.assertRaises(toolkit.ToolkitError):
                    backend.run("boot_write", "write_flash", "0x40000000", "0", "0", "mbrec.bin", write=True)
                process.assert_called_once()
            self.assertIn("command_end", (base / "run" / "events.jsonl").read_text(encoding="utf-8"))

    def test_backend_rejects_brec_checksum_warning_with_zero_exit(self) -> None:
        """The upstream BREC reader can return zero after a bad checksum."""
        with tempfile.TemporaryDirectory(dir=toolkit.PRIVATE_ROOT) as temp:
            base = Path(temp)
            runtime = base / "runtime"
            runtime.mkdir()
            binary = runtime / "actions_dump.exe"
            binary.write_bytes(b"test backend")
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()
            profile = dataclasses.replace(self.profile, runtime_sha256={"actions_dump.exe": digest})
            backend = toolkit.ActionsBackend(profile, runtime, toolkit.RunLog(base / "run"))
            result = subprocess.CompletedProcess([], 0, "bad brec checksum\n", "")
            with mock.patch.object(toolkit.subprocess, "run", return_value=result) as process:
                with self.assertRaisesRegex(toolkit.ToolkitError, "read_brec0"):
                    backend.run("read_brec0", "read_brec", "nandread.bin")
                process.assert_called_once()

    def test_incomplete_bundle_cannot_be_restored(self) -> None:
        """A partial dump is never accepted as a complete restore source."""
        with tempfile.TemporaryDirectory(dir=toolkit.PRIVATE_ROOT) as temp:
            path = Path(temp)
            toolkit.write_manifest(path, {
                "schema": 1, "model": self.profile.model,
                "nand_id": self.profile.nand_id.hex(),
                "status": "incomplete", "files": {},
            })
            with self.assertRaises(toolkit.ToolkitError):
                toolkit.verify_bundle(path, self.profile)

    def test_driver_load_order_and_nand_id_gate(self) -> None:
        """FWSC must load before NANDINFO, and a different chip stops first."""
        scan = bytearray(0x9C)
        scan[:2] = b"HW"
        scan[16:21] = self.profile.nand_id
        status = bytearray(92)
        status[6:12] = b"NAFWSC"

        def hexdump(data: bytes) -> str:
            lines = [data[index:index + 16].hex(" ") for index in range(0, len(data), 16)]
            return f"result ({len(data)}):\n" + "\n".join(lines) + "\n"

        backend = mock.Mock()
        backend.profile = self.profile
        backend.file.side_effect = lambda name: Path(name)
        backend.run.side_effect = [hexdump(scan), hexdump(status)]
        toolkit.start_fwsc(backend)
        self.assertEqual(backend.run.call_count, 2)
        command = backend.run.call_args.args
        self.assertLess(command.index("fwsc.bin"), command.index("nandinfo.bin"))

        scan[16:21] = b"\0" * 5
        backend.run.reset_mock()
        backend.run.side_effect = [hexdump(scan)]
        with self.assertRaises(toolkit.ToolkitError):
            toolkit.start_fwsc(backend)
        backend.run.assert_called_once()

    def test_write_area_requires_final_acknowledged_sector_count(self) -> None:
        """The final progress line must agree with the exact file size."""
        with tempfile.TemporaryDirectory(dir=toolkit.PRIVATE_ROOT) as temp:
            image = Path(temp) / "record.bin"
            image.write_bytes(bytes(self.profile.sector_size * 2))
            backend = mock.Mock()
            backend.profile = self.profile
            backend.run.return_value = "write_flash: 2/2 sectors\n"
            toolkit.write_area(backend, "write_mbrec", "0x40000000", image)
            self.assertEqual(backend.run.call_count, 1)
            self.assertTrue(backend.run.call_args.kwargs["write"])
            backend.run.reset_mock()
            backend.run.return_value = "write_flash: 1/2 sectors\n"
            with self.assertRaises(toolkit.ToolkitError):
                toolkit.write_area(backend, "write_mbrec", "0x40000000", image)
            backend.run.assert_called_once()

    def test_full_restore_is_gated_before_usb_without_boot_mapping(self) -> None:
        """A normal restore cannot silently assume 0x46 mirrors both BREC copies."""
        with (
            tempfile.TemporaryDirectory(dir=toolkit.PRIVATE_ROOT) as temp,
            mock.patch.object(toolkit, "verify_bundle", return_value={"status": "complete"}),
            mock.patch.object(toolkit, "probe_device") as probe,
            self.assertRaisesRegex(toolkit.ToolkitError, "not verified"),
        ):
            toolkit.restore_full(self.profile, toolkit.DEFAULT_RUNTIME, Path(temp), False)
        probe.assert_not_called()

    def test_failed_dump_keeps_an_incomplete_manifest(self) -> None:
        """A USB failure cannot leave a restore-ready bundle label behind."""
        with (
            tempfile.TemporaryDirectory(dir=toolkit.PRIVATE_ROOT) as temp,
            mock.patch.object(toolkit, "probe_device", return_value=toolkit.Device("normal", "ABC")),
            mock.patch.object(toolkit, "enter_adfu", side_effect=toolkit.ToolkitError("USB down")),
        ):
            destination = Path(temp) / "dump"
            with self.assertRaises(toolkit.ToolkitError):
                toolkit.dump(self.profile, toolkit.DEFAULT_RUNTIME, destination)
            manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "incomplete")

    def test_dump_reads_second_brec_after_first_copy_fails(self) -> None:
        """A failed primary BREC must not hide a readable backup copy."""
        with (
            tempfile.TemporaryDirectory(dir=toolkit.PRIVATE_ROOT) as temp,
            mock.patch.object(toolkit, "probe_device", return_value=toolkit.Device("normal", "ABC")),
            mock.patch.object(toolkit, "enter_adfu", return_value=toolkit.Device("adfu", "ADFU")),
            mock.patch.object(toolkit, "ActionsBackend", return_value=mock.Mock()),
            mock.patch.object(toolkit, "start_adfus"),
            mock.patch.object(toolkit, "start_fwsc"),
            mock.patch.object(toolkit, "read_lfi", return_value={"size": 1, "sha256": "LFI"}),
            mock.patch.object(toolkit, "read_boot_copy", side_effect=[
                toolkit.ToolkitError("bad brec checksum"),
                {"mbrec_copy.bin": {"size": 1024}, "brec1.bin": {"size": 201216}},
            ]) as read_copy,
        ):
            destination = Path(temp) / "dump"
            with self.assertRaisesRegex(toolkit.ToolkitError, "boot read incomplete"):
                toolkit.dump(self.profile, toolkit.DEFAULT_RUNTIME, destination)
            manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "incomplete")
            self.assertEqual(manifest["steps"]["brec0"]["status"], "failed")
            self.assertEqual(manifest["steps"]["brec1"]["status"], "ok")
            self.assertIn("brec1.bin", manifest["files"])
            self.assertEqual([call.args[2] for call in read_copy.call_args_list], [0, 1])

    def test_flash_aborts_before_finalize_after_uncertain_write(self) -> None:
        """An unconfirmed LFI write is never finalized, read back, or rebooted."""
        with (
            tempfile.TemporaryDirectory(dir=toolkit.PRIVATE_ROOT) as temp,
            mock.patch.object(toolkit, "validate_lfi", return_value={"size": 512, "sha256": "test", "files": 148}),
            mock.patch.object(toolkit, "probe_device", return_value=toolkit.Device("normal", "ABC")),
            mock.patch.object(toolkit, "enter_adfu"),
            mock.patch.object(toolkit, "start_adfus"),
            mock.patch.object(toolkit, "start_fwsc"),
            mock.patch.object(toolkit, "read_lfi") as read,
            mock.patch.object(toolkit, "check_preserved_header"),
            mock.patch.object(toolkit, "write_area", side_effect=toolkit.ToolkitError("USB status uncertain")) as write,
            mock.patch.object(toolkit, "finalize_flash") as finalize,
            mock.patch.object(toolkit, "reset_after_verify") as reset,
            mock.patch.object(toolkit, "ActionsBackend"),
        ):
            candidate = Path(temp) / "candidate.bin"
            candidate.write_bytes(bytes(512))
            with self.assertRaisesRegex(toolkit.ToolkitError, "USB status uncertain"):
                toolkit.flash_lfi(self.profile, toolkit.DEFAULT_RUNTIME, candidate)
            read.assert_called_once()
            write.assert_called_once()
            finalize.assert_not_called()
            reset.assert_not_called()

    def test_unverified_restore_returns_distinct_exit_status(self) -> None:
        """Automation must not mistake unavailable boot readback for verification."""
        outcome = toolkit.RestoreOutcome(Path("private_dumps/run"), False, True, False)
        with (
            mock.patch.object(toolkit, "restore_full", return_value=outcome),
            mock.patch.object(sys, "stderr", new_callable=io.StringIO) as errors,
        ):
            self.assertEqual(toolkit.main(["restore-full", "private_dumps/toolkit/original", "--emergency"]), 2)
            self.assertIn("UNVERIFIED", errors.getvalue())

    def test_full_restore_order_and_both_boot_readbacks(self) -> None:
        """Normal restore stages MBREC, BREC, LFI, finalize, then checks both copies."""
        steps: list[str] = []
        profile = dataclasses.replace(self.profile, boot_write_mapping_verified=True)
        with tempfile.TemporaryDirectory(dir=toolkit.PRIVATE_ROOT) as temp:
            source = Path(temp) / "source"
            source.mkdir()

            def read_firmware(_backend: object, path: Path, name: str) -> dict:
                steps.append(name)
                path.write_bytes(b"fake readback")
                return {}

            def read_records(_backend: object, _directory: Path) -> dict:
                steps.append("read_boot")
                return {}

            with (
                mock.patch.object(toolkit, "verify_bundle", return_value={
                    "status": "complete", "device_serial": "ABC",
                }),
                mock.patch.object(toolkit, "new_run", return_value=toolkit.RunLog(Path(temp) / "run")),
                mock.patch.object(toolkit, "probe_device", return_value=toolkit.Device("normal", "ABC")),
                mock.patch.object(toolkit, "enter_adfu"),
                mock.patch.object(toolkit, "ActionsBackend", return_value=mock.Mock()),
                mock.patch.object(toolkit, "start_adfus", side_effect=lambda _backend: steps.append("adfus")),
                mock.patch.object(toolkit, "start_fwsc", side_effect=lambda _backend: steps.append("fwsc")),
                mock.patch.object(toolkit, "read_boot", side_effect=read_records),
                mock.patch.object(toolkit, "read_lfi", side_effect=read_firmware),
                mock.patch.object(
                    toolkit, "write_area", side_effect=lambda _backend, name, _sector, _path: steps.append(name),
                ),
                mock.patch.object(toolkit, "finalize_flash", side_effect=lambda _backend: steps.append("finalize")),
                mock.patch.object(toolkit, "compare_files"),
                mock.patch.object(toolkit, "reset_after_verify", side_effect=lambda *_args: steps.append("reset")),
            ):
                outcome = toolkit.restore_full(profile, toolkit.DEFAULT_RUNTIME, source, False)
            self.assertTrue(outcome.boot_verified)
            self.assertEqual(steps, [
                "adfus", "read_boot", "fwsc", "read_before", "write_mbrec",
                "write_brec", "write_lfi", "finalize", "read_after", "read_boot", "reset",
            ])


if __name__ == "__main__":
    unittest.main()
