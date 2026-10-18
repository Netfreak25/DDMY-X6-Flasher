"""Generated BREC bytes only: checksum preparation and the native write boundary."""

import dataclasses
import hashlib
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import x6_brec_transfer as transfer
import x6_toolkit as toolkit


def synthetic_inputs():
    """Generate a finalized boot record and a driver stub; no device data."""
    profile = toolkit.Profile.load()
    data = bytearray(profile.brec_size)
    struct.pack_into("<4H", data, 0, 0, 0, 92, len(data) // profile.sector_size)
    data[0x80:0x85] = profile.nand_id
    struct.pack_into("<H", data, 0x210, 0x1234)
    struct.pack_into("<H", data, 2, transfer.word_sum(data[4:transfer.HEADER_SIZE]))
    checked = 92 * profile.sector_size
    checksum = (transfer.word_sum(data[2:checked]) + transfer.CHECKSUM_SEED) & transfer.WORD_MASK
    struct.pack_into("<H", data, 0, checksum)
    base = int(profile.protocol["work_address"], 0)
    driver = bytearray(transfer.DRIVER_HEADER_END - base)
    driver[transfer.DRIVER_HEADER_START - base:] = transfer.DRIVER_HEADER_CODE
    profile = dataclasses.replace(profile, runtime_sha256={"fwsc.bin": hashlib.sha256(driver).hexdigest()})
    return bytes(data), bytes(driver), profile


class BrecTransferTests(unittest.TestCase):
    def setUp(self):
        self.original, self.driver, self.profile = synthetic_inputs()

    def test_driver_adjustment_recovers_original_without_mutating_backup(self):
        prepared = transfer.prepare_transfer(self.original, self.driver, self.profile)
        self.assertEqual(prepared[2:], self.original[2:])
        adjusted = bytearray(prepared)
        header_sum = transfer.word_sum(adjusted[4:transfer.HEADER_SIZE])
        struct.pack_into("<H", adjusted, 2, header_sum)
        struct.pack_into("<H", adjusted, 0, (struct.unpack_from("<H", adjusted)[0] + 2 * header_sum) & 0xFFFF)
        self.assertEqual(adjusted, self.original)
        self.assertNotEqual(prepared, self.original)

    def test_corruption_and_prepared_input_are_rejected(self):
        corrupt = bytearray(self.original)
        corrupt[0x210] ^= 1
        prepared = transfer.prepare_transfer(self.original, self.driver, self.profile)
        for data in (self.original[:-1], bytes(corrupt), prepared):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                transfer.prepare_transfer(data, self.driver, self.profile)

    def test_code_checksum_does_not_hide_bad_finalized_header(self):
        data = bytearray(self.original)
        data[2] ^= 1
        struct.pack_into("<H", data, 0, (transfer.word_sum(data[2:92 * self.profile.sector_size]) + 0x1234) & 0xFFFF)
        with self.assertRaisesRegex(ValueError, "header checksum"):
            transfer.prepare_transfer(bytes(data), self.driver, self.profile)

    def test_wrong_driver_hash_and_changed_routine_are_rejected(self):
        driver = self.driver[:-1] + bytes([self.driver[-1] ^ 1])
        with self.assertRaisesRegex(ValueError, "FWSC hash"):
            transfer.prepare_transfer(self.original, driver, self.profile)
        profile = dataclasses.replace(self.profile, runtime_sha256={"fwsc.bin": hashlib.sha256(driver).hexdigest()})
        with self.assertRaisesRegex(ValueError, "routine"):
            transfer.prepare_transfer(self.original, driver, profile)

    def test_native_writer_sends_prepared_copy_once_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            original, driver = directory / "brec.bin", directory / "fwsc.bin"
            original.write_bytes(self.original)
            driver.write_bytes(self.driver)
            backend = mock.Mock(profile=self.profile)
            backend.file.return_value = driver
            backend.log.directory = directory
            sectors = len(self.original) // self.profile.sector_size
            backend.run.return_value = f"write_flash: {sectors}/{sectors} sectors"
            toolkit.write_area(backend, "write_brec", self.profile.protocol["brec_sector"], original)
            sent = Path(backend.run.call_args.args[-1])
            self.assertNotEqual(sent, original)
            self.assertEqual(sent.read_bytes(), transfer.prepare_transfer(self.original, self.driver, self.profile))
            self.assertEqual(original.read_bytes(), self.original)
            backend.run.assert_called_once()
            backend.run.reset_mock()
            with self.assertRaisesRegex(toolkit.ToolkitError, "preparation failed"):
                toolkit.write_area(backend, "write_brec", self.profile.protocol["brec_sector"], sent)
            backend.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
