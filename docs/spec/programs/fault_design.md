# Fault injection (BACKLOG rank 2, EI1)

Variants `-D FAULTS=1`; the default (`FAULTS = 0`) image is byte-identical (tested).

## Fault word
`FW = FAULT_W + ENG` = 475…478 (MEMMAP.md, `cleo_mem.inc`), 512-word build only. Images `.reserve FW, 1` with `.word 0`; any other program's use of it is an assembly error.

| Bits | Field |
|---|---|
| 15 | ARMED |
| 14:12 | TYPE (0 = none) |
| 11:8 | POS: byte in the transaction |
| 7:0 | ARG: count, length, bit or mask |

The host writes FW only between transactions; the program reads it once per transaction (UART in each start bit, I2C at each ACK slot it gives, SPI after CS falls, CAN at frame pull) and writes 0 when the fault is done: it fires once, and `RDMEM FW` = 0 confirms it. Pointer `FW`: a spare register from `.init`, else `li` + `lih` (FW ≥ 256).

## Faults
| Program | TYPE | Effect | Receiver reports |
|---|---|---|---|
| uart_tx | 1 | next parity bit inverted (PARITY builds) | uart_rx event 2 |
| | 2 | stop bit 0, then one stop bit | event 1 |
| | 3 | line low ARG ≥ 2 bits from the start bit, byte dropped | 0x00 + event 1 |
| i2c_tgt | 1 | after POS ACK slots: NACK ARG slots in a row (both counted down in FW) | NACK |
| | 2 | SCL held ARG x 16 clk (+ ≤ 80 decode) longer, then ACK | stretch length |
| | 3 | not addressed: no ACK, idle | NACK, nothing pushed |
| | 4 | SDA low ARG x 16 clk from the ACK, SCL released, watchers off, then idle | SDA stuck (bit errors) |
| spi_tgt | 1 | response byte POS, bit ARG[2:0] inverted | one-bit mismatch |
| | 2, 3 | byte POS is 8 ∓ ARG bits (SHCFG) | later bytes slip ARG bits |
| can (TX) | 1 | next CRC XOR (ARG << 7) | event 2 + error flag, no event 1; clean retry |

No program change: hot-unplug = `PAD_*` `en` = 0 (proven: ⇒ `uio_oe` = 0); stuck line = EXEC `p1 <- 0` on a stopped engine.

Random campaigns: `host/fault.py` draws (TYPE, POS, ARG, transaction) from `random.Random(seed)`; same seed, same campaign.

## Cost (uart_tx, i2c_tgt measured; spi_tgt, can estimates)
| Program | Default words | FAULTS=1 | Timing |
|---|---|---|---|
| uart_tx | 8 | 29 + FW (8N1) | BIT ≥ 46 clk (≤ 1.08 Mbaud), per-path `.deadline` |
| i2c_tgt | 32 | 99 + FW | each ACK slot stretched ≈ 40 clk (+ ≤ 60 while counting down) |
| spi_tgt | 12 + 8 | ≈ 34 | first edge > 7 clk after CS falls: step 3 |
| can | 128 | ≈ 135 | SRAM only, `ORG_RX` up; ≤ 500 kbit/s |
