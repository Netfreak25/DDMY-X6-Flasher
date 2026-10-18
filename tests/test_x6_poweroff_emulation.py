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
WATCHDOG = 0xC003001C
USB_PRESENT = 1 << 30
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
                    cpu.mem_map(WATCHDOG & ~0xFFF, 0x1000)
                    cpu.mem_write(address, payload)
                    for offset in REGISTER_OFFSETS:
                        value = initial & ~USB_PRESENT if offset == 0x28 else initial
                        cpu.mem_write(PMU_BASE + offset, struct.pack("<I", value))
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
                    self.assertEqual(magic, b"X6OFFv2!")
                    self.assertEqual(values[:4], [initial, initial,
                                                  initial & ~USB_PRESENT, initial])
                    self.assertEqual(values[4:], [initial & ~3, 0x15, 0x01F00E1F,
                                                 (initial & ~0x1C0) | 0xC0])

    def test_waits_for_usb_removal_and_feeds_watchdog(self) -> None:
        """USB present forbids PMU writes; removal releases the five writes."""
        profile = json.loads((ROOT / "tools" / "x6_profile.json").read_text())
        address = int(profile["protocol"]["work_address"], 0)
        cpu = unicorn.Uc(unicorn.UC_ARCH_ARM,
                         unicorn.UC_MODE_THUMB | unicorn.UC_MODE_MCLASS)
        cpu.mem_map(address, 0x2000)
        cpu.mem_map(PMU_BASE, 0x1000)
        cpu.mem_map(WATCHDOG & ~0xFFF, 0x1000)
        cpu.mem_write(address, Path(os.environ["X6_POWEROFF_PAYLOAD"]).read_bytes())
        cpu.mem_write(PMU_BASE + 0x20, struct.pack("<I", 3))
        cpu.mem_write(PMU_BASE + 0x28, struct.pack("<I", USB_PRESENT))
        cpu.mem_write(WATCHDOG, struct.pack("<I", 0x16))
        cpu.reg_write(arm_const.UC_ARM_REG_SP, address + 0x2000)
        cpu.reg_write(arm_const.UC_ARM_REG_LR, (address + 0x1000) | 1)
        writes = []
        cpu.hook_add(unicorn.UC_HOOK_MEM_WRITE,
                     lambda uc, access, dest, size, value, data:
                     data.append((dest, size, value)), user_data=writes,
                     begin=PMU_BASE, end=WATCHDOG + 3)
        cpu.emu_start(address | 1, address + 0x1000, count=10_000)
        self.assertTrue(writes)
        self.assertTrue(all(write == (WATCHDOG, 4, 0x17) for write in writes))
        self.assertEqual(cpu.reg_read(arm_const.UC_ARM_REG_PRIMASK), 1)
        # Continue the same CPU after simulating the cable being unplugged.
        writes.clear()
        cpu.mem_write(PMU_BASE + 0x28, struct.pack("<I", 0))
        cpu.emu_start(cpu.reg_read(arm_const.UC_ARM_REG_PC) | 1,
                      address + 0x1000, count=3_000_000)
        pmu_writes = [write for write in writes if write[0] != WATCHDOG]
        self.assertEqual(pmu_writes, [
            (PMU_BASE + 0x2C, 4, 0xC0),
            (PMU_BASE + 0x24, 4, 0x15),
            (PMU_BASE + 0x28, 4, 0x01F00E1F),
            (PMU_BASE + 0x20, 4, 1),
            (PMU_BASE + 0x20, 4, 0),
        ])
        self.assertEqual(cpu.reg_read(arm_const.UC_ARM_REG_PC), address + 0x1000)
        self.assertEqual(cpu.reg_read(arm_const.UC_ARM_REG_PRIMASK), 0)


if __name__ == "__main__":
    unittest.main()
