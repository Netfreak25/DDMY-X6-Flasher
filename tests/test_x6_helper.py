"""Native GUI with mocked operations: no hardware is accessed."""

import sys
import threading
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import x6_helper as helper


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.probe = mock.patch.object(helper.toolkit, "probe_device", side_effect=AssertionError("hardware forbidden"))
        self.probe.start()
        self.jobs = helper.DeviceJobs()
        self.window = helper.HelperWindow(self.root, helper.toolkit.Profile.load(), Path("unused-runtime"), self.jobs)

    def tearDown(self):
        self.window.close()
        self.probe.stop()

    def finish(self):
        self.window.worker.join(timeout=3)
        self.assertFalse(self.window.worker.is_alive())
        if self.window.poll_id:
            self.root.after_cancel(self.window.poll_id)
        self.window.poll()

    def test_passive_start_and_native_connection_without_api_or_browser(self):
        self.assertFalse(self.window.connected)
        self.assertEqual(self.window.dump_button.cget("state"), "disabled")
        with mock.patch.object(self.window.operations, "connect", return_value={"connected": True, "message": "X6 erkannt"}) as connect:
            self.window.connect_button.invoke()
            self.finish()
            connect.assert_called_once()
        self.assertTrue(self.window.connected)
        self.assertEqual(self.window.dump_button.cget("state"), "normal")
        self.window.connect_button.invoke()
        self.assertFalse(self.window.connected)

    def test_double_click_close_guard_and_busy_buttons(self):
        gate = threading.Event()
        with mock.patch.object(self.window.operations, "mode", side_effect=lambda *_: gate.wait(3) and {"message": "ADFU bereit"}) as action:
            try:
                self.window.enter_button.invoke()
                self.window.mode("leave", "ADFU verlassen")
                self.assertTrue(self.window.busy)
                for button in self.window.controls:
                    self.assertEqual(button.cget("state"), "disabled")
                with mock.patch.object(helper.messagebox, "showinfo") as info:
                    self.window.close()
                    info.assert_called_once()
            finally:
                gate.set()
                self.finish()
            action.assert_called_once_with("enter")
        self.assertIsNone(self.jobs.owner)
        self.assertEqual(self.window.activity.get(), "ADFU bereit")

    def test_pages_ownership_blocks_native_action(self):
        token = self.jobs.acquire("GitHub Pages")
        try:
            with mock.patch.object(helper.messagebox, "showinfo") as info, mock.patch.object(self.window.operations, "mode") as mode:
                self.window.reset_button.invoke()
                info.assert_called_once()
                mode.assert_not_called()
        finally:
            self.jobs.release(token)

    def test_error_disconnects_and_preserves_details_without_retry(self):
        self.window.connected = True
        with mock.patch.object(self.window.operations, "mode", side_effect=helper.toolkit.ToolkitError("USB timeout")) as mode:
            self.window.poweroff_button.invoke()
            self.finish()
            mode.assert_called_once_with("poweroff")
        self.assertFalse(self.window.connected)
        self.assertIn("USB timeout", self.window.activity.get())
        self.assertEqual(self.window.report[-2]["event"], "failure")
        self.assertIsNone(self.jobs.owner)

    def test_cancelled_save_never_starts_dump_or_flash(self):
        self.window.operations.selection = SimpleNamespace(label="BREC + LFI", areas=("brec", "lfi"), has_boot=True)
        with mock.patch.object(helper.filedialog, "asksaveasfilename", return_value=""), \
                mock.patch.object(helper.messagebox, "askokcancel", return_value=True), \
                mock.patch.object(self.window, "start") as start:
            self.window.dump()
            self.window.flash()
            start.assert_not_called()

    def test_detected_components_are_confirmed_and_passed_to_flash_with_zip_backup(self):
        selection = SimpleNamespace(label="MBREC + BREC", areas=("mbrec", "brec"), has_boot=True,
                                    sources=(Path("arbitrary.bin"), Path("second.bin")))
        self.window.operations.selection = selection
        self.window.events.put({"event": "result", "message": "Erkannt", "selection_ready": True})
        self.window.poll()
        self.assertIn("MBREC + BREC", self.window.firmware.get())
        with mock.patch.object(helper.messagebox, "askokcancel", return_value=True) as confirm, \
                mock.patch.object(helper.filedialog, "asksaveasfilename", return_value="before.zip") as save, \
                mock.patch.object(self.window.operations, "flash_selection", return_value={"message": "Geprüft"}) as flash:
            self.window.flash()
            self.finish()
        self.assertIn("MBREC + BREC", confirm.call_args.args[1])
        self.assertIn("beiden Bootkopien", confirm.call_args.args[1])
        self.assertEqual(save.call_args.kwargs["defaultextension"], ".x6backup.zip")
        flash.assert_called_once_with(selection, Path("before.zip"))

    def test_file_picker_accepts_multiple_files_and_runs_offline_identification(self):
        with mock.patch.object(helper.filedialog, "askopenfilenames", return_value=("a.bin", "b.bin")), \
                mock.patch.object(self.window.operations, "inspect", return_value={"message": "Erkannt"}) as inspect:
            self.window.choose_firmware()
            self.finish()
        inspect.assert_called_once_with((Path("a.bin"), Path("b.bin")))

    def test_flash_without_backup_skips_picker_for_standard_and_editor_actions(self):
        selection = SimpleNamespace(label="BREC + LFI", areas=("brec", "lfi"), has_boot=True)
        self.window.operations.selection = selection
        self.assertTrue(self.window.backup_before_flash.get())
        self.window.backup_before_flash.set(False)
        for labels in (None, ["Startbild"]):
            with self.subTest(editor=bool(labels)), \
                    mock.patch.object(helper.messagebox, "askokcancel", return_value=True) as confirm, \
                    mock.patch.object(helper.filedialog, "asksaveasfilename") as save, \
                    mock.patch.object(self.window.operations, "flash_selection", return_value={"message": "Geprüft"}) as flash:
                self.window.flash(selection, labels)
                self.finish()
                save.assert_not_called()
                flash.assert_called_once_with(selection, None)
                self.assertIn("Ohne neue Rücksicherung", confirm.call_args.args[1])

    def test_restore_folder_and_unverified_result_are_visible(self):
        with mock.patch.object(helper.filedialog, "askdirectory", return_value="backup-folder"):
            self.window.choose_folder()
        self.assertEqual(self.window.backup.get(), "backup-folder")
        with mock.patch.object(helper.messagebox, "askokcancel", return_value=True), \
                mock.patch.object(self.window.operations, "restore", return_value={"message": "Boot-Rücklesung offen", "warning": True}) as restore:
            self.window.restore()
            self.finish()
            restore.assert_called_once_with(Path("backup-folder"), False)
        self.assertIn("Hinweis", self.window.status.get())
        self.assertIn("Boot-Rücklesung", self.window.activity.get())

    def test_confirmed_backend_progress_only(self):
        self.window.events.put({"event": "phase", "label": "LFI schreiben"})
        self.window.events.put({"event": "backend_output", "line": "write_flash: 32/128 sectors"})
        self.window.poll()
        self.assertEqual(self.window.progress.cget("value"), 32)
        self.assertIn("25%", self.window.progress_text.get())

    def test_restart_wait_keeps_flat_style_and_does_not_redraw_duplicate_phases(self):
        self.assertEqual(self.window.progress.cget("style"), "X6.Horizontal.TProgressbar")
        with mock.patch.object(self.window.progress, "start") as animate:
            self.window.events.put({"event": "phase", "label": "Neustarten"})
            self.window.poll()
            with mock.patch.object(self.window.progress, "configure") as configure:
                for _ in range(20):
                    self.window.events.put({"event": "phase", "label": "Neustarten"})
                self.window.poll()
                configure.assert_not_called()
            animate.assert_not_called()
        self.assertEqual(str(self.window.progress.cget("mode")), "determinate")
        self.assertEqual(self.window.progress.cget("value"), 0)

    def test_minimum_window_does_not_clip_action_or_footer_buttons(self):
        self.root.geometry("720x760+10000+10000")
        self.root.deiconify()
        self.root.update()
        for button in self.window.controls:
            if button.winfo_ismapped():
                self.assertGreaterEqual(button.winfo_height(), button.winfo_reqheight(), button.cget("text"))
                self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth(), button.cget("text"))

    def test_pending_save_remains_visible_below_multiline_error(self):
        self.window.operations.pending_backup = Path("test.bin")
        self.window.activity.set("Lesen abgeschlossen.\nSpeichern fehlgeschlagen.\nBitte einen anderen Speicherort wählen.")
        self.window.update_controls()
        self.root.geometry("720x760+10000+10000")
        self.root.deiconify()
        self.root.update()
        button = self.window.pending_button
        self.assertTrue(button.winfo_ismapped())
        self.assertGreaterEqual(button.winfo_height(), button.winfo_reqheight())


if __name__ == "__main__":
    unittest.main()
