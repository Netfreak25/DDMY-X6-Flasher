"""Reset packet, Windows API and failure tests; never touch a real device."""

import ctypes
import dataclasses
import hashlib
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import x6_reset as reset
import x6_toolkit as toolkit

PAYLOAD = bytes(range(256)) * 4
INSTANCE = r"USB\VID_10D6&PID_10D6\TEST"


def acknowledgement(data=b"", signature=reset.CSW_SIGNATURE, tag=0, residue=0, status=0):
    return data + struct.pack("<IIIB", signature, tag, residue, status)


class FakeUsb:
    def __init__(self, replies):
        self.replies, self.writes = list(replies), []

    def write(self, data):
        self.writes.append(data)

    def read(self, length):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.profile = dataclasses.replace(toolkit.Profile.load(),
                                           runtime_sha256={"adfus.bin": hashlib.sha256(PAYLOAD).hexdigest()})
        self.replies = [acknowledgement(bytes(512)), acknowledgement(reset.INFO_SIGNATURE + bytes(9)),
                        acknowledgement(), acknowledgement(), acknowledgement(),
                        acknowledgement(PAYLOAD[:512]), acknowledgement()]

    def session(self, replies=None):
        usb = FakeUsb(self.replies if replies is None else replies)
        return reset.ResetSession(usb, self.profile), usb

    def test_exact_sequence_and_split_replies(self):
        replies = [fragment for reply in self.replies for fragment in (reply[:5], reply[5:])]
        session, usb = self.session(replies)
        session.reset(PAYLOAD)
        address = int(self.profile.protocol["adfus_address"], 0)
        info = bytes.fromhex("cc000000000000120000000000000000")
        cdbs = [struct.pack("<BIII3x", 0xCD, reset.READ_RAM, 512, address), info,
                struct.pack("<BIII3x", 0xCD, reset.WRITE_RAM, 512, address),
                struct.pack("<BIII3x", 0xCD, reset.WRITE_RAM, 512, address + 512),
                struct.pack("<BIII3x", 0xCD, reset.SWITCH, 0, address),
                struct.pack("<BIII3x", 0xCD, reset.READ_RAM, 512, address), bytes([0xB0]) + bytes(15)]
        packets = [data for data in usb.writes if len(data) == 31]
        self.assertEqual([packet[15:] for packet in packets], cdbs)
        self.assertEqual([struct.unpack("<IIIBBB", packet[:15]) for packet in packets],
                         [(reset.CBW_SIGNATURE, 0, length, flag, 0, 16) for length, flag in
                          ((512, 128), (18, 128), (512, 0), (512, 0), (0, 0), (512, 128), (0, 0))])
        self.assertEqual(usb.writes[3], PAYLOAD[:512])
        self.assertEqual(usb.writes[5], PAYLOAD[512:])
        self.assertFalse(usb.replies)

    def test_bad_identity_or_ram_never_sends_reset(self):
        for index, replacement in ((1, acknowledgement(bytes(18))), (5, acknowledgement(bytes(512)))):
            with self.subTest(index=index):
                replies = list(self.replies)
                replies[index] = replacement
                session, usb = self.session(replies)
                with self.assertRaises(toolkit.ToolkitError):
                    session.reset(PAYLOAD)
                self.assertFalse(any(len(data) == 31 and data[15] == 0xB0 for data in usb.writes))

    def test_final_ram_block_is_not_padded(self):
        session, usb = self.session()
        payload = PAYLOAD[:-12]
        session.reset(payload)
        self.assertEqual(struct.unpack_from("<I", usb.writes[4], 8)[0], 500)
        self.assertEqual(usb.writes[5], payload[512:])

    def test_existing_adfus_is_reset_without_unsupported_info_or_ram_writes(self):
        session, usb = self.session([acknowledgement(PAYLOAD[:512]), acknowledgement()])
        session.reset(PAYLOAD)
        self.assertEqual(len(usb.writes), 2)
        self.assertEqual(usb.writes[0][15:17], bytes([0xCD, reset.READ_RAM]))
        self.assertEqual(usb.writes[1][15:], bytes([0xB0]) + bytes(15))

    def test_unresponsive_ram_probe_does_not_trigger_fallback_commands(self):
        session, usb = self.session([TimeoutError("RAM probe timeout")])
        with self.assertRaises(TimeoutError):
            session.reset(PAYLOAD)
        self.assertEqual(len(usb.writes), 1)

    def test_status_timeout_disconnect_and_short_reply_stop_without_retry(self):
        failures = [acknowledgement(signature=0), acknowledgement(tag=1), acknowledgement(residue=1),
                    acknowledgement(status=1), acknowledgement() + b"extra", b"",
                    TimeoutError("timeout"), OSError("disconnected")]
        for reply in failures:
            with self.subTest(reply=repr(reply)):
                session, usb = self.session(self.replies[:2] + [reply])
                with self.assertRaises((toolkit.ToolkitError, OSError)):
                    session.reset(PAYLOAD)
                self.assertEqual(len(usb.writes), 4)  # RAM probe, INFO, first CBW and its RAM data
                with self.assertRaisesRegex(toolkit.ToolkitError, "Sitzung ist beendet"):
                    session.transaction(bytes(16))
                self.assertEqual(len(usb.writes), 4)

    def test_reset_without_acknowledgement_is_not_repeated(self):
        session, usb = self.session(self.replies[:-1] + [TimeoutError("timeout")])
        with self.assertRaisesRegex(toolkit.ToolkitError, "Reset wurde nicht bestätigt"):
            session.reset(PAYLOAD)
        self.assertEqual(sum(len(data) == 31 and data[15] == 0xB0 for data in usb.writes), 1)

    def test_no_access_before_hash_and_mode_validation(self):
        with mock.patch.object(toolkit, "probe_device") as probe, mock.patch.object(reset, "WindowsUsb") as usb:
            with self.assertRaisesRegex(toolkit.ToolkitError, "beschädigt"):
                reset.leave_adfu(self.profile, b"invalid")
            probe.assert_not_called()
            for device in (toolkit.Device("missing"), toolkit.Device("adfu", instance_id=r"USB\VID_BAD\TEST")):
                probe.return_value = device
                with self.assertRaises(toolkit.ToolkitError):
                    reset.leave_adfu(self.profile, PAYLOAD)
            probe.return_value = toolkit.Device("normal")
            self.assertIn("bereits", reset.leave_adfu(self.profile, PAYLOAD))
            usb.assert_not_called()

    def test_close_before_wait_and_no_success_without_normal_mode(self):
        events = []
        with mock.patch.object(toolkit, "probe_device", return_value=toolkit.Device("adfu", instance_id=INSTANCE)), \
                mock.patch.object(reset, "WindowsUsb") as usb, mock.patch.object(reset, "ResetSession") as session, \
                mock.patch.object(toolkit, "wait_for_mode") as wait:
            usb.return_value.close.side_effect = lambda: events.append("close")
            session.return_value.reset.side_effect = lambda payload: events.append("reset")
            wait.side_effect = lambda *args: events.append("wait")
            self.assertIn("Normalmodus erkannt", reset.leave_adfu(self.profile, PAYLOAD))
            self.assertEqual(events, ["reset", "close", "wait"])
            usb.assert_called_once_with(INSTANCE, self.profile.usb_timeout_ms)
            wait.assert_called_once_with(self.profile, "normal")
            wait.side_effect = toolkit.ToolkitError("timeout")
            with self.assertRaisesRegex(toolkit.ToolkitError, "nicht erkannt"):
                reset.leave_adfu(self.profile, PAYLOAD)
            wait.reset_mock()
            session.return_value.reset.side_effect = toolkit.ToolkitError("unconfirmed")
            with self.assertRaisesRegex(toolkit.ToolkitError, "unconfirmed"):
                reset.leave_adfu(self.profile, PAYLOAD)
            wait.assert_not_called()
            self.assertEqual(usb.return_value.close.call_count, 3)


