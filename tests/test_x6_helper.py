"""Native window reset controls with a mocked device and HTTP server."""

import sys
import threading
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import x6_helper as helper


class HelperResetTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.server = mock.Mock(version="test", switch_lock=threading.Lock())
        self.server.payload.return_value = b"test-payload"
        self.probe = mock.patch.object(helper.toolkit, "probe_device", side_effect=AssertionError("hardware forbidden"))
        self.probe.start()
        self.reset = mock.patch.object(helper.x6_reset, "leave_adfu", return_value="Normalmodus erkannt")
        self.leave = self.reset.start()
        self.window = helper.HelperWindow(self.root, self.server)

    def tearDown(self):
        self.window.close()
        self.reset.stop()
        self.probe.stop()

    def finish_worker(self):
        self.window.reset_worker.join(timeout=2)
        self.assertFalse(self.window.reset_worker.is_alive())
        self.window._poll_reset()

    def test_passive_start_and_button_runs_without_browser(self):
        self.leave.assert_not_called()
        with mock.patch.object(helper.subprocess, "Popen") as browser:
            self.window.reset_button.invoke()
            self.finish_worker()
            browser.assert_not_called()
        self.leave.assert_called_once_with(self.server.profile, b"test-payload")
        self.assertEqual(self.window.status.get(), "Normalmodus erkannt")
        self.assertEqual(self.window.reset_button.cget("state"), "normal")
        self.assertFalse(self.server.switch_lock.locked())

    def test_double_click_and_close_during_reset_do_not_interrupt_or_repeat(self):
        gate = threading.Event()
        self.leave.side_effect = lambda *args: gate.wait(2) and "Normalmodus erkannt"
        try:
            self.window.leave_adfu()
            self.window.leave_adfu()
            self.assertEqual(self.window.reset_button.cget("state"), "disabled")
            with mock.patch.object(helper.messagebox, "showinfo") as message:
                self.window.close()
                message.assert_called_once()
                self.server.shutdown.assert_not_called()
        finally:
            gate.set()
            self.finish_worker()
        self.assertEqual(self.leave.call_count, 1)

    def test_busy_bridge_does_not_start_reset(self):
        self.server.switch_lock.acquire()
        try:
            with mock.patch.object(helper.messagebox, "showinfo") as message:
                self.window.reset_button.invoke()
                message.assert_called_once()
            self.leave.assert_not_called()
        finally:
            self.server.switch_lock.release()

    def test_failure_is_shown_and_releases_lock_without_retry(self):
        self.leave.side_effect = helper.toolkit.ToolkitError("USB timeout; reset unconfirmed")
        with mock.patch.object(helper.messagebox, "showerror") as message:
            self.window.reset_button.invoke()
            self.finish_worker()
            message.assert_called_once_with("ADFU verlassen", "USB timeout; reset unconfirmed", parent=self.root)
        self.assertEqual(self.leave.call_count, 1)
        self.assertEqual(self.window.status.get(), "Neustart nicht bestätigt")
        self.assertFalse(self.server.switch_lock.locked())
        self.assertEqual(self.window.reset_button.cget("state"), "normal")


if __name__ == "__main__":
    unittest.main()
