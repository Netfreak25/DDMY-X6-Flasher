"""Optional upgrades, atomic rejection, and repeated application without hardware."""

import copy
import hashlib
import json
import os
import struct
import unittest
from pathlib import Path

import test_x6_artwork as artwork
import x6_artwork as art
import x6_lfi as lfi
import x6_screensaver as saver
import x6_screensaver_patch as patch


def sums(data):
    """Rechecksum synthetic resources after deliberate edits."""
    for pos in range(512, 8192, 32):
        if not data[pos]:
            break
        start, size = struct.unpack_from("<II", data, pos + 16)
        struct.pack_into("<I", data, pos + 28, lfi.word_sum(data[start * 512:start * 512 + size], 4))
    struct.pack_into("<I", data, 16, lfi.word_sum(data[512:8192], 4))
    struct.pack_into("<H", data, 510, lfi.word_sum(data[:510], 2))
    return bytes(data)


def fixture():
    components, device = artwork.fixture()
    config = copy.deepcopy(art.catalog())
    code_names = ("T0000003.BIN", "T0000004.BIN")
    raw = components["lfi"]
    entries = lfi.lfi_entries(raw)
    variants, versions = {}, {}
    for state, byte in (("install", 0), ("update", 17), ("current", 34)):
        data = bytearray(raw)
        hashes = {}
        for name in code_names:
            entry = entries[name]
            value = bytes([byte]) * 4 + bytes(entry.length - 4)
            data[entry.data_offset:entry.data_offset + entry.length] = value
            hashes[name] = hashlib.sha256(value).hexdigest()
        variants[state], versions[state] = hashes, sums(data)
    blob = hashlib.sha256(b'"' * 4).hexdigest()
    config["screensaver"]["patch"] = {"schema": 1, "lfi_size": len(raw), "guards": {}, "variants": variants,
        "writes": {name: [{"offset": 0, "blob": blob}] for name in code_names}, "blobs": {blob: (b'"' * 4).hex()}}
    config["screensaver"]["supported_code"] = variants["current"]
    return components, device, config, versions


