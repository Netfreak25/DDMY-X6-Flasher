"""Synthetic artwork fixtures and offline byte-boundary regression tests."""

import dataclasses
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageCms

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import x6_artwork as art
import x6_images as images
import x6_lfi as lfi
import x6_picture as picture
import x6_toolkit as tk
from test_x6_brec_transfer import synthetic_inputs


def fixture():
    """Generate valid blank resources, boot records and 148 files without ROM data."""
    config = art.catalog()
    raw = bytearray(3 * 1024 * 1024)
    struct.pack_into("<I", raw, 0, lfi.LFI_MAGIC)
    offset = lfi.LFI_DIRECTORY_END
    for index in range(lfi.X6_FILE_COUNT):
        name = ["COMMON.STY", "LOGO.STY", "END.STY"][index] if index < 3 else f"T{index:07d}.BIN"
        slots = [slot for slot in config["slots"] if slot.get("file") == name]
        content = bytearray(4)
        if slots:
            count = max(slot["index"] for slot in slots) + 1
            table_end = 1024 + count * 16
            content = bytearray(table_end + len(slots) * config["pixel_bytes"])
            struct.pack_into("<I", content, 0, config["sty"]["magic"])
            struct.pack_into("<H", content, 20, count)
            for nth, slot in enumerate(slots):
                struct.pack_into("<IIHHH", content, 1024 + slot["index"] * 16,
                                 table_end + nth * config["pixel_bytes"], config["pixel_bytes"], 2, 240, 320)
        pos = 512 + index * 32
        base, ext = name.split(".")
        raw[pos:pos + 11] = (base.ljust(8) + ext).encode("ascii")
        struct.pack_into("<II", raw, pos + 16, offset // 512, len(content))
        struct.pack_into("<I", raw, pos + 28, lfi.word_sum(content, 4))
        raw[offset:offset + len(content)] = content
        offset += (len(content) + 511) // 512 * 512
    struct.pack_into("<I", raw, 16, lfi.word_sum(raw[512:8192], 4))
    struct.pack_into("<H", raw, 510, lfi.word_sum(raw[:510], 2))
    brec, _driver, profile = synthetic_inputs()
    brec = bytearray(brec)
    brec[-512:-508] = b"RES\x19"
    struct.pack_into(">HH", brec, len(brec) - 508, 240, 320)
    mbrec = bytearray(1024)
    mbrec[2:5] = b"\x5a\x02\x03"
    struct.pack_into("<HH", mbrec, 12, 256, 8192)
    return {"lfi": bytes(raw), "brec": bytes(brec), "mbrec": bytes(mbrec)}, dataclasses.replace(profile, lfi_size=len(raw))


class ArtworkTests(unittest.TestCase):
    def setUp(self):
        tk.PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=tk.PRIVATE_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.components, self.profile = fixture()
        files = {}
        for area, data in self.components.items():
            path = self.base / f"{area}.bin"
            path.write_bytes(data)
            files[area] = path
        self.selection = images.Selection(files, {area: tk.sha256_file(path) for area, path in files.items()}, tuple(files.values()))
        self.editor = art.Artwork(self.selection, self.profile)
        self.red = bytes((255, 0, 0)) * 240 * 320

    def test_shared_vectors(self):
        vectors = json.loads((Path(__file__).parent / "artwork_vectors.json").read_text())
        for value in vectors:
            for endian, key in (("little", "le"), ("big", "be")):
                encoded = art.encode_rgb565(bytes(value["rgb"]), endian)
                self.assertEqual(encoded, bytes(value[key]))
                self.assertEqual(art.decode_rgb565(encoded, endian), bytes(value["decoded"]))

    def test_all_slots_noop_and_reset_exact(self):
        self.assertEqual(len(self.editor.ranges), 14)
        self.assertEqual(self.editor.build(), self.components)
        for key in self.editor.ranges:
            self.editor.replace(key, self.red)
            self.assertEqual(self.editor.rgb(key), self.red)
            self.editor.reset(key)
            self.assertEqual(self.editor.edits, {})
        self.assertEqual(self.editor.build(), self.components)

    def test_coupling_byte_orders_and_exact_allowed_changes(self):
        self.editor.replace("startup", self.red)
        self.assertEqual(set(self.editor.edits), {"startup", "boot-startup"})
        self.assertEqual(self.editor.rgb("startup"), self.editor.rgb("boot-startup"))
        self.assertEqual(self.editor.pixels("startup")[:2], b"\0\xf8")
        self.assertEqual(self.editor.pixels("boot-startup")[:2], b"\xf8\0")
        output = self.editor.build()
        entries = lfi.lfi_entries(self.components["lfi"])
        for area, original in self.components.items():
            allowed = bytearray(len(original))
            for key in self.editor.edits:
                item = self.editor.ranges[key]
                if item.slot["area"] == area:
                    allowed[item.start:item.end] = b"\1" * (item.end - item.start)
            if area == "lfi":
                pos = entries["LOGO.STY"].directory_offset + 28
                allowed[pos:pos + 4] = b"\1" * 4
                allowed[16:20] = b"\1" * 4
                allowed[510:512] = b"\1" * 2
            self.assertEqual(len(output[area]), len(original))
            self.assertFalse(any(a != b and not permit for a, b, permit in zip(original, output[area], allowed)))
        self.assertEqual(len(lfi.lfi_entries(output["lfi"])), 148)

    def test_missing_components_and_uncoupled_flash_selection(self):
        self.editor.coupled = False
        self.editor.replace("startup", self.red)
        selected = self.editor.stage(self.base / "candidate")
        self.assertEqual(selected.areas, ("lfi",))
        reopened = art.Artwork(selected, self.profile)
        self.assertFalse(reopened.coupled)
        self.assertEqual(len(reopened.ranges), 13)
        self.assertEqual(reopened.rgb("startup"), self.red)

    def test_save_bins_package_roundtrip_and_source_protection(self):
        self.editor.replace("boot-startup", self.red)
        targets = {area: self.base / f"new-{area}.bin" for area in self.editor.changed_areas}
        self.editor.save_bins(targets, self.base / "staged")
        with self.assertRaisesRegex(ValueError, "überschrieben"):
            self.editor.save_bins(targets, self.base / "staged-again")
        for area, path in self.selection.files.items():
            self.assertEqual(path.read_bytes(), self.components[area])
        package = self.base / "edited.x6backup.zip"
        self.editor.save_package(package, self.base / "package")
        selected = images.select_images((package,), self.base / "import", self.profile)
        reopened = art.Artwork(selected, self.profile)
        self.assertEqual(reopened.rgb("boot-startup"), self.red)
        self.assertEqual(reopened.originals["mbrec"], self.components["mbrec"])

    def test_invalid_descriptor_and_overlapping_resources_rejected(self):
        bad = bytearray(self.components["brec"])
        bad[-512] ^= 1
        with self.assertRaisesRegex(ValueError, "deskriptor"):
            art.locate({"brec": bytes(bad)}, self.profile, art.catalog())
        self.editor.coupled = False
        self.editor.replace("boot-startup", self.red)
        self.assertEqual(self.editor.stage(self.base / "only-brec").areas, ("brec",))


class PictureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_alpha_exif_srgb_and_formats(self):
        path = self.base / "alpha.png"
        Image.new("RGBA", (4, 2), (255, 0, 0, 128)).save(path)
        self.assertEqual(picture.import_picture(path).getpixel((0, 0)), (128, 0, 0))
        exif = Image.Exif()
        exif[274] = 6
        path = self.base / "rotated.jpg"
        Image.new("RGB", (4, 2), "red").save(path, exif=exif)
        self.assertEqual(picture.import_picture(path).size, (2, 4))
        path = self.base / "color.webp"
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        Image.new("RGB", (3, 2), "blue").save(path, lossless=True, icc_profile=profile)
        self.assertEqual(picture.import_picture(path).getpixel((0, 0)), (0, 0, 255))

    def test_invalid_animated_and_limits(self):
        path = self.base / "bad.png"
        path.write_bytes(b"not an image")
        with self.assertRaises(ValueError):
            picture.import_picture(path)
        Image.new("RGB", (4, 2), "red").save(path, save_all=True, append_images=[Image.new("RGB", (4, 2), "blue")])
        with self.assertRaisesRegex(ValueError, "Animierte"):
            picture.import_picture(path)
        Image.new("RGB", (4, 2), "red").save(path)
        with (mock.patch.object(picture, "catalog", return_value={**art.catalog(), "max_input_bytes": 1}),
              self.assertRaisesRegex(ValueError, "25 MiB")):
            picture.import_picture(path)
        with (mock.patch.object(picture, "catalog", return_value={**art.catalog(), "max_input_pixels": 1}),
              self.assertRaisesRegex(ValueError, "40 Mega")):
            picture.import_picture(path)

    def test_cover_contain_pan_zoom_and_rotation(self):
        source = Image.new("RGB", (480, 320), "red")
        source.paste("blue", (240, 0, 480, 320))
        fitted = picture.render(source, mode="contain")
        self.assertEqual(fitted.getpixel((120, 0)), (0, 0, 0))
        self.assertEqual(fitted.size, (240, 320))
        self.assertEqual(picture.render(source, pan_x=1).getpixel((120, 100)), (255, 0, 0))
        self.assertEqual(picture.render(source, pan_x=-1).getpixel((120, 100)), (0, 0, 255))
        rotated = picture.render(source, rotation=90)
        self.assertEqual(rotated.getpixel((120, 0)), (255, 0, 0))
        self.assertEqual(rotated.getpixel((120, 319)), (0, 0, 255))
        with self.assertRaises(ValueError):
            picture.render(source, zoom=float("nan"))


if __name__ == "__main__":
    unittest.main()
