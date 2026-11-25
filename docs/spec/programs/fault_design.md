# Fault injection (BACKLOG rank 2, EI1)

Variants `-D FAULTS=1`; the default (`FAULTS = 0`) image is byte-identical (tested).

## Fault word
`FW = FAULT_W + ENG` = 475…478 (MEMMAP.md, `cleo_mem.inc`), 512-word build only. FAULTS images `.reserve FW, 1` and place `.word 0` there; the assembler rejects any other program's use of it, so they combine with the bridges and usb_ls.

| Bits | Field |
|---|---|
| 15 | ARMED |
| 14:12 | TYPE (0 = none) |
| 11:8 | POS: byte in the transaction |
| 7:0 | ARG: count, length, bit or mask |

The host writes FW only between transactions; the program reads it once per transaction (UART before the start bit, I2C after the address compare, SPI after CS falls, CAN at frame pull) and writes 0 when the fault is done: it fires once, and `RDMEM FW` = 0 confirms it. Pointer `FW`: a spare register from `.init`, else `li` + `lih` (FW ≥ 256).

## Faults
| Program | TYPE | Effect | Receiver reports |
|---|---|---|---|
| uart_tx | 1 | next parity bit inverted (PARITY builds) | uart_rx event 2 |
| | 2 | stop bit driven 0 | event 1 |
| | 3 | line low ARG bits, then the character | 0x00 + event 1 |
| i2c_tgt | 1 | NACK byte POS of the next ARG transfers (count stored back) | NACK |
| | 2 | SCL held ARG x 16 clk longer after byte POS (≤ 81 µs) | stretch length |
| | 3 | address ignored once | address NACK |
| | 4 | SDA held low ARG x 256 clk from the ACK of byte POS, watchers off | SDA stuck, STOP fails |
| spi_tgt | 1 | response byte POS, bit ARG[2:0] inverted | one-bit mismatch |
| | 2, 3 | byte POS is 8 ∓ ARG bits (SHCFG) | later bytes slip ARG bits |
| can (TX) | 1 | next CRC XOR (ARG << 7) | event 2 + error flag, no event 1; clean retry |

No program change: hot-unplug = `PAD_*` `en` = 0 (proven: ⇒ `uio_oe` = 0); stuck line = EXEC `p1 <- 0` on a stopped engine.

Random campaigns: `host/fault.py` draws (TYPE, POS, ARG, transaction) from `random.Random(seed)`; same seed, same campaign. Not the on-chip LFSR (shared with CRC).

## Cost (estimates, `.assert`ed in steps 2–3)
| Program | Default words | FAULTS=1 | Timing |
|---|---|---|---|
| uart_tx | 8 | ≈ 26 | back-to-back limit: step 2 |
| i2c_tgt | 32 | ≈ 60 | none: checks run while SCL is held |
| spi_tgt | 12 + 8 | ≈ 34 | first edge > 7 clk after CS falls: step 3 |
| can | 128 | ≈ 135 | SRAM only, `ORG_RX` up; ≤ 500 kbit/s |
