"""Guard the boundary between the public flasher and private firmware work."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from package_release import allowed_source


class ReleaseBoundaryTests(unittest.TestCase):
    def test_private_artifacts_and_unapproved_images_are_rejected(self):
        for name in ("private_dumps/original/lfi.bin", "tools/fwsc.bin", "assets/firmware.bin",
                     "assets/another-photo.png", "docs/investigation.pdf", "../README.md"):
            self.assertFalse(allowed_source(name), name)
        for name in ("README.md", "tools/x6_helper.py", "lib/driver.js", "assets/ddmy-x6-detail.png"):
            self.assertTrue(allowed_source(name), name)


if __name__ == "__main__":
    unittest.main()
