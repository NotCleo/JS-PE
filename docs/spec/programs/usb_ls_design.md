# USB Low-Speed device — design (step 1; not designed in, no RTL/ISS change)

Scope: USB 2.0 LS device, 1.5 Mbit/s, demo = HID keyboard (EP0 control + EP1 interrupt IN). Pmod: ROADMAP_V2 §1 (27 Ω series, 1.5 kΩ D− pull-up to uio[6]). Engines: **E_TX = E0** (owns the pads), **E_RX = E1**.

## 1. Pins and line states
- Pads: uio4 = D+ ← E0.p0; uio5 = D− ← E0.p1 with pad `inv`; uio6 = pull-up ← E0.p2 (`p2 <- 1` in `.init` = attach; detach = host clears PAD `en`).
- One E instruction per state (A on p0, B on p1): **J** (LS idle: D+ 0, D− 1) `p0 <- 0, p1 <- 0`; **K** `p0 <- 1, p1 <- 1`; **SE0** `p0 <- 0, p1 <- 1`. Both pads change in the same clock.
- Release: `p0 <- z` then `p1 <- z` (action B has no `z`); both lines already sit at J levels, so the 1-clk skew is invisible.
- E_RX binds: p0 = uio_in[4] (D+, shift input), p1 = uio_in[5] (D−), p2 = G_TXB, p3 = G_RDY. E_TX binds p3 = uio_in[4] (its line-state read for NRZI). `.filter uio4, uio5`: crossover SE0 glitches (< 40 ns) never reach the EOP watcher.

## 2. Bit timing
- `CLK_HZ` 48 MHz (D126), BIT = 32 clk, P = 31, frac 0.
- E_RX: timer hard sync on both edges of p0 (TCFG `sedge` 3, `spin` p0): every NRZI transition re-times the grid. Tock at mid-bit, 16 clk after the synchronized edge (2-FF + filter latency is common to all edges, so it cancels).
- Drift: stuffing bounds a run to 7 bits without a transition. Host ±1.5 % (spec allows the LS device ±1.5 % too): 7 × 32 × 0.03 = 6.7 clk worst case against 16 clk half-bit margin; LS edges (75–300 ns) add ≤ 3 clk crossover jitter. Margin ≥ 6 clk.
- E_TX: own periodic grid, `tsync` at the start of each packet, no auto-sync (it must not re-time on its own edges); tock 4 clk after each tick = decision point for the next bit. RP2350 clock is crystal-derived (≪ 1.5 %).
