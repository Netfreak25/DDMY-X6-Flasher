"""Shared native-pixel vectors and supported/legacy editor behavior."""

import copy
import hashlib
import json
import struct
import unittest
from pathlib import Path
from types import SimpleNamespace

import test_x6_artwork as fixtures
import x6_artwork as art
import x6_screensaver as saver


class ScreensaverTests(unittest.TestCase):
    def test_shared_vectors_and_idempotence(self):
        profile = art.catalog()["screensaver"]
        vectors = json.loads(Path(__file__).with_name("screensaver_vectors.json").read_text())
        self.assertEqual(profile["revision"], vectors["revision"])
        for case in vectors["pixels"]:
            self.assertEqual(saver.limit_pixel(case["word"], case["ceiling"]), case["result"])
        raw = struct.pack("<76800H", *[(i * 40503 + 97) & 65535 for i in range(76800)])
        for case in vectors["backgrounds"]:
            self.assertEqual(saver.limits(profile, case["slot"]), case["limits"])
            data = saver.process(raw, profile, case["slot"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), case["sha256"])
            self.assertEqual(saver.process(data, profile, case["slot"]), data)
        self.assertEqual(saver.process(bytes(len(raw)), profile, 1), bytes(len(raw)))

    def test_recognition_depends_on_code_not_image_bytes(self):
        profile = {"revision": saver.REVISION, "supported_code": {"MAINMENU.AP": hashlib.sha256(b"code").hexdigest()}}
        entries = {"MAINMENU.AP": SimpleNamespace(data_offset=0, length=4)}
        self.assertTrue(saver.supported(b"codepicture", entries, profile))
        self.assertTrue(saver.supported(b"codeCHANGED", entries, profile))
        self.assertFalse(saver.supported(b"xxxxpicture", entries, profile))
        self.assertFalse(saver.supported(b"codepicture", {}, profile))
        self.assertFalse(saver.supported(b"codepicture", entries, {**profile, "revision": "future"}))
        self.assertFalse(saver.supported(b"codepicture", entries, {**profile, "revision": "v4-native-contour"}))

    def test_import_preview_reset_export_and_legacy(self):
        fixture = fixtures.ArtworkTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        model = fixture.editor
        self.assertFalse(model.contour)
        self.assertEqual(model.build(), fixture.components)
        model.replace("background-01", fixture.red)
        self.assertEqual(model.rgb("background-01"), fixture.red)
        model.reset("background-01")
        model.contour = True  # Synthetic fixture; code recognition tested separately.
        source = bytes((255, 255, 255)) * 240 * 320
        for key in ("background-01", "background-02", "background-11"):
            preview = model.preview_rgb(key, source)
            model.replace(key, source)
            self.assertEqual(model.rgb(key), preview)
            before = model.pixels(key)
            model.replace(key, model.rgb(key))
            self.assertEqual(model.pixels(key), before)
        for key in ("startup", "boot-startup", "shutdown"):
            self.assertEqual(model.preview_rgb(key, source), source)
        original = copy.deepcopy(model.originals)
        model.reset("background-01")
        self.assertNotIn("background-01", model.edits)
        self.assertEqual(model.originals, original)
        saved = model.stage(fixture.base / "processed")
        reopened = art.Artwork(saved, fixture.profile)
        self.assertEqual(reopened.pixels("background-02"), model.pixels("background-02"))


if __name__ == "__main__":
    unittest.main()
