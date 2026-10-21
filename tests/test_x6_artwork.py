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


def fixture(managed=False):
    """Generate valid blank resources, boot records and 148 files without ROM data."""
    config = art.catalog()
    raw = bytearray((8 if managed else 3) * 1024 * 1024)
    struct.pack_into("<I", raw, 0, lfi.LFI_MAGIC)
    offset = lfi.LFI_DIRECTORY_END
    for index in range(lfi.X6_FILE_COUNT):
        name = ["COMMON.STY", "LOGO.STY", "END.STY"][index] if index < 3 else f"T{index:07d}.BIN"
        if managed and index == 3:
            name = "CONFIG.BIN"
        if managed and index == lfi.X6_FILE_COUNT - 1:
            name = "X6FREE.BIN"
        slots = [slot for slot in config["slots"] if slot.get("file") == name]
        content = bytearray(4)
        if slots:
            count = max(slot["index"] for slot in slots) + 1
            table_end = 1024 + count * 16
            content = bytearray(table_end + len(slots) * config["pixel_bytes"])
            struct.pack_into("<I", content, 0, config["sty"]["magic"])
            struct.pack_into("<I", content, 0x10, config["sty"]["table_offset"])
            struct.pack_into("<H", content, 20, count)
            for nth, slot in enumerate(slots):
                struct.pack_into("<IIHHH", content, 1024 + slot["index"] * 16,
                                 table_end + nth * config["pixel_bytes"], config["pixel_bytes"], 2, 240, 320)
            if managed and name == "COMMON.STY":
                content.extend(bytes(art.storage.LAYOUT["common_base_size"] - len(content)))
                struct.pack_into("<4sHHII", content, 64, b"X6SC", 1, 11, len(content), 0)
                struct.pack_into("<11I", content, 80, 0, *[table_end + nth * config["pixel_bytes"] for nth in range(1, 11)])
        if name == "CONFIG.BIN":
            content = bytearray(512)
            content[:4] = b"cfg\0"
            struct.pack_into("<II", content, 72, 128, 8)
            struct.pack_into("<H", content, 128, 11)
        pos = 512 + index * 32
        base, ext = name.split(".")
        raw[pos:pos + 11] = (base.ljust(8) + ext).encode("ascii")
        allocated = len(raw) - offset if index == lfi.X6_FILE_COUNT - 1 else (len(content) + 511) // 512 * 512
        struct.pack_into("<II", raw, pos + 16, offset // 512, allocated)
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
            if self.editor.ranges[key].slot.get("fixed_black"):
                with self.assertRaisesRegex(ValueError, "Schwarz"):
                    self.editor.replace(key, self.red)
                with self.assertRaisesRegex(ValueError, "Schwarz"):
                    self.editor.reset(key)
                continue
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

    def managed_editor(self):
        """Use public allocation metadata with synthetic image pixels and no device data."""
        components, profile = fixture(managed=True)
        for area, data in components.items():
            self.selection.files[area].write_bytes(data)
            self.selection.hashes[area] = tk.sha256_file(self.selection.files[area])
        return art.Artwork(self.selection, profile)

    def test_dynamic_images_preserve_pending_edits_originals_and_identity(self):
        model = self.managed_editor()
        initial = model.build()
        capacity = model.storage["capacity"]
        self.assertTrue(model.contour)
        self.assertFalse(model.has_changes)
        model.replace("startup", self.red)
        blue = bytes((0, 0, 255)) * 240 * 320
        first = model.add_background(self.red)
        second = model.add_background(blue)
        self.assertEqual((first, second), ("background-12", "background-13"))
        self.assertEqual(model.storage["capacity"], capacity - 2)
        second_original = model.pixels(second, original=True)
        model.replace(second, self.red)
        remap = model.delete_background(first)
        self.assertEqual(remap[second], first)
        self.assertEqual(model.pixels(first, original=True), second_original)
        model.reset(first)
        self.assertEqual(model.pixels(first), second_original)
        model.reset("startup")
        self.assertEqual(model.pixels("startup"), model.original_pixels["startup"])
        selected = model.stage(self.base / "managed")
        reopened = art.Artwork(selected, model.profile)
        self.assertEqual(reopened.pixels(first), second_original)
        self.assertEqual(reopened.storage["capacity"], capacity - 1)
        model.delete_background(first)
        self.assertEqual(model.build(), initial)
        self.assertFalse(model.has_changes)

    def test_dynamic_preview_gradient_and_failed_operations_are_atomic(self):
        model = self.managed_editor()
        white = bytes((255, 255, 255)) * 240 * 320
        key = model.add_background(white)
        expected = model.prepare_new(white)
        self.assertEqual(model.pixels(key), expected)
        model.replace(key, model.rgb(key))
        self.assertEqual(model.pixels(key), expected)
        before = model.build()
        for protected in ("background-01", "background-02"):
            with self.assertRaisesRegex(ValueError, "Standardbilder"):
                model.delete_background(protected)
        with self.assertRaisesRegex(ValueError, "240"):
            model.add_background(b"invalid")
        self.assertEqual(model.build(), before)
        self.assertEqual(model.originals["lfi"], self.selection.files["lfi"].read_bytes())

    def test_legacy_add_and_screensaver_upgrade_are_unavailable(self):
        before = self.editor.build()
        with self.assertRaisesRegex(ValueError, "Bilderkatalog"):
            self.editor.add_background(self.red)
        with self.assertRaisesRegex(ValueError, "deaktiviert"):
            self.editor.apply_screensaver()
        self.assertFalse(self.editor.has_changes)
        self.assertEqual(self.editor.build(), before)


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
