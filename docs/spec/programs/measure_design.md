# Measurement programs (BACKLOG rank 4)

Capture `@edge : mov rd, T`, ±1 clk (BACKLOG 3.3). No MEMMAP region. At 50 MHz.

## (a) meter.s: width, period, duty
TCFG `sedge` = both, `swin` = 1, C = P = 0xFFFF: every edge restarts the timer in hardware (one-shot, saturates at 0). `@rise p0 : mov R1, T` reads the low phase, `@fall p0 : mov R2, T` the high phase: steps = 0x10000 − T, T = 0 over range. Record: low, high (4 bytes). Range per phase 1.31 ms / 21 ms / 335 ms (pre 0/1/2).
`EXT=1` (pre 0, `swin` = 0): watcher 0 calls on tick, `evt #1; ret`; each capture is pushed before the next edge, so the ticks of a phase sit between its neighbours' records: phase = 65536·ticks + 0x10000 − T, no upper bound.
Measured (D222): ±1 clk per phase and per sum of phases; high ≥ 1 / low ≥ 22 clk (EXT: 10 / 14); a shorter phase reads below the minimum (late capture), the next stays exact. EXT wrap window: edge 0–13 clk after a wrap −12…+13.5 clk. 7 / 9 words.

## (b) freq.s: edges per gate
Window = tock to tick of a one-shot `tset` (C = GATE: exact GATE·16^PRE clk; the `@tock` wait drops rises latched in the handler); watcher 0 aborts on tick. Count: 16-rise `shin` loop with `shin` input p1 = constant 1 (a late rise still shifts a 1), pass = `add S, #1` (clears S, CY) + `adc R0, R2`. Push S, R0 (+0); evt 2 = OVR (rises merged). Edges = popcount(S) + 16·R0, −1…+1 edge. Measured (D222): period ≥ 7 clk (7.1 MHz), high/low ≥ 1 clk; beyond, losses (evt 2 below 6 clk); pulses < 1 clk and > 25 MHz alias silently. 17 words.

## (c) nec.s: NEC IR receiver
IR module (active low) on a Pmod pin, BIND-inverted; pre 2 (`CLK_HZ`, `PRE` scale the constants).
- Leader: `@rise : tset`, mark ≥ 6.75 ms; space < 1.69 ms ignored, < 3.375 ms repeat (`evt #1`), else frame.
- Bits: `@fall : tset`, `@tock : shin` at 0.84 ms (inside a 0's next mark, inside a 1's space; 1.125 ms would hit the end of a 0's next mark), `jw`; two 16-bit words: addr, ~addr, cmd, ~cmd.
- Tick 3 ms after a mark end: abort, `evt #2`.
Measured (D223): ±10 % per-edge jitter and ×0.8 / ×1.2 timing decode, repeats, short leader / leader space ignored, cut frame → evt 2 and the next frame clean. Host checks complements. 29 words.

## (d) autobaud.s: UART RX locking on 0x55
0x55 falls at bits 0, 2, 4, 6, 8. TCFG sedge = falls: each capture is one 2-bit interval, wraps counted by a tick call (`add R0, #1`; none can land between a capture and its sum). D = 8 bits (32 bit); PRE by D; BIT = Ds >> 3, FRAC = (Ds & 7) << 5; BIT < 15 → `evt #3`, re-measure; push `evt #4`, BIT, TCFG; uart_rx 8N1 loop. No stop/bit-8 check (no time left at 1 Mbaud): a wrong sync character shows as framing errors, the host re-RUNs. Next character ≥ 140 clk after the last sync fall (back to back from BIT 70). Measured (D223): 9.6 k–1 Mbaud at ±2 %, 1200 / 700 / 300 baud. 75 words.

## Host (step 4): method choice in host/measure.py
Period capture (meter): ±1 clk per phase, and a sum of N periods is ±1 clk, so an average over time T has error 20 ns / T at any f. Gate counting (freq): ±1 edge per window, error 1 / (f·T). At equal T the meter always wins; its limit is the link (4 bytes/period: 78 k/s at B = 16, 312 k at B = 4) and low ≥ 22 clk. Rule: estimate f with a 10 ms freq window; f ≤ 50 kHz (B = 16) / 200 kHz (B = 4) → meter, PRE = smallest with period < 65 536 steps (EXT = 1 below 3 Hz), over-range readings step PRE up; else freq (window = requested T). OVR notice or evt 2 → retry / report.