class WindowsTransportTests(unittest.TestCase):
    def setUp(self):
        self.kernel, self.api = mock.Mock(), mock.Mock()
        self.kernel.CreateFileW.return_value = 42
        self.api.WinUsb_Initialize.side_effect = lambda file, handle: setattr(handle._obj, "value", 43) or True
        self.api.WinUsb_GetCurrentAlternateSetting.side_effect = lambda handle, alt: setattr(alt._obj, "value", 0) or True

        def descriptor(handle, alt, result):
            result._obj.usb_class, result._obj.endpoints = reset.USB_VENDOR_CLASS, 2
            return True

        def pipe(handle, alt, index, result):
            result._obj.type, result._obj.id = reset.USB_BULK, (0x81, 0x02)[index]
            return True

        self.api.WinUsb_QueryInterfaceSettings.side_effect = descriptor
        self.api.WinUsb_QueryPipe.side_effect = pipe

    def open(self):
        with mock.patch.object(reset.ctypes, "WinDLL", side_effect=[self.kernel, self.api]), \
                mock.patch.object(reset, "interface_path", return_value="test-path"):
            return reset.WindowsUsb(INSTANCE, 30000)

    def test_native_layout_exclusive_open_and_timeout_on_both_pipes(self):
        self.assertEqual([ctypes.sizeof(item) for item in
                          (reset.Guid, reset.InterfaceDescriptor, reset.PipeInformation)], [16, 9, 12])
        usb = self.open()
        self.kernel.CreateFileW.assert_called_once_with("test-path", reset.GENERIC_READ_WRITE, 0,
                                                        None, reset.OPEN_EXISTING, reset.FILE_FLAG_OVERLAPPED, None)
        self.assertEqual((usb.input, usb.output), (0x81, 2))
        self.assertEqual(self.kernel.CreateFileW.restype, reset.wt.HANDLE)
        self.assertEqual([call.args[1] for call in self.api.WinUsb_SetPipePolicy.call_args_list], [0x81, 2])
        self.assertTrue(all(call.args[-1]._obj.value == 30000 for call in self.api.WinUsb_SetPipePolicy.call_args_list))
        usb.close()
        usb.close()
        self.kernel.CloseHandle.assert_called_once_with(42)
        self.api.WinUsb_Free.assert_called_once()

    def test_exclusive_open_failure_never_initializes_or_sends(self):
        self.kernel.CreateFileW.return_value = reset.INVALID_HANDLE
        with self.assertRaisesRegex(toolkit.ToolkitError, "exklusiv"):
            self.open()
        self.api.WinUsb_Initialize.assert_not_called()
        self.api.WinUsb_WritePipe.assert_not_called()

    def test_initialization_failure_closes_handle(self):
        self.api.WinUsb_Initialize.side_effect = None
        self.api.WinUsb_Initialize.return_value = False
        with self.assertRaises(toolkit.ToolkitError):
            self.open()
        self.kernel.CloseHandle.assert_called_once_with(42)
        self.api.WinUsb_WritePipe.assert_not_called()

    def test_short_write_and_empty_read_are_errors(self):
        usb = self.open()
        self.api.WinUsb_WritePipe.return_value = True
        self.api.WinUsb_ReadPipe.return_value = True
        with self.assertRaisesRegex(toolkit.ToolkitError, "teilweise"):
            usb.write(b"abcd")
        self.api.WinUsb_WritePipe.assert_called_once()
        with self.assertRaisesRegex(toolkit.ToolkitError, "Leere"):
            usb.read(1024)
        usb.close()


