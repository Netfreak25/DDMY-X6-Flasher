"""Power-off RAM integrity, acknowledged loop start and stop behavior."""

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

CODE = bytes(range(toolkit.Profile.load().protocol["poweroff"]["size"]))


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
                        acknowledgement()]
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

    def test_hash_readback_then_switch_once_without_followup_usb_commands(self):
        usb = self.transport()
        message = poweroff.power_off(self.profile, self.runtime)
        self.assertEqual(message, "Start bestätigt. Jetzt USB-Kabel abziehen.")
        self.enter.assert_called_once_with(self.profile)
        self.open.assert_called_once_with(INSTANCE, self.profile.usb_timeout_ms)
        self.assertEqual(self.commands(usb), [reset.READ_RAM, reset.WRITE_RAM, reset.READ_RAM,
                                            reset.SWITCH])
        self.assertEqual(usb.writes[2], CODE)
        packets = [data for data in usb.writes if len(data) == 31]
        self.assertEqual(struct.unpack_from("<I", packets[3], 24)[0], int(self.profile.protocol["work_address"], 0))
        self.assertEqual(packets[-1][15:], struct.pack("<BIII3x", 0xCD, reset.SWITCH, 0,
                                                     int(self.profile.protocol["work_address"], 0)))
        self.assertFalse(usb.replies)
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

    def test_uncertain_upload_or_mismatched_readback_never_starts_loop(self):
        for replies in ([self.replies[0], TimeoutError("upload timeout")],
                        self.replies[:2] + [acknowledgement(bytes(len(CODE)))]):
            usb = self.transport(replies)
            with self.assertRaises((toolkit.ToolkitError, TimeoutError)):
                poweroff.power_off(self.profile, self.runtime)
            self.assertNotIn(reset.SWITCH, self.commands(usb))
            usb.close.assert_called_once()

    def test_missing_or_bad_start_ack_is_unconfirmed_without_retry(self):
        for reply in (OSError("disconnected"), TimeoutError("timeout"), acknowledgement(status=2)):
            with self.subTest(reply=reply):
                usb = self.transport(self.replies[:3] + [reply])
                with self.assertRaisesRegex(toolkit.ToolkitError, "Ausschaltaufruf nicht bestätigt"):
                    poweroff.power_off(self.profile, self.runtime)
                self.assertEqual(self.commands(usb).count(reset.SWITCH), 1)
                self.assertEqual(self.commands(usb)[-1], reset.SWITCH)
                usb.close.assert_called_once()

    def test_split_start_ack_finishes_without_waiting_for_poweroff_report(self):
        ack = self.replies[-1]
        usb = self.transport(self.replies[:-1] + [ack[:5], ack[5:]])
        self.assertIn("Jetzt USB-Kabel abziehen", poweroff.power_off(self.profile, self.runtime))
        self.assertEqual(self.commands(usb)[-1], reset.SWITCH)
        self.assertFalse(usb.replies)
        usb.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
