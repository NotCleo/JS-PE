# Measurement programs (BACKLOG rank 4)

Capture `@edge : mov rd, T`, ±1 clk (BACKLOG 3.3). No MEMMAP region. At 50 MHz.

## Shared: wrap
P = 0xFFFF; `pre` 0/1/2: step 20 ns/320 ns/5.12 µs, wrap 1.31/21/335 ms. Watcher 0 calls on tick: `evt #1; ret` (as D218). Host: t = 65536·wraps − T; a record with T < 0x8000 after a wrap event precedes it. Edges latched in the handler read late (≤ 12 clk).

## (a) meter.s: width, period, duty
`@rise p0 : mov R1, T`, `@fall p0 : mov R2, T`, 4 `@htx` pushes. Record: rise T, fall T (4 bytes). `AVG=K`: skip K−1 rises (`loop { @rise p0 : shin }`), period = Δrise/K.
High ≥ 1.5 clk, low ≥ 22.5 clk; ≤ 78 k records/s at B = 16. Duty 1–99 % up to 22 kHz, faster with AVG. A glitch is a short record (host min-width filter). Est. 12 words.

## (b) freq.s: edges per gate
Gate: one-shot `tset` (1/10/100 ms at pre 0/1/2); watcher 0 aborts on tick. Count: 16-rise `shin` loop, `li S, #0`, `add R0, #1`, carry into R1. Handler: restart gate, push S, R0, R1. Edges = popcount(S) + 16·(R0 + 65536·R1) over gate − D (dead time); ±1 edge. Input ≤ 12.5 MHz est. Est. 16 words.

## (c) nec.s: NEC IR receiver
IR module (active low) on a Pmod pin, BIND-inverted; pre 2.
- Leader: `@rise : tset`, mark ≥ 6.75 ms; space < 1.69 ms bad, < 3.375 ms repeat (`evt #1`), else frame.
- Bits: `@fall : tset`, `@tock : shin` at 1.125 ms, `jw`; two 16-bit words: addr, ~addr, cmd, ~cmd.
- Tick 3 ms after a mark end: abort, `evt #2`.
Margins: bits ±50 %, leader −25 %; host checks complements. Est. 30 words.

## (d) autobaud.s: UART RX locking on 0x55
0x55 falls at bits 0, 2, 4, 6, 8: `@fall : tsync`, capture at fall 5: Δ = 8 bits, wraps counted below 6.1 kbaud. Self-configure: PRE by wraps (0, < 16, more), Δs = Δ >> 4·PRE, BIT = Δs >> 3, FRAC = (Δs & 7) << 5; write P, C, TCFG; bit 8 low and stop high, else `evt #3` and re-measure; push `evt #4`, BIT, FRAC|PRE; enter the uart_rx loop (host fallback: re-EXEC uart_rx). 300 baud–1 Mbaud, error ≤ 1 step/8. Est. 30 words.
