# On-chip bridges (BACKLOG rank 1)

Two Cleo images; the host only loads and watches.

## Engines and pads
- `bridge_uart_spi.s`: E1 UART RX (`ui_in[7]`, no pad) → E0 SPI controller, mode 0, MSB first, one byte per CS (SCK `uio3`, CS `uio0`, MOSI `uio1`).
- `bridge_i2c_uart.s`: E0 I2C target, writes only (SCL `uio6`, SDA `uio5`, od) → E1 UART TX (`uo0`).

## Handoff: G0
The consumer binds one logical pin's input to G0 (BIND source 16; output stays CS/TXD), waits `@c0`. Not `@rise`: `sset` on a set flag makes no edge. Producer: `st`, then `sset #0`; consumer: `ld`, then `sclr #0`.

## Memory map
Word 0 = `halt` + `jmp 0` (reset PC = 0, D109); code from word 2; buffer at 512−N…511 (`.org`, `.word`/`.space`), `.assert` code end ≤ 512 − N. Pointers run 0x10000 − N…0xFFFF (bits [8:0] = buffer); `add #1` + `or` wraps branch-free.

## UART → SPI: ring
Ring of N = 32, loaded free (bit 15 = 1) by the image. A received byte has bit 15 = 0; the consumer frees a slot by storing the wrap mask.
- Producer: slot[WP] full at bit 3 → **overrun: drop the new byte, `evt #2`**; else store, `sset #0`. Pointer work sits between the first data samples, so only `st` + `sset` precede the stop sample. Framing error `evt #1`.
- Consumer: slot[RP] full → free, advance, send. Empty → `sclr #0`, reload, wait `@c0` only if still empty. A same-clock clear wins harmlessly: `st` precedes `sset`.
- 47 code words + 32 ring.

## I2C → UART: mailbox, stretching
One word, one token: only the producer sets G0 (seen 0), only the consumer clears it (seen 1). Full → `jhi p2, $` holds SCL low after ACK. `st` + `sset` run while SCL is held: no START/STOP abort can split them. Reads, other addresses: no ACK. 30 code words + mailbox.

## Rates (ISS)
- UART → SPI: 3.57 Mbaud = 357 kB/s (UART BIT 14). SPI byte P = 9·BIT + 3·CSDLY + 36 clk; sustained iff P ≤ 10·UBIT (BIT ≤ 6 at 3.57 Mbaud). Else first drop at byte ≈ 32·P/(P − 10·UBIT): 307, 117, 68 at BIT 8, 12, 20.
- I2C → UART: 38.8 / 9.96 kB/s at 400 / 100 kHz. Slower UART: stretch = character − I2C byte; SMBus (25 ms) needs > 400 baud.
