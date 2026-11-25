# Fault injection (BACKLOG rank 2, EI1)

Variants `-D FAULTS=1`; default images byte-identical (tested).

## Fault word
`FW = FAULT_W + ENG` = 475…478 (MEMMAP.md, `cleo_mem.inc`), 512-word build only. Claimed by `.reserve` (overlaps fail assembly).

| Bits | Field |
|---|---|
| 15 | ARMED |
| 14:12 | TYPE (0 = none) |
| 11:8 | POS: byte in the transaction |
| 7:0 | ARG |

The host writes FW only between transactions; the program reads it once per transaction (UART in each start bit, I2C at each ACK slot it gives, SPI after CS falls, CAN at frame pull) and writes 0 when the fault is done (spi_tgt: at frame start; lost if the frame is shorter than POS): it fires once. Undefined TYPEs alias; `host/fault.py` refuses them. Pointer `FW`: a spare register from `.init`, else `li` + `lih` (FW ≥ 256).

## Faults
| Program | TYPE | Effect | Receiver reports |
|---|---|---|---|
| uart_tx | 1 | next parity bit inverted (PARITY builds) | uart_rx event 2 |
| | 2 | stop bit 0, then one stop bit | event 1 |
| | 3 | line low ARG ≥ 2 bits from the start bit, byte dropped | ARG ≥ frame bits (10 for 8N1): 0x00 + event 1 |
| i2c_tgt | 1 | after POS ACK slots, NACK ARG slots (counted down; address: idle) | NACK |
| | 2 | SCL held ARG x 16 clk (+ ≤ 80) longer, then ACK | stretch |
| | 3 | not addressed: no ACK, idle | NACK, nothing pushed |
| | 4 | SDA low ARG x 16 clk from the ACK, then idle | SDA stuck (bit errors) |
| spi_tgt | 1 | response byte POS ^= ARG (mask; bit k = 1 << k) | bit mismatch |
| | 2, 3 | byte POS is 8 ∓ ARG bits (SHCFG) | later bytes slip ARG bits |
| can (TX) | 1 | CRC state ^= ARG << 1 before the last step | partner: CRC error, no ACK, not delivered; TX event 4, retry clean. Own RX: OK (shared LFSR, D211) |

No program change: hot-unplug = `PAD_*` `en` = 0 (formally proven).

Random campaigns: `host/fault.py`, `random.Random(seed)`: same seed, same campaign.

## Cost (measured)
| Program | Default words | FAULTS=1 | Timing |
|---|---|---|---|
| uart_tx | 8 | 29 + FW (8N1) | BIT ≥ 46 clk (≤ 1.08 Mbaud), per-path `.deadline` |
| i2c_tgt | 32 | 111 + FW | each ACK slot stretched ≈ 40 clk (+ ≤ 60 while counting down) |
| spi_tgt | 12 + 8 | 69 + 8 + FW | first edge ≥ 160 clk after CS falls, SCK ≤ 1 MHz |
| can | 128 | 150 + FW | 512-word build, `ORG_RX` + 24; rates unchanged |
