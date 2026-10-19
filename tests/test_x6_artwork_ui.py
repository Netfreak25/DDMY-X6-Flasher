"""Native editor UI regression tests, with hardware access explicitly forbidden."""

import struct
import sys
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import test_x6_artwork as fixtures
import test_x6_screensaver_patch as patch_fixtures
import x6_artwork as art
import x6_artwork_ui as ui
import x6_helper as helper


class EditorWindowTests(unittest.TestCase):
    def setUp(self):
        self.data = fixtures.ArtworkTests()
        self.data.setUp()
        self.addCleanup(self.data.doCleanups)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        forbidden = mock.patch.object(helper.toolkit, "probe_device", side_effect=AssertionError("hardware forbidden"))
        forbidden.start()
        self.addCleanup(forbidden.stop)
        self.host = helper.HelperWindow(self.root, self.data.profile, Path("unused"), helper.DeviceJobs())
        self.host.open_artwork()
        self.editor = self.host.artwork_window
        self.editor.loaded(self.data.editor)
        self.root.update()

    def finish(self):
        self.host.worker.join(timeout=5)
        self.assertFalse(self.host.worker.is_alive())
        if self.host.poll_id:
            self.root.after_cancel(self.host.poll_id)
            self.host.poll_id = None
        self.host.poll()

    def test_fixed_gallery_offline_controls_and_missing_brec(self):
        self.assertEqual(len(self.editor.tiles), 14)
        self.assertTrue(self.editor.coupled.get())
        self.assertEqual(str(self.editor.device_button.cget("state")), "disabled")
        self.assertEqual(str(self.editor.save_button.cget("state")), "disabled")
        self.data.editor.coupled = False
        self.data.editor.replace("startup", self.data.red)
        selected = self.data.editor.stage(self.data.base / "only-lfi")
        self.editor.loaded(art.Artwork(selected, self.data.profile))
        self.assertEqual(str(self.editor.tiles["boot-startup"].cget("state")), "disabled")
        self.assertIn("BREC nicht geladen", self.editor.info.get())

    def test_backup_checkbox_is_shared_with_main_window_and_disabled_while_busy(self):
        self.assertTrue(self.host.backup_before_flash.get())
        self.editor.backup_checkbox.invoke()
        self.assertFalse(self.host.backup_before_flash.get())
        self.host.backup_checkbox.invoke()
        self.assertTrue(self.editor.backup_checkbox.instate(["selected"]))
        self.host.busy = True
        self.host.update_controls()
        self.assertEqual(self.host.backup_checkbox.cget("state"), "disabled")
        self.assertTrue(self.editor.backup_checkbox.instate(["disabled"]))
        self.host.busy = False

    def test_screensaver_upgrade_is_optional_preserves_edits_and_disables_second_patch(self):
        """A UI action alone activates code changes; selecting wallpapers only explains it."""
        _components, _device, config, versions = patch_fixtures.fixture()
        path = self.data.selection.files["lfi"]
        path.write_bytes(versions["install"])
        self.data.selection.hashes["lfi"] = art.tk.sha256_file(path)
        with mock.patch.object(art, "catalog", return_value=config):
            model = art.Artwork(self.data.selection, self.data.profile)
        self.editor.loaded(model)
        self.editor.select("background-02")
        self.assertIn("empfehlen", self.editor.patch_info.get())
        self.assertFalse(model.has_changes)
        self.assertIsNone(model.patched_lfi)
        self.assertEqual(str(self.editor.patch_button.cget("state")), "normal")
        model.replace("shutdown", self.data.red)
        pixels = model.pixels("shutdown")
        self.editor.patch_button.invoke()
        self.finish()
        self.assertTrue(model.contour)
        self.assertEqual(model.patch_state, "current")
        self.assertEqual(model.pixels("shutdown"), pixels)
        self.assertEqual(str(self.editor.patch_button.cget("state")), "disabled")
        self.assertEqual(model.originals["lfi"], versions["install"])

    def test_repaired_import_can_be_saved_without_editing_any_picture(self):
        raw = bytearray(self.data.components["lfi"])
        entry = list(art.lfi.lfi_entries(raw).values())[-1]
        struct.pack_into("<I", raw, entry.directory_offset + 20, 512)
        raw = patch_fixtures.sums(raw)
        source = self.data.base / "gap.bin"
        source.write_bytes(raw)
        selection = art.images.select_images((source,), self.data.base / "repair", self.data.profile)
        model = art.Artwork(selection, self.data.profile)
        self.editor.loaded(model)
        self.assertTrue(model.has_changes)
        self.assertEqual(model.changed_areas, ("lfi",))
        self.assertEqual(str(self.editor.save_button.cget("state")), "normal")
        self.assertIn("Dateizuordnung korrigiert", self.editor.info.get())
        self.assertIsNone(model.patched_lfi)
        saved = model.stage(self.data.base / "export")
        self.assertEqual(saved.files["lfi"].read_bytes(), self.data.components["lfi"])
        self.assertEqual(source.read_bytes(), raw)

    def test_cancel_crop_reset_and_dirty_close(self):
        self.editor.select("startup")
        self.editor.edit()
        self.editor.crop_dialog.close()
        self.assertEqual(self.data.editor.edits, {})
        self.editor.crop(ui.Image.new("RGB", (400, 400), "red"))
        self.editor.crop_dialog.rotate()
        pending = self.editor.crop_dialog.preview_id
        self.editor.crop_dialog.accept()
        self.assertNotIn(pending, self.root.tk.call("after", "info"))
        self.assertEqual(set(self.data.editor.edits), {"startup", "boot-startup"})
        with mock.patch.object(ui.messagebox, "askokcancel", return_value=False):
            self.assertFalse(self.editor.close())
        self.editor.reset()
        self.assertEqual(self.data.editor.edits, {})
        self.assertFalse(self.editor.dirty)

    def test_gallery_fills_window_and_reflows_without_losing_edits(self):
        """Reproduce the large blank canvas after maximizing, then shrink again."""
        self.data.editor.replace("shutdown", self.data.red)
        self.editor.select("shutdown")
        self.editor.refresh()
        edits = dict(self.data.editor.edits)
        columns = []
        for size in ("960x720", "1040x780", "1920x1000", "1040x780"):
            with self.subTest(size=size):
                self.editor.window.geometry(size + "+10000+10000")
                self.root.update()
                canvas, gallery = self.editor.gallery_canvas, self.editor.gallery
                columns.append(max(int(button.grid_info()["column"]) for button in self.editor.tiles.values()) + 1)
                self.assertEqual(gallery.winfo_width(), canvas.winfo_width())
                theme = ui.ttk.Style(self.editor.window).lookup("TFrame", "background") or ui.BACKGROUND
                self.assertEqual(canvas.winfo_rgb(canvas.cget("background")), canvas.winfo_rgb(theme))
                for button in self.editor.tiles.values():
                    self.assertLessEqual(button.winfo_x() + button.winfo_width(), canvas.winfo_width())
                self.assertEqual(self.editor.active, "shutdown")
                self.assertEqual(self.data.editor.edits, edits)
        self.assertGreater(columns[2], columns[1])
        self.assertEqual(columns[1], columns[3])

    def test_contour_crop_preview_apply_cancel_and_both_resets(self):
        """The visible crop is the exported pixel data, after every edit operation."""
        model = self.data.editor
        model.contour = True  # Recognition has separate byte-level tests.
        self.editor.select("background-01")
        source = ui.Image.new("RGB", (480, 320), "white")
        source.paste("yellow", (0, 0, 240, 320))
        self.editor.crop(source)
        dialog = self.editor.crop_dialog
        dialog.rotate()
        dialog.pan_x.set(0.5)
        dialog.window.after_cancel(dialog.preview_id)
        dialog.refresh()
        expected = model.preview_rgb("background-01", dialog.rgb)
        self.assertEqual(ui.ImageTk.getimage(dialog.photo).convert("RGB").tobytes(), expected)
        dialog.close()
        self.assertEqual(model.edits, {})
        self.editor.crop(source)
        dialog = self.editor.crop_dialog
        dialog.rotate()
        dialog.reset()
        dialog.accept()
        expected = model.preview_rgb("background-01", dialog.rgb)
        self.assertEqual(model.rgb("background-01"), expected)
        self.assertEqual(source.getpixel((400, 200)), (255, 255, 255))
        self.editor.edit()
        self.editor.crop_dialog.accept()
        self.assertEqual(model.rgb("background-01"), expected)
        self.editor.reset()
        self.assertEqual(model.edits, {})
        self.assertFalse(self.editor.dirty)

    def test_saving_is_offline_even_while_pages_has_device_lease(self):
        self.data.editor.replace("background-01", self.data.red)
        self.editor.dirty = True
        self.editor.refresh()
        token = self.host.jobs.acquire("GitHub Pages")
        try:
            path = self.data.base / "saved-lfi.bin"
            with mock.patch.object(ui.filedialog, "asksaveasfilename", return_value=str(path)):
                self.editor.save_bins()
                self.finish()
            self.assertTrue(path.exists())
            self.assertFalse(self.editor.dirty)
        finally:
            self.host.jobs.release(token)

    def test_flash_handoff_has_only_changed_areas_and_labels(self):
        self.data.editor.replace("startup", self.data.red)
        with mock.patch.object(self.host, "flash") as flash:
            self.editor.flash()
            self.finish()
        selected, labels = flash.call_args.args
        self.assertEqual(selected.areas, ("brec", "lfi"))
        self.assertEqual(len(labels), 2)
        self.assertNotIn("mbrec", selected.files)

    def test_load_device_requires_explicit_click_save_and_complete_result(self):
        self.host.connected = True
        destination = self.data.base / "device-original.zip"
        with (mock.patch.object(ui.filedialog, "asksaveasfilename", return_value=str(destination)),
              mock.patch.object(self.host.operations, "dump", return_value={"message": "Unvollständig", "complete": False}) as dump,
              mock.patch.object(self.editor, "load_sources") as load):
            self.editor.load_device()
            self.finish()
            dump.assert_called_once_with(destination)
            load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
