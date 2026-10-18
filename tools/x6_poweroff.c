/* X6 power-off request executed in RAM. No NAND, SD or GPIO access.
 * Waits for USB power removal before any PMU write and feeds the watchdog.
 * Start using ADFUS switch, which acknowledges before entering this code.
 * USB commands cannot be serviced during the wait.
 */
#include <stdint.h>

#define PMU_BASE       UINT32_C(0xc0010000)
#define POWER_ENABLE   UINT32_C(0x20)
#define POWER_CONTROL  UINT32_C(0x24)
#define WAKE_PENDING   UINT32_C(0x28)
#define KEY_CONTROL    UINT32_C(0x2c)
#define KEY_OFF_MASK   UINT32_C(0x1c0)
#define X6_KEY_OFF     UINT32_C(0xc0)
#define X6_POWER_OFF   UINT32_C(0x15)
#define X6_CLEAR_WAKE  UINT32_C(0x01f00e1f)
/* ATJ2157 datasheet v1.2 pp. 37, 70-71: live DC5V flag and watchdog feed. */
#define USB_PRESENT    (UINT32_C(1) << 30)
#define WATCHDOG_ADDR  UINT32_C(0xc003001c)
#define WATCHDOG_FEED  UINT32_C(1)
#define WATCHDOG       (*(volatile uint32_t *)WATCHDOG_ADDR)
/* Conservative settling loop used by the upstream ADFUS power-off path. */
#define SETTLE_LOOPS   UINT32_C(0x8000)
#define PMU(offset) (*(volatile uint32_t *)(PMU_BASE + (offset)))

struct report {
    char magic[8];
    uint32_t before[4];
    uint32_t after[4];
};

struct result {
    const void *address;
    uint32_t size;
};

static struct report report = {"X6OFFv2!", {0}, {0}};
static const struct result result = {&report, sizeof(report)};

static void settle(void)
{
    for (volatile uint32_t count = SETTLE_LOOPS; count; --count)
        __asm__ volatile ("nop");
}

static void snapshot(uint32_t values[4])
{
    values[0] = PMU(POWER_ENABLE);
    values[1] = PMU(POWER_CONTROL);
    values[2] = PMU(WAKE_PENDING);
    values[3] = PMU(KEY_CONTROL);
}

/* Interrupts stay masked so ADFUS cannot reset in response to USB removal. */
static void wait_for_usb_removal(void)
{
    while (PMU(WAKE_PENDING) & USB_PRESENT)
        WATCHDOG |= WATCHDOG_FEED;
}

__attribute__((section(".text.entry")))
const struct result *entry(void)
{
    uint32_t interrupt_mask;
    __asm__ volatile ("mrs %0, primask\ncpsid i" : "=r"(interrupt_mask) :: "memory");
    wait_for_usb_removal();
    snapshot(report.before);
    PMU(KEY_CONTROL) = (PMU(KEY_CONTROL) & ~KEY_OFF_MASK) | X6_KEY_OFF;
    settle();
    PMU(POWER_CONTROL) = X6_POWER_OFF;
    settle();
    PMU(WAKE_PENDING) = X6_CLEAR_WAKE;
    settle();
    PMU(POWER_ENABLE) &= ~UINT32_C(2);
    settle();
    PMU(POWER_ENABLE) &= ~UINT32_C(1);
    settle();
    snapshot(report.after);
    __asm__ volatile ("msr primask, %0" :: "r"(interrupt_mask) : "memory");
    return &result;
}
