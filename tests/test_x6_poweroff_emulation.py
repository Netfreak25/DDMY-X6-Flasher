"""Emulate the actual ARM payload and check every peripheral write."""

import json
import os
import struct
import unittest
from pathlib import Path

try:
    import unicorn
    from unicorn import arm_const
except ImportError:
    unicorn = None

ROOT = Path(__file__).resolve().parents[1]
PMU_BASE = 0xC0010000
REGISTER_OFFSETS = (0x20, 0x24, 0x28, 0x2C)


@unittest.skipUnless(unicorn and os.environ.get("X6_POWEROFF_PAYLOAD"),
                     "requires Unicorn and a compiled X6_POWEROFF_PAYLOAD")
class PowerOffTests(unittest.TestCase):
    def test_exact_x6_writes_and_return(self) -> None:
        """Only the five vendor PMU writes may occur; preserve interrupt state."""
        profile = json.loads((ROOT / "tools" / "x6_profile.json").read_text())
        address = int(profile["protocol"]["work_address"], 0)
        payload = Path(os.environ["X6_POWEROFF_PAYLOAD"]).read_bytes()
        self.assertLessEqual(len(payload), profile["sector_size"])
        for initial in (0, 0xFFFFFFFF, 0xA5A51203):
            for mask in (0, 1):
                with self.subTest(initial=initial, interrupt_mask=mask):
                    cpu = unicorn.Uc(unicorn.UC_ARCH_ARM,
                                     unicorn.UC_MODE_THUMB | unicorn.UC_MODE_MCLASS)
                    cpu.mem_map(address, 0x2000)
                    cpu.mem_map(PMU_BASE, 0x1000)
                    cpu.mem_write(address, payload)
                    for offset in REGISTER_OFFSETS:
                        cpu.mem_write(PMU_BASE + offset, struct.pack("<I", initial))
                    cpu.reg_write(arm_const.UC_ARM_REG_SP, address + 0x2000)
                    cpu.reg_write(arm_const.UC_ARM_REG_LR, (address + 0x1000) | 1)
                    cpu.reg_write(arm_const.UC_ARM_REG_PRIMASK, mask)
                    writes = []
                    cpu.hook_add(unicorn.UC_HOOK_MEM_WRITE,
                                 lambda uc, access, dest, size, value, data:
                                 data.append((dest, size, value)), user_data=writes,
                                 begin=PMU_BASE, end=PMU_BASE + 0xFFF)
                    cpu.emu_start(address | 1, address + 0x1000, count=3_000_000)
                    self.assertEqual(cpu.reg_read(arm_const.UC_ARM_REG_PC), address + 0x1000)
                    self.assertEqual(writes, [
                        (PMU_BASE + 0x2C, 4, (initial & ~0x1C0) | 0xC0),
                        (PMU_BASE + 0x24, 4, 0x15),
                        (PMU_BASE + 0x28, 4, 0x01F00E1F),
                        (PMU_BASE + 0x20, 4, initial & ~2),
                        (PMU_BASE + 0x20, 4, initial & ~3),
                    ])
                    self.assertEqual(cpu.reg_read(arm_const.UC_ARM_REG_PRIMASK), mask)
                    descriptor = cpu.reg_read(arm_const.UC_ARM_REG_R0)
                    report, size = struct.unpack("<II", cpu.mem_read(descriptor, 8))
                    self.assertEqual(size, 40)
                    magic, *values = struct.unpack("<8s8I", cpu.mem_read(report, size))
                    self.assertEqual(magic, b"X6OFFv1!")
                    self.assertEqual(values[:4], [initial] * 4)
                    self.assertEqual(values[4:], [initial & ~3, 0x15, 0x01F00E1F,
                                                 (initial & ~0x1C0) | 0xC0])


if __name__ == "__main__":
    unittest.main()
