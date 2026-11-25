# On-chip bridges (BACKLOG rank 1)

Two Cleo images; the host only loads and watches events. Program-only.

## Engines and pads
- `bridge_uart_spi.s`: E1 UART RX (`ui_in[7]`, no pad) → E0 SPI controller, mode 0, MSB first, one byte per CS (SCK `uio3`, CS `uio0`, MOSI `uio1`).
- `bridge_i2c_uart.s`: E0 I2C target, writes only (SCL `uio6`, SDA `uio5`, od) → E1 UART TX (`uo0`).

## Handoff: G0
The consumer binds one logical pin's input to G0 (BIND source 16; output stays CS/TXD), waits `@c0` on its level. Level, not `@rise`: `sset` on a set flag makes no edge. Producer: `st`, then `sset #0`; consumer: `ld`, then `sclr #0`.

## Memory map
Word 0 = `halt` + `jmp 0` (reset PC = 0, D109). Code from word 2; buffer at the top, 512−N…511, reserved by `.org 512 − N` + `.space N`; `.assert` code end ≤ 512 − N. Pointers run 0x10000 − N…0xFFFF (bits [8:0] = buffer); overflow to 0 → `add #−N`, so stores stay inside.

## UART → SPI: ring, valid bit
Ring of N = 32; slot valid = bit 15 (producer `lih S, #0x80`, consumer stores 0 after `ld`). No shared index words (four registers).
- Producer: slot[WP] valid → **overrun: drop the new byte, `evt #2`**; else store, advance, `sset #0`. Framing error `evt #1`.
- Consumer: slot[RP] valid → clear, advance, send. Empty → `sclr #0`, reload, wait `@c0` only if still empty. A same-clock set/clear (clear wins) is safe: `st` precedes `sset`, so the reload sees it.
- ≈ 39 code words + 32 ring.

## I2C → UART: mailbox, stretching
One word, one token: only the producer sets G0 (seen 0), only the consumer clears it (seen 1). Full → `jhi p2, $` holds SCL low after ACK. `st` + `sset` run while SCL is held: no START/STOP abort can split them. R/W = 1 NACKed at the address. ≈ 34 + 1 words.

## Rates (BACKLOG §3.4)
- UART → SPI: prototype ceiling 3.57 Mbaud = 357 kB/s (UART BIT 14, SPI BIT 6/8), no ring; step 2 measures the max with the ring. Sustained iff the SPI byte (≈ 8·BIT + 70 clk) < one UART character; else 32-byte bursts, then overrun.
- I2C → UART: 38.8 kB/s at 400 kHz, 1 Mbaud (I2C limits). Slower UART: SCL stretched ≤ one character; SMBus (25 ms) needs > 400 baud.
