"""Independent synthetic v2 layout, protected pixels and editor tail tests."""

import json
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import x6_artwork as art
from test_x6_storage import ROOT, fixture, lfi, replace_resource, storage


def skin_fixture(layout_version=1, role_count=2):
    image, profile = fixture()
    image = storage.remove_dictionaries(image, profile)
    entry = lfi.lfi_entries(image)["COMMON.STY"]
    raw = bytearray(image[entry.data_offset:entry.data_offset + entry.length])
    table, old_count = storage.bitmap_table(raw)
    new_table = len(raw)
    raw.extend(raw[table:table + old_count * 16])
    raw.extend(bytes(16))
    pixels = len(raw)
    raw.extend(b"\x45\x23" * (240 * 30))
    header_bytes = 32 if layout_version == 1 else 40
    extension, mapping = len(raw), len(raw) + header_bytes
    raw.extend(bytes(header_bytes))
    raw.extend(struct.pack(f"<{role_count*8}H", *([old_count] * (role_count*8))))
    palette_offset = len(raw)
    if layout_version == 2:
        raw.extend(struct.pack("<64H", *range(64)))
    raw.extend(bytes(-len(raw) % 512))
    struct.pack_into("<IIHHHH", raw, new_table + old_count * 16, pixels, 240 * 30 * 2, 2, 240, 30, 0)
    struct.pack_into("<IH", raw, 0x10, new_table, old_count + 1)
    struct.pack_into("<4sHHII", raw, 64, b"X6SC", 2, 11, len(raw), extension)
    struct.pack_into("<4sHHIHHIIII", raw, extension, b"X6SK", layout_version, header_bytes, len(raw), 8, role_count, mapping, role_count*16, 0, 0)
    if layout_version == 2:
        struct.pack_into("<II", raw, extension + 32, palette_offset, 128)
    return storage.repack(image, {"COMMON.STY": bytes(raw)}, profile=profile), profile


class SkinStorageTests(unittest.TestCase):
    layout_version = 1
    role_count = 2

    @classmethod
    def setUpClass(cls):
        cls.image, cls.profile = skin_fixture(cls.layout_version,cls.role_count)

    def test_roundtrip_preserves_entire_skin_prefix_and_file_allocation(self):
        report = storage.inspect_storage(self.image, self.profile)
        self.assertEqual(report["catalog_version"], 2)
        self.assertEqual(report["skin"]["skin_ids"], [309])
        pixels = b"\x76\x54" * (240 * 320)
        edited = storage.add_background(self.image, pixels, self.profile)
        after = storage.inspect_storage(edited, self.profile)
        a, b = lfi.lfi_entries(self.image)["COMMON.STY"], lfi.lfi_entries(edited)["COMMON.STY"]
        prefix = bytearray(edited[b.data_offset:b.data_offset + report["prefix_bytes"]])
        original = self.image[a.data_offset:a.data_offset + a.length]
        prefix[64:1024] = original[64:1024]
        self.assertEqual(prefix, original)
        extra = after["backgrounds"][-1]
        self.assertEqual(edited[extra["absolute_offset"]:extra["absolute_offset"] + extra["size"]], pixels)
        self.assertEqual(storage.delete_background(edited, 11, self.profile), self.image)

    def test_unknown_version_bad_prefix_bad_map_and_wallpaper_alias_are_rejected(self):
        report = storage.inspect_storage(self.image, self.profile)
        ext = report["skin"]["extension_offset"]
        mutations = [(68, "<H", 3), (76, "<I", 64), (ext + 8, "<I", report["prefix_bytes"] - 512),
                     (ext + 12, "<H", 7), (ext + 16, "<I", ext), (ext + 32, "<H", 298)]
        for at, fmt, value in mutations:
            with self.subTest(at=at):
                bad = replace_resource(self.image, "COMMON.STY",
                                       lambda raw, fmt=fmt, at=at, value=value: struct.pack_into(fmt, raw, at, value), self.profile)
                with self.assertRaises(ValueError):
                    storage.inspect_storage(bad, self.profile)

    def test_artwork_editor_follows_relocated_table_and_exposes_only_wallpapers(self):
        config = art.catalog()
        config["slots"] = [slot for slot in config["slots"] if slot.get("file") == "COMMON.STY"]
        pixels = b"\x76\x54" * (240 * 320)
        changed = storage.add_background(self.image, pixels, self.profile)
        slots = art.locate({"lfi": changed}, SimpleNamespace(**self.profile), config)
        self.assertEqual(len(slots), 12)
        self.assertTrue(slots["background-01"].slot["fixed_black"])
        extra = slots["background-12"]
        self.assertEqual(changed[extra.start:extra.end], pixels)

    @unittest.skipUnless(shutil.which("node"), "Node required for independent parser parity")
    def test_javascript_parser_and_add_delete_match_python(self):
        pixels = b"\x76\x54" * (240 * 320)
        expected = storage.add_background(self.image, pixels, self.profile)
        with tempfile.TemporaryDirectory() as directory:
            source, out = Path(directory) / "input.bin", Path(directory) / "output.bin"
            source.write_bytes(self.image)
            script = """import {readFileSync,writeFileSync} from 'node:fs';
import {inspectStorage,addBackground,deleteBackground} from './lib/storage.js';
import {view} from './lib/formats.js';
const input=new Uint8Array(readFileSync(process.argv[1])), profile={lfi_size:input.length,sector_size:512};
const pixels=new Uint8Array(153600);for(let i=0;i<pixels.length;i+=2)view(pixels).setUint16(i,0x5476,true);
const output=addBackground(input,pixels,profile);writeFileSync(process.argv[2],output);
if(Buffer.compare(Buffer.from(deleteBackground(output,11,profile)),Buffer.from(input)))throw Error('roundtrip');
console.log(JSON.stringify(inspectStorage(output,profile)));"""
            result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script, str(source), str(out)],
                                    cwd=ROOT, check=True, capture_output=True, text=True)
            self.assertEqual(out.read_bytes(), expected)
            self.assertEqual(json.loads(result.stdout)["catalog_version"], 2)


class SkinManyRolesStorageTests(SkinStorageTests):
    layout_version,role_count = 2,512

    def test_limit_applies_to_roles_without_reducing_native_id_bound(self):
        self.assertEqual(storage.inspect_storage(self.image,self.profile)["skin"]["role_count"],512)
        invalid,profile = skin_fixture(2,513)
        with self.assertRaises(ValueError):
            storage.inspect_storage(invalid,profile)


class SkinPaletteStorageTests(SkinStorageTests):
    layout_version = 2

    def test_palette_is_preserved_and_cannot_alias_map_or_wallpaper_tail(self):
        report = storage.inspect_storage(self.image, self.profile)
        skin = report["skin"]
        self.assertEqual(skin["palette_bytes"], 128)
        self.assertEqual(skin["layout_version"], 2)
        for at, value in ((skin["extension_offset"] + 32, skin["extension_offset"]),
                          (skin["extension_offset"] + 36, 126)):
            bad = replace_resource(self.image, "COMMON.STY",
                                   lambda raw, at=at, value=value: struct.pack_into("<I", raw, at, value), self.profile)
            with self.assertRaisesRegex(ValueError, "Farbtabelle"):
                storage.inspect_storage(bad, self.profile)


if __name__ == "__main__":
    unittest.main()
