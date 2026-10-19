"""Progress feedback must stay calm while waiting; no GUI or hardware is used."""

import queue
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import x6_helper as helper


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.window = SimpleNamespace(
            events=queue.SimpleQueue(), report=[], poll_id=None, busy=False,
            progress=mock.Mock(), activity=mock.Mock(), progress_text=mock.Mock(),
        )

    def phase(self, label):
        self.window.events.put({"event": "phase", "label": label})
        helper.HelperWindow.poll(self.window)

    def test_restart_wait_has_no_automatic_progress_animation(self):
        """The actual restart phase must not start a looping progress timer."""
        self.phase("Neustarten")
        self.window.activity.set.assert_called_once_with("Neustarten")
        self.window.progress.start.assert_not_called()
        self.window.progress.stop.assert_not_called()
        self.window.progress.configure.assert_called_once_with(mode="determinate", value=0)

    def test_repeated_wait_phases_do_not_restart_animation(self):
        """Repeated labels and empty polls never produce an animation cycle."""
        for _ in range(3):
            self.phase("Neustarten")
            helper.HelperWindow.poll(self.window)
        self.window.progress.start.assert_not_called()
        self.window.progress.stop.assert_not_called()
        self.assertEqual(self.window.report, [{"event": "phase", "label": "Neustarten"}] * 3)

    def test_confirmed_write_progress_still_advances(self):
        for done in (32, 64):
            self.window.events.put({"event": "backend_output", "line": f"write_flash: {done}/128 sectors"})
            helper.HelperWindow.poll(self.window)
        self.assertEqual(self.window.progress.configure.call_args_list, [
            mock.call(mode="determinate", maximum=128, value=32),
            mock.call(mode="determinate", maximum=128, value=64),
        ])
        self.window.progress_text.set.assert_called_with("50% · bestätigte Sektoren")
        self.window.progress.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
