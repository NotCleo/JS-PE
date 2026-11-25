# On-chip bridges (BACKLOG rank 1)

Two Cleo images; the host only loads and watches events.

## Engines and pads
- `bridge_uart_spi.s`: E1 UART RX (`ui_in[7]`, no pad) → E0 SPI controller, mode 0, MSB first, one byte per CS (SCK `uio3`, CS `uio0`, MOSI `uio1`).
- `bridge_i2c_uart.s`: E0 I2C target, writes only (SCL `uio6`, SDA `uio5`, od) → E1 UART TX (`uo0`).

## Handoff: G0
The consumer binds one logical pin's input to G0 (BIND source 16; output stays CS/TXD), waits `@c0` (level). Not `@rise`: `sset` on a set flag makes no edge. Producer: `st`, then `sset #0`; consumer: `ld`, then `sclr #0`.

## Memory map
Word 0 = `halt` + `jmp 0` (reset PC = 0, D109); code from word 2; buffer at 512−N…511 (`.org`, `.word`/`.space`), `.assert` code end ≤ 512 − N. Pointers run 0x10000 − N…0xFFFF (bits [8:0] = buffer): `add #1`, `or` 0x10000 − N wraps without a branch.

## UART → SPI: ring
Ring of N = 32, initialised free (bit 15 = 1). A received byte has bit 15 = 0 (the shifter keeps 8 bits); the consumer frees a slot by storing the wrap mask (bit 15 = 1).
- Producer: slot[WP] full at bit 3 → **overrun: drop the new byte, `evt #2`**; else store, `sset #0`. Pointer work sits between the first data samples: only `st` + `sset` precede the stop sample. Framing error `evt #1`.
- Consumer: slot[RP] full → free, advance, send. Empty → `sclr #0`, reload, wait `@c0` only if still empty. Same-clock set/clear (clear wins) is safe: `st` precedes `sset`.
- 47 code words (incl. park) + 32 ring.

## I2C → UART: mailbox, stretching
One word, one token: only the producer sets G0 (seen 0), only the consumer clears it (seen 1). Full → `jhi p2, $` holds SCL low after ACK. `st` + `sset` run while SCL is held: no START/STOP abort can split them. R/W = 1 NACKed at the address. ≈ 34 + 1 words.

## Rates (ISS)
- UART → SPI: 3.57 Mbaud = 357 kB/s (UART BIT 14, as the prototype). Sustained iff the SPI byte (156 clk at BIT 8, 356 at BIT 30) < one UART character; else bursts, then overrun.
- I2C → UART: 38.8 kB/s at 400 kHz, 1 Mbaud (I2C limits). Slower UART: SCL stretched ≤ one character; SMBus (25 ms) needs > 400 baud.
