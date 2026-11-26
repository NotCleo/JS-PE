# Memory map (program convention, D209)

The hardware has no fixed regions (ARCH 10). Programs that keep data the host or another engine reads or writes take it from one chip-wide map, so any set of programs that fits on the engines also fits in memory without collisions. Symbols: `docs/spec/programs/cleo_mem.inc` (`.include "cleo_mem.inc"`); a program claims its region with `.reserve` and the assembler rejects any other file's code, data or `.reserve` there (ISA 9).

## 512-word build (SRAM, shipped)

| Words | Symbol | Owner | Note |
|---|---|---|---|
| 0 … 454 | below `MEM_DATA` | code and `.word` tables of all programs | default `ORG`s as today; word 0 = reset PC (D109) |
| 455 … 464 | `SWD_BOX`, `SWD_BOX_N` | swd_dp (engine 0 at 455, engine 1 at 460) | RDB_L, RDB_H (RP2350-written), ST (both), DL, DH (chip-written); debug_design.md |
| 465 … 474 | `USB_BUF`, `USB_BUF_N` | usb_ls | IN buffer 7, KEY0, BUFKEY, KEY1; RP2350-written |
| 475 … 478 | `FAULT_W` + engine, `FAULT_N` | `-D FAULTS=1` variants | one fault word per engine (programs/fault_design.md) |
| 479 | `BR_MBOX` | bridge_i2c_uart | one-word mailbox |
| 480 … 511 | `BR_RING`, `BR_RING_N` | bridge_uart_spi | ring must end at 511: pointers 0x10000 − N … 0xFFFF wrap on overflow |

Rules: a new data region is added here and in `cleo_mem.inc` (directly below the lowest one, `MEM_DATA` moved down), never as a literal address in a program. Code must end below `MEM_DATA`; the `.reserve` check enforces it for every image that loads the region's owner.

## 128-word build (flop fallback)

Addresses alias modulo 128. usb_ls's 128-word build fills the memory and runs alone (buffer at `USB_BUF_FLOP` = 118…127). The other data programs (bridges, fault variants) are for the 512-word build.
