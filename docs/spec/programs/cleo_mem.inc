; cleo_mem.inc - chip-wide data map (docs/spec/MEMMAP.md). Include with .include "cleo_mem.inc";
; claim a region with .reserve. Code and tables stay below MEM_DATA.
.equ MEM_DATA,     455          ; lowest data word, 512-word (SRAM) build
.equ SWD_BOX,      455          ; swd_dp: 5 words per instance (engine 0, 1): RDB_L, RDB_H, ST, DL, DH
.equ SWD_BOX_N,    10
.equ USB_BUF,      465          ; usb_ls: IN buffer (7), KEY0, BUFKEY, KEY1
.equ USB_BUF_N,    10
.equ FAULT_W,      475          ; fault word of engine e = FAULT_W + e (fault_design.md)
.equ FAULT_N,      4
.equ BR_MBOX,      479          ; bridge_i2c_uart mailbox
.equ BR_RING,      480          ; bridge_uart_spi ring; must end at 511 (branch-free pointer wrap)
.equ BR_RING_N,    32
.equ USB_BUF_FLOP, 118          ; usb_ls 128-word build (flop fallback): fills the memory, buffer at its top
