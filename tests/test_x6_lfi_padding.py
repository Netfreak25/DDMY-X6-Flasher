"""Native-size allocation, source retention and Python/JavaScript equivalence."""

import dataclasses
import hashlib
import json
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

import test_x6_native as native_fixture
import x6_archive as archive
import x6_images as images
import x6_lfi as lfi
import x6_toolkit as toolkit

VECTORS = Path(__file__).with_name("lfi_padding_vectors.json")


def make_image(case):
    """Build checksummed named payloads and separately specified orphan padding."""
    data = bytearray(case["size"])
    struct.pack_into("<I", data, 0, lfi.LFI_MAGIC)
    for start, end, value in case.get("padding", []):
        data[start:end] = bytes([value]) * (end - start)
    for name, offset, length, value in case["entries"]:
        struct.pack_into("<I", data, offset, value)
        if case.get("catalog") and name == "COMMON.STY":
            struct.pack_into("<4sHHII", data, offset + 0x40, b"X6SC", 1, 1, length, 0)
    for index, (name, offset, length, _value) in enumerate(case["entries"]):
        pos = lfi.LFI_DIRECTORY_START + index * lfi.LFI_ENTRY_SIZE
        base, ext = name.split(".")
        data[pos:pos + 11] = (base.ljust(8) + ext.ljust(3)).encode("ascii")
        struct.pack_into("<II", data, pos + 16, offset // 512, length)
        struct.pack_into("<I", data, pos + 28, lfi.word_sum(data[offset:offset + length], 4))
    struct.pack_into("<I", data, 16, lfi.word_sum(data[512:8192], 4))
    struct.pack_into("<H", data, 510, lfi.word_sum(data[:510], 2))
    return bytes(data)


class PaddingTests(unittest.TestCase):
    def test_shared_vectors_preserve_payloads_and_are_idempotent(self):
        for case in json.loads(VECTORS.read_text()):
            with self.subTest(case=case["name"]):
                source = make_image(case)
                if case.get("error"):
                    with self.assertRaises(ValueError):
                        lfi.normalize_lfi(source)
                    continue
                changes = []
                result = lfi.normalize_lfi(source, changes)
                entries = lfi.lfi_entries(result)
                self.assertEqual([entry.length for entry in entries.values()], case["lengths"])
                self.assertEqual(len(result), len(source))
                self.assertEqual(lfi.normalize_lfi(result), result)
                self.assertEqual(8192 + sum(entry.length // 512 * 512 for entry in entries.values()), len(result))
                for name, offset, length, _value in case["entries"]:
                    expected = bytearray(source[offset:offset + length])
                    if case.get("catalog") and name == "COMMON.STY":
                        struct.pack_into("<I", expected, 0x48, entries[name].length)
                        self.assertTrue(changes[0]["catalog_size_updated"])
                    self.assertEqual(expected, result[offset:offset + length])
                for change in changes:
                    start, count = change["offset"], change["padding_bytes"]
                    self.assertFalse(any(result[start:start + count]))
                if result != source:
                    with self.assertRaisesRegex(ValueError, "size mismatch"):
                        lfi.lfi_entries(source)

    def test_bad_checksums_are_not_repaired(self):
        source = bytearray(make_image(json.loads(VECTORS.read_text())[1]))
        source[8192] ^= 1
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            lfi.normalize_lfi(source)

    def test_python_and_javascript_produce_identical_bytes_and_reports(self):
        script = """
import {readFileSync} from 'node:fs';
import {normalizeLfi,sha256} from './lib/formats.js';
import {makeImage} from './web-tests/lfi-padding-fixture.js';
const result=[];
for(const c of JSON.parse(readFileSync('tests/lfi_padding_vectors.json','utf8'))){
 try {const changes=[],out=normalizeLfi(makeImage(c),{lfi_size:c.size,sector_size:512},changes);result.push({name:c.name,sha256:await sha256(out),changes});}
 catch {result.push({name:c.name,error:true});}
}
console.log(JSON.stringify(result));
"""
        actual = json.loads(subprocess.check_output(["node", "--input-type=module", "-e", script],
                                                   cwd=VECTORS.parents[1], text=True))
        expected = []
        for case in json.loads(VECTORS.read_text()):
            changes = []
            try:
                result = lfi.normalize_lfi(make_image(case), changes)
                expected.append({"name": case["name"], "sha256": hashlib.sha256(result).hexdigest(), "changes": changes})
            except ValueError:
                expected.append({"name": case["name"], "error": True})
        self.assertEqual(actual, expected)

    def test_raw_import_keeps_original_and_updates_selection_hash(self):
        case = json.loads(VECTORS.read_text())[3]
        source = make_image(case)
        profile = dataclasses.replace(toolkit.Profile.load(), lfi_size=len(source))
        with tempfile.TemporaryDirectory(dir=toolkit.PRIVATE_ROOT) as directory:
            base = Path(directory)
            path = base / "input.bin"
            path.write_bytes(source)
            selection = images.select_images((path,), base / "selection", profile)
            staged = selection.files["lfi"]
            self.assertEqual(path.read_bytes(), source)
            self.assertEqual(staged.with_name(staged.stem + ".original.bin").read_bytes(), source)
            self.assertEqual(selection.hashes["lfi"], hashlib.sha256(lfi.normalize_lfi(source)).hexdigest())
            report = json.loads(staged.with_suffix(".normalization.json").read_text())
            self.assertEqual(report["changes"][0]["cleared_nonzero_bytes"], 512)


class BundlePaddingTests(unittest.TestCase):
    """Reuse only the boot/package fixture lifecycle, not unrelated tests."""

    setUp = native_fixture.NativeTests.setUp
    tearDown = native_fixture.NativeTests.tearDown
    manifest = native_fixture.NativeTests.manifest

    def test_bundle_authenticates_raw_hash_then_preserves_manifest(self):
        raw = bytearray(self.image)
        last = list(lfi.lfi_entries(raw).values())[-1]
        struct.pack_into("<I", raw, last.directory_offset + 20, 512)
        struct.pack_into("<I", raw, 16, lfi.word_sum(raw[512:8192], 4))
        struct.pack_into("<H", raw, 510, lfi.word_sum(raw[:510], 2))
        self.files["lfi.bin"] = bytes(raw)
        (self.source / "lfi.bin").write_bytes(raw)
        self.manifest()
        original_manifest = (self.source / "manifest.json").read_bytes()
        target = self.base / "normalized"
        manifest = archive.import_backup(self.source, target, self.profile)
        self.assertEqual((target / "original-lfi.bin").read_bytes(), raw)
        self.assertEqual((target / "original-manifest.json").read_bytes(), original_manifest)
        self.assertEqual(manifest["files"]["lfi.bin"]["sha256"], hashlib.sha256(self.image).hexdigest())
        self.assertEqual((target / "lfi.bin").read_bytes(), self.image)
        self.assertFalse(manifest["hardware_verified"])
        self.assertEqual(manifest["kind"], "firmware_candidate")