class PatchTests(unittest.TestCase):
    def test_install_update_current_and_unknown_are_distinct(self):
        _components, _device, config, versions = fixture()
        for state, raw in versions.items():
            self.assertEqual(patch.inspect(raw, config), state)
            output = patch.apply(raw, config)
            self.assertEqual(output, versions["current"])
            self.assertEqual(patch.apply(output, config), output)
            self.assertEqual(len(lfi.lfi_entries(output)), 148)
        mixed = bytearray(versions["install"])
        entry = lfi.lfi_entries(mixed)["T0000003.BIN"]
        mixed[entry.data_offset:entry.data_offset + 4] = b'"' * 4
        mixed = sums(mixed)
        self.assertEqual(patch.inspect(mixed, config), "unsupported")
        with self.assertRaises(ValueError):
            patch.apply(mixed, config)

    def test_bad_payload_and_background_leave_input_untouched(self):
        _components, _device, config, versions = fixture()
        raw = bytearray(versions["install"])
        original = bytes(raw)
        invalid = copy.deepcopy(config)
        blob = next(iter(invalid["screensaver"]["patch"]["blobs"]))
        invalid["screensaver"]["patch"]["blobs"][blob] = "deadbeef"
        with self.assertRaisesRegex(ValueError, "Patchdaten"):
            patch.apply(raw, invalid)
        self.assertEqual(bytes(raw), original)
        entry = lfi.lfi_entries(raw)["COMMON.STY"]
        record = entry.data_offset + 1024 + 298 * 16
        struct.pack_into("<I", raw, record, 0)
        raw = sums(raw)
        with self.assertRaisesRegex(ValueError, "Hintergrundplatz"):
            patch.apply(raw, config)

    def test_picture_edits_do_not_change_detection_or_force_upgrade(self):
        components, device, config, versions = fixture()
        components["lfi"] = versions["install"]
        ranges = art.locate(components, device, config)
        item = ranges["background-01"]
        raw = bytearray(versions["install"])
        raw[item.start:item.end] = b"\xff" * (item.end - item.start)
        raw = sums(raw)
        self.assertEqual(patch.inspect(raw, config), "install")
        upgraded = patch.apply(raw, config)
        self.assertEqual(patch.inspect(upgraded, config), "current")
        self.assertNotEqual(upgraded[item.start:item.end], raw[item.start:item.end])
        self.assertEqual(patch.apply(upgraded, config), upgraded)

    @unittest.skipUnless(os.environ.get("X6_FIRMWARE_FIXTURES"), "private firmware fixtures not selected")
    def test_real_previous_and_unpatched_equal_audited_candidate(self):
        root = Path(os.environ["X6_FIRMWARE_FIXTURES"])
        config = json.loads((Path(__file__).parents[1] / "tools/x6_artwork.json").read_text())
        current = (root / "x6-loki-screensaver-v7-no-hello-final/lfi.bin").read_bytes()
        for name in ("x6-loki-video-petrol", "x6-loki-video-screensaver-final",
                     "x6-loki-video-screensaver-bankfix", "x6-loki-video-screensaver-scanline"):
            raw = (root / name / "lfi.bin").read_bytes()
            self.assertEqual(patch.apply(raw, config), current)
        self.assertEqual(patch.apply(current, config), current)
        hello = (root / "x6-loki-screensaver-v7-hello-padded/lfi.bin").read_bytes()
        previous = lfi.normalize_lfi((root / "x6-loki-screensaver-hello/lfi.bin").read_bytes())
        self.assertEqual(patch.apply(previous, config), hello)
        self.assertEqual(patch.apply(hello, config), hello)
        self.assertTrue(art.screensaver.supported(hello, lfi.lfi_entries(hello), config["screensaver"]))

    @unittest.skipUnless(os.environ.get("X6_FIRMWARE_FIXTURES"), "private firmware fixtures not selected")
    def test_catalog_count_and_wallpapers_do_not_change_v7_code_recognition(self):
        root = Path(os.environ["X6_FIRMWARE_FIXTURES"])
        config = art.catalog()
        current = (root / "x6-loki-screensaver-v7-no-hello-final/lfi.bin").read_bytes()
        entries = lfi.lfi_entries(current)
        common, native = entries["COMMON.STY"], entries["CONFIG.BIN"]
        style = current[common.data_offset:common.data_offset + common.length]
        offsets = saver.catalog_offsets(style)
        count_at = native.data_offset + saver.config_count_offset(current[native.data_offset:native.data_offset + native.length])
        black_pixels = struct.unpack_from("<I", style, config["sty"]["table_offset"] + 298 * config["sty"]["record_size"])[0]
        for selected in ((0,), offsets, (*offsets, black_pixels)):
            data = bytearray(current)
            struct.pack_into("<H", data, common.data_offset + saver.CATALOG_OFFSET + 6, len(selected))
            struct.pack_into(f"<{len(selected)}I", data, common.data_offset + saver.CATALOG_ENTRIES, *selected)
            struct.pack_into("<H", data, count_at, len(selected))
            if len(selected) > 1:
                data[common.data_offset + selected[1]:common.data_offset + selected[1] + 2] = b"\xff\xff"
            image = sums(data)
            self.assertEqual(patch.inspect(image, config), "current")
            self.assertEqual(patch.apply(image, config), image)
        broken_count = bytearray(current)
        struct.pack_into("<H", broken_count, count_at, 1)
        broken_count = sums(broken_count)
        self.assertEqual(patch.inspect(broken_count, config), "update")
        self.assertEqual(patch.apply(broken_count, config), current)
        bad_offset = bytearray(current)
        struct.pack_into("<I", bad_offset, common.data_offset + saver.CATALOG_ENTRIES + 4, common.length)
        self.assertEqual(patch.inspect(sums(bad_offset), config), "unsupported")
        ranges = art.locate({"lfi": current}, art.tk.Profile.load(), config)
        self.assertTrue(ranges["background-01"].slot["fixed_black"])


if __name__ == "__main__":
    unittest.main()