class InterfaceDiscoveryTests(unittest.TestCase):
    def test_instance_filter_registry_guid_and_present_flag(self):
        cfg = mock.Mock()
        path = r"\\?\usb#vid_10d6&pid_10d6#TEST#{11111111-2222-3333-4444-555555555555}"

        def size(count, guid, instance, flags):
            self.assertEqual((instance, flags), (INSTANCE, reset.PRESENT_INTERFACES))
            self.assertEqual(bytes(guid._obj), reset.uuid.UUID("11111111-2222-3333-4444-555555555555").bytes_le)
            count._obj.value = len(path) + 2
            return 0

        def listing(guid, instance, buffer, length, flags):
            self.assertEqual((instance, flags), (INSTANCE, reset.PRESENT_INTERFACES))
            buffer.value = path
            return 0

        cfg.CM_Get_Device_Interface_List_SizeW.side_effect = size
        cfg.CM_Get_Device_Interface_ListW.side_effect = listing
        with mock.patch.object(reset.winreg, "OpenKey") as registry, \
                mock.patch.object(reset.winreg, "QueryValueEx", return_value=(["{11111111-2222-3333-4444-555555555555}"], 7)), \
                mock.patch.object(reset.ctypes, "WinDLL", return_value=cfg):
            self.assertEqual(reset.interface_path(INSTANCE), path)
            registry.assert_called_once_with(reset.winreg.HKEY_LOCAL_MACHINE,
                                              rf"SYSTEM\CurrentControlSet\Enum\{INSTANCE}\Device Parameters")
            cfg.CM_Get_Device_Interface_ListW.side_effect = None
            cfg.CM_Get_Device_Interface_ListW.return_value = 0
            with self.assertRaisesRegex(toolkit.ToolkitError, "eindeutige"):
                reset.interface_path(INSTANCE)
            cfg.CM_Get_Device_Interface_ListW.return_value = 26  # CR_BUFFER_SMALL: no retry with stale identity
            with self.assertRaisesRegex(toolkit.ToolkitError, "geändert"):
                reset.interface_path(INSTANCE)


if __name__ == "__main__":
    unittest.main()
