/* X6 power-off request executed in RAM. No NAND, SD or GPIO access.
 * USB can keep the board powered after battery power-enable is cleared.
 * Call using ADFUS exec_ret; a report is returned if USB keeps it alive.
 */
#include <stdint.h>

#define PMU_BASE       UINT32_C(0xc0010000)
#define POWER_ENABLE   UINT32_C(0x20)
#define POWER_CONTROL  UINT32_C(0x24)
#define POWER_CONFIG   UINT32_C(0x28)
#define KEY_CONTROL    UINT32_C(0x2c)
#define KEY_OFF_MASK   UINT32_C(0x1c0)
#define X6_KEY_OFF     UINT32_C(0xc0)
#define X6_POWER_OFF   UINT32_C(0x15)
#define X6_OFF_CONFIG  UINT32_C(0x01f00e1f)
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

static struct report report = {"X6OFFv1!", {0}, {0}};
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
    values[2] = PMU(POWER_CONFIG);
    values[3] = PMU(KEY_CONTROL);
}

__attribute__((section(".text.entry")))
const struct result *entry(void)
{
    uint32_t interrupt_mask;
    __asm__ volatile ("mrs %0, primask\ncpsid i" : "=r"(interrupt_mask) :: "memory");
    snapshot(report.before);
    PMU(KEY_CONTROL) = (PMU(KEY_CONTROL) & ~KEY_OFF_MASK) | X6_KEY_OFF;
    settle();
    PMU(POWER_CONTROL) = X6_POWER_OFF;
    settle();
    PMU(POWER_CONFIG) = X6_OFF_CONFIG;
    settle();
    PMU(POWER_ENABLE) &= ~UINT32_C(2);
    settle();
    PMU(POWER_ENABLE) &= ~UINT32_C(1);
    settle();
    snapshot(report.after);
    __asm__ volatile ("msr primask, %0" :: "r"(interrupt_mask) : "memory");
    return &result;
}
