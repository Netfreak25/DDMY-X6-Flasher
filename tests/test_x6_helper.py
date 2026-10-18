"""Both native ADFU buttons with a mocked device and HTTP server."""

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
        self.entry = mock.patch.object(helper.toolkit, "enter_adfu", return_value=helper.toolkit.Device("adfu"))
        self.enter = self.entry.start()
        self.off_patch = mock.patch.object(helper.x6_poweroff, "power_off", return_value="Start bestätigt. Jetzt USB-Kabel abziehen.")
        self.off = self.off_patch.start()
        self.window = helper.HelperWindow(self.root, self.server)

    def tearDown(self):
        self.window.close()
        self.reset.stop()
        self.entry.stop()
        self.off_patch.stop()
        self.probe.stop()

    def finish_worker(self):
        self.window.switch_worker.join(timeout=2)
        self.assertFalse(self.window.switch_worker.is_alive())
        self.window._poll_switch()

    def test_passive_start_and_button_runs_without_browser(self):
        self.leave.assert_not_called()
        self.enter.assert_not_called()
        self.off.assert_not_called()
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
            self.window.enter_adfu()
            self.window.power_off()
            self.assertEqual(self.window.poweroff_button.cget("state"), "disabled")
            self.assertEqual(self.window.enter_button.cget("state"), "disabled")
            self.assertEqual(self.window.reset_button.cget("state"), "disabled")
            with mock.patch.object(helper.messagebox, "showinfo") as message:
                self.window.close()
                message.assert_called_once()
                self.server.shutdown.assert_not_called()
        finally:
            gate.set()
            self.finish_worker()
        self.assertEqual(self.leave.call_count, 1)
        self.enter.assert_not_called()
        self.off.assert_not_called()

    def test_busy_bridge_does_not_start_reset(self):
        self.server.switch_lock.acquire()
        try:
            with mock.patch.object(helper.messagebox, "showinfo") as message:
                self.window.reset_button.invoke()
                self.window.enter_button.invoke()
                self.window.poweroff_button.invoke()
                self.assertEqual(message.call_count, 3)
            self.leave.assert_not_called()
            self.enter.assert_not_called()
            self.off.assert_not_called()
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

    def test_enter_uses_existing_toolkit_without_browser_or_ram_payload(self):
        with mock.patch.object(helper.subprocess, "Popen") as browser:
            self.window.enter_button.invoke()
            self.finish_worker()
            browser.assert_not_called()
        self.enter.assert_called_once_with(self.server.profile)
        self.server.payload.assert_not_called()
        self.leave.assert_not_called()
        self.assertEqual(self.window.status.get(), "X6 im ADFU-Modus erkannt.")
        self.assertEqual(self.window.enter_button.cget("state"), "normal")
        self.assertFalse(self.server.switch_lock.locked())

    def test_enter_failure_or_wrong_mode_never_reports_success_or_retries(self):
        for result in (helper.toolkit.ToolkitError("timeout"), helper.toolkit.Device("normal")):
            self.enter.reset_mock()
            self.enter.side_effect = result if isinstance(result, Exception) else None
            self.enter.return_value = result
            with mock.patch.object(helper.messagebox, "showerror") as message:
                self.window.enter_button.invoke()
                self.finish_worker()
                message.assert_called_once()
                self.assertEqual(message.call_args.args[0], "ADFU betreten")
            self.enter.assert_called_once()
            self.assertEqual(self.window.status.get(), "ADFU-Wechsel nicht bestätigt")
            self.assertFalse(self.server.switch_lock.locked())
            self.assertEqual(self.window.enter_button.cget("state"), "normal")

    def test_poweroff_button_runs_native_function_and_prompts_to_unplug(self):
        with mock.patch.object(helper.subprocess, "Popen") as browser:
            self.window.poweroff_button.invoke()
            self.finish_worker()
            browser.assert_not_called()
        self.off.assert_called_once_with(self.server.profile, self.server.runtime)
        self.leave.assert_not_called()
        self.enter.assert_not_called()
        self.assertEqual(self.window.status.get(), "Start bestätigt. Jetzt USB-Kabel abziehen.")
        self.assertEqual(self.window.poweroff_button.cget("state"), "normal")

    def test_poweroff_failure_is_not_retried_or_shown_as_power_loss(self):
        self.off.side_effect = helper.toolkit.ToolkitError("Ausschaltaufruf nicht bestätigt")
        with mock.patch.object(helper.messagebox, "showerror") as message:
            self.window.poweroff_button.invoke()
            self.finish_worker()
            message.assert_called_once_with("Player ausschalten", "Ausschaltaufruf nicht bestätigt", parent=self.root)
        self.off.assert_called_once()
        self.assertEqual(self.window.status.get(), "Ausschaltaufruf nicht bestätigt")
        self.assertFalse(self.server.switch_lock.locked())
        for button in (self.window.enter_button, self.window.reset_button, self.window.poweroff_button):
            self.assertEqual(button.cget("state"), "normal")


if __name__ == "__main__":
    unittest.main()
