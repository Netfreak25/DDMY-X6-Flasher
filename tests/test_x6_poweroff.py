"""Power-off RAM integrity, command order, returned values and stop behavior."""

import dataclasses
import hashlib
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import x6_poweroff as poweroff
import x6_reset as reset
import x6_toolkit as toolkit
from test_x6_reset import INSTANCE, PAYLOAD, FakeUsb, acknowledgement

CODE = bytes(range(196))
REPORT = struct.pack("<8s8I", b"X6OFFv1!", 3, 0, 0, 0, 0, 0x15, 0x01F00E1F, 0xC0)


class PowerOffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Path(self.temp.name)
        (self.runtime / poweroff.PAYLOAD_NAME).write_bytes(CODE)
        (self.runtime / "adfus.bin").write_bytes(PAYLOAD)
        profile = toolkit.Profile.load()
        config = {**profile.protocol["poweroff"], "sha256": hashlib.sha256(CODE).hexdigest()}
        self.profile = dataclasses.replace(profile, protocol={**profile.protocol, "poweroff": config},
                                           runtime_sha256={"adfus.bin": hashlib.sha256(PAYLOAD).hexdigest()})
        self.replies = [acknowledgement(PAYLOAD[:512]), acknowledgement(), acknowledgement(CODE),
                        acknowledgement(), acknowledgement(struct.pack("<I", 40)), acknowledgement(REPORT)]
        self.enter_patch = mock.patch.object(toolkit, "enter_adfu", return_value=toolkit.Device("adfu", instance_id=INSTANCE))
        self.enter = self.enter_patch.start()
        self.open_patch = mock.patch.object(reset, "WindowsUsb")
        self.open = self.open_patch.start()

    def tearDown(self):
        self.open_patch.stop()
        self.enter_patch.stop()
        self.temp.cleanup()

    def transport(self, replies=None):
        usb = FakeUsb(self.replies if replies is None else replies)
        usb.close = mock.Mock()
        self.open.return_value = usb
        return usb

    @staticmethod
    def commands(usb):
        return [struct.unpack_from("<I", data, 16)[0] for data in usb.writes if len(data) == 31]

    def test_hash_readback_execute_once_and_return_values_without_flash_or_reset(self):
        usb = self.transport()
        message = poweroff.power_off(self.profile, self.runtime)
        self.assertIn("Ausschaltcode ausgeführt", message)
        self.assertIn("USB ggf. abziehen", message)
        self.enter.assert_called_once_with(self.profile)
        self.open.assert_called_once_with(INSTANCE, self.profile.usb_timeout_ms)
        self.assertEqual(self.commands(usb), [reset.READ_RAM, reset.WRITE_RAM, reset.READ_RAM,
                                            reset.EXECUTE, reset.RETURN_SIZE, reset.READ_RETURN])
        self.assertEqual(usb.writes[2], CODE)
        packets = [data for data in usb.writes if len(data) == 31]
        self.assertEqual(struct.unpack_from("<I", packets[3], 24)[0], int(self.profile.protocol["work_address"], 0))
        self.assertEqual([data[12] for data in packets[-2:]], [0x80, 0x80])
        usb.close.assert_called_once()

    def test_corrupt_code_or_adfus_is_rejected_before_any_device_access(self):
        for filename in (poweroff.PAYLOAD_NAME, "adfus.bin"):
            with self.subTest(filename=filename):
                original = (self.runtime / filename).read_bytes()
                (self.runtime / filename).write_bytes(b"bad")
                with self.assertRaisesRegex(toolkit.ToolkitError, "beschädigt"):
                    poweroff.power_off(self.profile, self.runtime)
                self.enter.assert_not_called()
                self.open.assert_not_called()
                (self.runtime / filename).write_bytes(original)

    def test_wrong_identity_never_uploads(self):
        self.enter.return_value = toolkit.Device("adfu", instance_id=r"USB\VID_BAD\TEST")
        with self.assertRaisesRegex(toolkit.ToolkitError, "Gerätekennung"):
            poweroff.power_off(self.profile, self.runtime)
        self.open.assert_not_called()

    def test_uncertain_upload_or_mismatched_readback_never_executes(self):
        for replies in ([self.replies[0], TimeoutError("upload timeout")],
                        self.replies[:2] + [acknowledgement(bytes(len(CODE)))]):
            usb = self.transport(replies)
            with self.assertRaises((toolkit.ToolkitError, TimeoutError)):
                poweroff.power_off(self.profile, self.runtime)
            self.assertNotIn(reset.EXECUTE, self.commands(usb))
            usb.close.assert_called_once()

    def test_disconnect_after_execute_is_unconfirmed_without_retry(self):
        usb = self.transport(self.replies[:3] + [OSError("disconnected")])
        with self.assertRaisesRegex(toolkit.ToolkitError, "Ausschaltaufruf nicht bestätigt"):
            poweroff.power_off(self.profile, self.runtime)
        self.assertEqual(self.commands(usb).count(reset.EXECUTE), 1)
        self.assertEqual(self.commands(usb)[-1], reset.EXECUTE)
        usb.close.assert_called_once()

    def test_invalid_report_size_is_not_used_for_a_ram_read(self):
        usb = self.transport(self.replies[:4] + [acknowledgement(struct.pack("<I", 0xFFFFFFFF))])
        with self.assertRaisesRegex(toolkit.ToolkitError, "Rückmeldelänge"):
            poweroff.power_off(self.profile, self.runtime)
        self.assertNotIn(reset.READ_RETURN, self.commands(usb))
        usb.close.assert_called_once()

    def test_unknown_or_wrong_register_report_is_not_success(self):
        for report in (b"?" + REPORT[1:], REPORT[:-1] + b"?", REPORT[:-4]):
            with self.assertRaises(toolkit.ToolkitError):
                poweroff.validate_report(report, self.profile)


if __name__ == "__main__":
    unittest.main()
