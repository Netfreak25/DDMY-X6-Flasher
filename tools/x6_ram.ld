/* Caller supplies X6_RAM_ADDRESS from x6_profile.json. */
ENTRY(entry)
SECTIONS
{
    . = X6_RAM_ADDRESS;
    .payload : {
        *(.text.entry)
        *(.text*)
        *(.rodata*)
        *(.data*)
    }
    .bss : { *(.bss*) *(COMMON) }
    /DISCARD/ : { *(.comment*) *(.ARM.attributes*) *(.ARM.exidx*) }
}
ASSERT(SIZEOF(.bss) == 0, "RAM payload requires uninitialized memory")
ASSERT(SIZEOF(.payload) <= 0x200, "RAM payload exceeds its checked 512-byte slot")
