# Measurement programs (BACKLOG rank 4)

Capture `@edge : mov rd, T`, ±1 clk (BACKLOG 3.3). No MEMMAP region. At 50 MHz.

## (a) meter.s: width, period, duty
TCFG `sedge` = both, `swin` = 1, C = P = 0xFFFF: every edge restarts the timer in hardware (one-shot, saturates at 0). `@rise p0 : mov R1, T` reads the low phase, `@fall p0 : mov R2, T` the high phase: steps = 0x10000 − T, T = 0 over range. Record: low, high (4 bytes). Range per phase 1.31 ms / 21 ms / 335 ms (pre 0/1/2).
`EXT=1` (pre 0, `swin` = 0): watcher 0 calls on tick, `evt #1; ret`; each capture is pushed before the next edge, so the ticks of a phase sit between its neighbours' records: phase = 65536·ticks + 0x10000 − T, no upper bound.
Measured (D222): ±1 clk per phase and per sum of phases; high ≥ 1 / low ≥ 22 clk (EXT: 10 / 14); a shorter phase reads below the minimum (late capture), the next stays exact. EXT wrap window: edge 0–13 clk after a wrap −12…+13.5 clk. 7 / 9 words.

## (b) freq.s: edges per gate
Window = tock to tick of a one-shot `tset` (C = GATE: exact GATE·16^PRE clk; the `@tock` wait drops rises latched in the handler); watcher 0 aborts on tick. Count: 16-rise `shin` loop with `shin` input p1 = constant 1 (a late rise still shifts a 1), pass = `add S, #1` (clears S, CY) + `adc R0, R2`. Push S, R0 (+0); evt 2 = OVR (rises merged). Edges = popcount(S) + 16·R0, −1…+1 edge. Measured (D222): period ≥ 7 clk (7.1 MHz), high/low ≥ 1 clk; beyond, losses (evt 2 below 6 clk); pulses < 1 clk and > 25 MHz alias silently. 17 words.

## (c) nec.s: NEC IR receiver
IR module (active low) on a Pmod pin, BIND-inverted; pre 2.
- Leader: `@rise : tset`, mark ≥ 6.75 ms; space < 1.69 ms bad, < 3.375 ms repeat (`evt #1`), else frame.
- Bits: `@fall : tset`, `@tock : shin` at 1.125 ms, `jw`; two 16-bit words: addr, ~addr, cmd, ~cmd.
- Tick 3 ms after a mark end: abort, `evt #2`.
Margins: bits ±50 %, leader −25 %; host checks complements. Est. 30 words.

## (d) autobaud.s: UART RX locking on 0x55
0x55 falls at bits 0, 2, 4, 6, 8: `@fall : tsync`, capture at fall 5: Δ = 8 bits, wraps counted below 6.1 kbaud. Self-configure: PRE by wraps (0, < 16, more), Δs = Δ >> 4·PRE, BIT = Δs >> 3, FRAC = (Δs & 7) << 5; write P, C, TCFG; bit 8 low and stop high, else `evt #3` and re-measure; push `evt #4`, BIT, FRAC|PRE; enter the uart_rx loop (host fallback: re-EXEC uart_rx). 300 baud–1 Mbaud, error ≤ 1 step/8. Est. 30 words.

## Host (step 4): method choice in host/measure.py
Period capture (meter): ±1 clk per phase, and a sum of N periods is ±1 clk, so an average over time T has error 20 ns / T at any f. Gate counting (freq): ±1 edge per window, error 1 / (f·T). At equal T the meter always wins; its limit is the link (4 bytes/period: 78 k/s at B = 16, 312 k at B = 4) and low ≥ 22 clk. Rule: estimate f with a 10 ms freq window; f ≤ 50 kHz (B = 16) / 200 kHz (B = 4) → meter, PRE = smallest with period < 65 536 steps (EXT = 1 below 3 Hz), over-range readings step PRE up; else freq (window = requested T). OVR notice or evt 2 → retry / report.
