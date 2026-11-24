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

## 3. RX path (E_RX)
- SHCFG: WL 8, LSB first, `diff` + `inv` (NRZI: no transition = 1). TAPCFG `rl` 6, `r1` 1, `lfen`. WL never changes inside a packet (an SHCFG write zeroes RUN).
- Hunt: `smp` in idle (LV = J), `@rise p0` (first K), `loop { @tock : shin }`, `cmp S, #0x80` = SYNC.
- Byte routine: `@tock : shin`; `jrun` → `@tock : smp`, `jb` = stuff error (seventh 1); `jw` loop.
- PID: `cmp S, #pid` per accepted PID; an exact match also checks ~PID. Others ignored.
- Token tail (b1, b2) compared with a 4-word table the RP2350 writes at SET_ADDRESS (EP0, EP1, CRC5 precomputed): no CRC5 on chip; a corrupt token simply fails the compare.
- DATA: `wcfg LFSR_LO` ← 0xFFFF after the PID byte; bytes pushed unguarded; residual 0xB001 = good.
- EOP: watcher 0 (CC0 = ~p0 & ~p1, LUT 0x1, abort) → `eop`, which acts on the packet state.
- Events: SETUP, OUT (data good, after its bytes), CRC error, IN-ACKed.

## 4. TX path (E_TX) and turnaround
- SHCFG WL 16, LSB first; TAPCFG `rl` 6, `r1` 1, `alt` 2. A handshake is one word: SYNC | PID << 8 (ACK 0xD280, NAK 0x5A80, STALL 0x1E80).
- Per bit, at the tock: `p3 <- out` (bit → B, RUN, LFSR); `jrun` → `p3 <- q` (stuff 0, booked); `jb` hold; else `jhi p3` picks `@tick` J or K. ≤ 20 clk of 28.
- DATA: words from the IN buffer (`ld`), LFSR ← 0xFFFF after word 0, then `rcfg` + `xor` 0xFFFF = CRC16, then EOP: `@tick` SE0, SE0, J, release.
- Turnaround: E_RX waits for the SE0→J edge (`@rise p1`), then `sset` G_ACK, G_NAK or G_DAT. E_TX (`@c0` = G_ACK | G_NAK, watcher 1 on G_DAT) clears it, `sset` G_TXB, `tsync`, first K on the second tick: SYNC starts ≈ 2.6 bits after EOP. Spec window 2–6.5 bits (detachable cable; 7.5 is the captive-cable figure; host timeout 16).
- NAK-until-ready: E_RX answers an IN with G_DAT only if G_RDY (written by the RP2350) and the token matches the armed endpoint, else G_NAK; G_STL turns NAK into STALL.
