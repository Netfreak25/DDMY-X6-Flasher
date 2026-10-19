"""Synthetic sector ownership and cross-language background allocation tests."""

import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import x6_lfi as lfi
import x6_storage as storage

FIXTURE = json.loads((ROOT / "tests/storage_fixture.json").read_text())


def fixture():
    """Build synthetic ROM-free bytes, preserving nontrivial directory metadata."""
    layout = storage.LAYOUT
    common = bytearray(layout["common_base_size"])
    struct.pack_into("<I", common, 0, layout["sty_magic"])
    count = layout["bitmap_base"] + layout["standard_count"]
    struct.pack_into("<H", common, layout["bitmap_count_offset"], count)
    offsets = [0]
    for index in range(layout["standard_count"]):
        pos = layout["bitmap_table"] + (layout["bitmap_base"] + index) * layout["bitmap_record"]
        offset = FIXTURE["bitmap_pixels_start"] + index * layout["pixel_bytes"]
        struct.pack_into("<IIHHH", common, pos, offset, layout["pixel_bytes"], layout["bitmap_format"], layout["width"], layout["height"])
        if index:
            common[offset:offset + layout["pixel_bytes"]] = bytes([index]) * layout["pixel_bytes"]
            offsets.append(offset)
    struct.pack_into("<4sHHII", common, layout["catalog_offset"], b"X6SC", 1, len(offsets), len(common), 0)
    struct.pack_into(f"<{len(offsets)}I", common, layout["catalog_table"], *offsets)
    config = bytearray(512)
    config[:4] = b"cfg\0"
    struct.pack_into("<II", config, layout["config_descriptor"], FIXTURE["config_value_offset"], layout["config_record_size"])
    struct.pack_into("<H", config, FIXTURE["config_value_offset"], len(offsets))
    resources = {"COMMON.STY": bytes(common), "CONFIG.BIN": bytes(config),
                 "USER2.AP": bytes(range(256)) * 2, "USER4.AP": bytes(512),
                 **{name: bytes([index + 31]) * size for index, (name, size) in enumerate(FIXTURE["dictionary_sizes"].items())}}
    image = bytearray(lfi.LFI_DIRECTORY_END + sum(map(len, resources.values())))
    struct.pack_into("<I", image, 0, lfi.LFI_MAGIC)
    cursor, positions = lfi.LFI_DIRECTORY_END, {}
    for name in FIXTURE["physical_files"]:
        raw = resources[name]
        positions[name] = cursor
        image[cursor:cursor + len(raw)] = raw
        cursor += len(raw)
    for index, name in enumerate(FIXTURE["files"]):
        at = lfi.LFI_DIRECTORY_START + index * lfi.LFI_ENTRY_SIZE
        base, ext = name.split(".")
        image[at:at + 11] = (base.ljust(8) + ext.ljust(3)).encode()
        image[at + 11:at + 16] = bytes([index + 1]) * 5
        struct.pack_into("<III", image, at + 16, positions[name] // 512, len(resources[name]), index + 123)
        struct.pack_into("<I", image, at + 28, lfi.word_sum(resources[name], 4))
    struct.pack_into("<I", image, 16, lfi.word_sum(image[512:8192], 4))
    struct.pack_into("<H", image, 510, lfi.word_sum(image[:510], 2))
    return bytes(image), {"lfi_size": len(image), "sector_size": 512}


def replace_resource(image, name, transform, profile):
    entry = lfi.lfi_entries(image)[name]
    raw = bytearray(image[entry.data_offset:entry.data_offset + entry.length])
    transform(raw)
    return storage.repack(image, {name: bytes(raw)}, profile=profile)


class StorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original, cls.profile = fixture()
        cls.freed = storage.remove_dictionaries(cls.original, cls.profile)
        cls.pixels = [struct.pack("<H", value) * (storage.LAYOUT["pixel_bytes"] // 2) for value in FIXTURE["added_pixels"]]

    def test_capacity_and_explicit_deletion_preserve_other_resources_and_metadata(self):
        self.assertEqual(storage.inspect_storage(self.original, self.profile)["free_bytes"], 0)
        report = storage.inspect_storage(self.freed, self.profile)
        self.assertEqual((report["free_bytes"], report["capacity"]), (308736, 2))
        before, after = lfi.lfi_entries(self.original), lfi.lfi_entries(self.freed)
        self.assertFalse(set(storage.LAYOUT["dictionaries"]) & after.keys())
        for name in set(before) & set(after):
            a, b = before[name], after[name]
            self.assertEqual(self.original[a.data_offset:a.data_offset + a.length], self.freed[b.data_offset:b.data_offset + b.length])
            for start, end in [(0, 16), (24, 28)]:
                self.assertEqual(self.original[a.directory_offset + start:a.directory_offset + end], self.freed[b.directory_offset + start:b.directory_offset + end])
        self.assertEqual(after["USER4.AP"].length, 512, "zero-filled AP is owned, not free")
        self.assertEqual(storage.remove_dictionaries(self.freed, self.profile), self.freed)

    def test_add_delete_compaction_reopen_and_full_capacity(self):
        first = storage.add_background(self.freed, self.pixels[0], self.profile)
        both = storage.add_background(first, self.pixels[1], self.profile)
        self.assertEqual(storage.inspect_storage(both, self.profile)["capacity"], 0)
        with self.assertRaisesRegex(ValueError, "Kein Platz"):
            storage.add_background(both, self.pixels[0], self.profile)
        removed = storage.delete_background(both, 11, self.profile)
        report = storage.inspect_storage(removed, self.profile)
        extra = report["backgrounds"][-1]
        self.assertEqual(removed[extra["absolute_offset"]:extra["absolute_offset"] + extra["size"]], self.pixels[1])
        self.assertEqual(storage.delete_background(removed, 11, self.profile), self.freed)
        self.assertEqual(storage.delete_background(first, 11, self.profile), self.freed)

    def test_black_and_standard_images_cannot_be_deleted(self):
        for index in (-1, 0, 1, 10, 11, 255, 11.5):
            with self.assertRaises(ValueError):
                storage.delete_background(self.freed, index, self.profile)

    def test_legacy_and_broken_catalogs_do_not_get_repaired_or_patched(self):
        legacy = replace_resource(self.freed, "COMMON.STY", lambda raw: raw.__setitem__(slice(64, 1024), bytes(960)), self.profile)
        self.assertFalse(storage.inspect_storage(legacy, self.profile)["supported"])
        with self.assertRaisesRegex(ValueError, "Kein unterstützter"):
            storage.add_background(legacy, self.pixels[0], self.profile)
        mutations = [lambda raw: struct.pack_into("<I", raw, 84, 0),
                     lambda raw: struct.pack_into("<I", raw, 72, len(raw) - 512),
                     lambda raw: raw.__setitem__(1000, 1),
                     lambda raw: struct.pack_into("<II", raw, 1024, len(raw), 16)]
        for mutation in mutations:
            broken = replace_resource(self.freed, "COMMON.STY", mutation, self.profile)
            with self.assertRaises(ValueError):
                storage.inspect_storage(broken, self.profile)
        bad_count = replace_resource(self.freed, "CONFIG.BIN", lambda raw: struct.pack_into("<H", raw, 128, 12), self.profile)
        with self.assertRaisesRegex(ValueError, "Bildanzahl"):
            storage.inspect_storage(bad_count, self.profile)

    def test_invalid_reserve_and_failed_edits_leave_input_untouched(self):
        before = hashlib.sha256(self.freed).digest()
        with self.assertRaises(ValueError):
            storage.add_background(self.freed, bytes(2), self.profile)
        with self.assertRaises(ValueError):
            storage.repack(self.freed, {"USER2.AP": bytes(len(self.freed))}, profile=self.profile)
        self.assertEqual(hashlib.sha256(self.freed).digest(), before)
        raw = bytearray(self.freed)
        entry = lfi.lfi_entries(self.freed)["X6FREE.BIN"]
        raw[entry.data_offset] = 1
        struct.pack_into("<I", raw, entry.directory_offset + 28, 1)
        struct.pack_into("<I", raw, 16, lfi.word_sum(raw[512:8192], 4))
        struct.pack_into("<H", raw, 510, lfi.word_sum(raw[:510], 2))
        with self.assertRaisesRegex(ValueError, "enthält Daten"):
            storage.inspect_storage(bytes(raw), self.profile)

    def test_exact_exhaustion_removes_empty_reserve(self):
        entry = lfi.lfi_entries(self.freed)["USER2.AP"]
        old = self.freed[entry.data_offset:entry.data_offset + entry.length]
        full = storage.repack(self.freed, {"USER2.AP": old + bytes(308736)}, profile=self.profile)
        self.assertNotIn("X6FREE.BIN", lfi.lfi_entries(full))
        self.assertEqual(storage.inspect_storage(full, self.profile)["free_bytes"], 0)

    @unittest.skipUnless(shutil.which("node"), "Node required for Python/JavaScript parity")
    def test_python_javascript_outputs_are_byte_identical(self):
        expected = storage.add_background(self.freed, self.pixels[0], self.profile)
        with tempfile.TemporaryDirectory() as directory:
            original, output = Path(directory) / "input.bin", Path(directory) / "output.bin"
            original.write_bytes(self.original)
            script = """import {readFileSync, writeFileSync} from 'node:fs';
import {removeDictionaries, addBackground} from './lib/storage.js';
const raw=new Uint8Array(readFileSync(process.argv[1]));
const profile={lfi_size:raw.length,sector_size:512};
const pixels=new Uint8Array(153600), view=new DataView(pixels.buffer);
for(let i=0;i<pixels.length;i+=2)view.setUint16(i,4660,true);
writeFileSync(process.argv[2],addBackground(removeDictionaries(raw,profile),pixels,profile));"""
            subprocess.run([shutil.which("node"), "--input-type=module", "-e", script, str(original), str(output)], cwd=ROOT, check=True, capture_output=True)
            self.assertEqual(output.read_bytes(), expected)


if __name__ == "__main__":
    unittest.main()
