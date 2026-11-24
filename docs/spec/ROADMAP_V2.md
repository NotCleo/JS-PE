# Cleo v2 roadmap: USB LS, 10BASE-T, and architecture improvements

Handoff from a separate analysis chat (2026-11-24). Every number below was checked against this repo (CAPABILITY.md, manchester.s, can.s, ISA.md, ARCH.md, DECISIONS D062–D064/D081/D090) or comes from my own SPICE simulation. Deadline: 18 Jan 2027.

## 1. Physical layer: designed and simulated (no chip changes)

**Organizer rule (quoted):** "magnetics, connector and any line driver go on the Pmod, and your chip does the rest (Manchester coding, framing, CRC). Same for USB… leaning on the RP2350's USB wouldn't count for much." So MAC/PHY chips (ENC28J60, PmodNIC100, W5500) and the RP2350's USB PHY are out.

**10BASE-T Pmod (uio[0] TXP, uio[1] TXN, uio[2] RXD, uio[3] LED):**
- 7SEMI RJ45 breakout, LPJ0011FBNL MagJack (P1 TD+, P2 TD−, P3 RD+, P6 RD−, P4/P5 centre taps; confirm which tap is TX by continuity).
- TX: 74LVC244A, 2 gates per wire, each with its own 100 Ω; 10 kΩ pull-downs on TXP/TXN; TX centre tap to GND via 100 nF. Idle = both low.
- RX: TLV3501 comparator, 100 nF AC coupling on each input, 100 Ω termination, bias dividers giving −IN ≈ 1.650 V and +IN ≈ 1.610 V (R12 = 9.53 kΩ), so RXD idles at 0.
- LTspice/ngspice: RX decodes the exact frame at 2.5 V and 0.6 V input; TX delivers ±1.6 V at the PC, which decodes correctly. Schematic, netlist, BOM and .asc files exist (author's folder `peltwork/`).

**USB LS Pmod (uio[4] D+, uio[5] D−, uio[6] pull-up enable):** 7SEMI micro-USB breakout (DP, DN, GND; VBUS and ID unconnected), 27 Ω in series on each data line, 1.5 kΩ from D− to uio[6] (soft connect). No active parts.

## 2. Feasibility on the current chip (rtl-freeze-v1, 128×16 flop memory, 50 MHz)

| Item | Verdict | Evidence |
|---|---|---|
| 10BASE-T frames | **Not possible** | Measured Manchester max: TX 2.27 Mbit/s (BIT ≥ 22 clk), RX 2.78 Mbit/s (BIT ≥ 18). 10BASE-T needs BIT = 4–6 clk: 3–5× short at any closable clock. Frames also need gap-free shifter↔memory movement; auto-transfer only reaches the FIFOs, and `ld`/`st` (2–5 clk) is longer than a half-bit |
| 10BASE-T link pulses | **Possible, no RTL** | 100 ns pulse every 16 ms from tick/tock; PC shows link up |
| USB LS | **Possible, no RTL** | 48 MHz = 32 clk/bit (1.8× above the RX limit). NRZI decode = SHCFG `diff`; stuffing = TAPCFG `rl`=6, `r1`=1 + `jrun`/`smp`/`q`(ALT stuff) on padless p3 (as in can.s); CRC5/16 = right-form LFSR (POLY 0x14 / 0xA001); SE0 = condition cell LUT 0x1 as a watcher; J/K/SE0 = D+ pad on E0.p0, D− pad on E0.p1 with `inv`, both set in one E instruction. TX NRZI needs a per-bit branch (`diff` acts on one pin only; SE0 needs separate pins): ~4 instructions per bit, within 32 clk. ACK/NAK deadline 7.5 bits = 240 clk: E1 sets a signal flag, E0 answers; slow work (descriptors) is NAK-until-ready on the RP2350 |
| USB LS risk | **128 words** | can.s uses 128/128. Estimate: E1 ~40 + E0 ~35–40 + IN buffer ~5 ≈ 80–85. Assemble early |
| USB full speed, 100BASE-TX, HDMI, USB HS | Impossible | Pads (~66 MHz out, no analog, no differential) |

## 3. Improvement options (need decisions, ranked by value)

| # | Change | Gain | Cost (estimate) | Lifts freeze? |
|---|---|---|---|---|
| 0 | PIO UART host link at LINK = 4 clk/bit | Host bandwidth ×4 (~1.2 MB/s); the hardware UART is stuck at 3 Mbaud (D090) | Host software only | No |
| 1 | **SRAM macro retry**: fix the LVS supply-name mismatch (VDD!/VSS!/VDDARRAY! vs VDD/VSS/VDDARRAY) with a netgen equate or a renamed CDL/LEF, starting from commit 6896dd8 | −133k µm² placed (D062), 512 words | Flow work; flop build stays as fallback | No (already a deferred PROGRESS task) |
| A | **Timer-clocked shifting**: shin on tock / out on tick in hardware, no instruction per bit | Bit rate limited by the timer (≥2 clk/bit), not fire/re-arm (~18–22): 5–10× faster bit layer for every protocol | ~27k µm² | Yes |
| B | **Line-code modes**: Manchester (D XOR half-bit phase), biphase-mark, pin-pair output with SE0 override | 10BASE-T TX, hardware USB NRZI on two pins, RC5, DALI | ~11k µm² | Yes |
| C | **Memory auto-transfer (DMA)**: per-engine pointer + count, push/pull to jspe_mem at word end | Whole frames (Ethernet, USB buffers, CAN bursts), logic-analyzer capture, pattern replay | ~22k µm² | Yes |
| D | Second LFSR | Full-duplex CRC; removes can.s limit 2 | ~10k µm² | Yes |
| E | Timer capture on edge | IR NEC, PWM in, DShot, SENT, 1-Wire | ~6.5k µm² | Yes |
| F | ×2 / ×4 multi-lane shift | QSPI, SD 4-bit, HUB75 | ~32k µm² | Yes |

Area today: 692k placed = 57.3 % of core, limit 60 % → ~32k µm² headroom. A–E (~77k) fits only after item 1. Area estimates assume ~27 µm² per placed cell (445k/23.7k synthesized × 1.42); they are not measured.

Timing rule for A–F: keep each local and registered (timer events → S → pin state register). Nothing may lengthen the guard → fire → pad path that D081 cut.

**Full 10BASE-T needs 1 + A + B + C**, and a clock of exactly 40 or 60 MHz for an even half-bit grid (50 MHz puts the mid-bit edge 10 ns off; 55 MHz adds jitter).

## 4. Decisions for the author

1. Lift the area freeze / rtl-freeze-v1 for a v2 RTL batch (new tag), or stay program-only?
2. Scope: (a) USB LS + 10BASE-T link pulses only (no RTL); (b) + SRAM retry; (c) + accelerators A/B/C (+ D/E); (d) full 10BASE-T.
3. Clock for the demos (48 MHz USB; 40/50/60 MHz Ethernet).
4. Re-verification budget: every RTL change needs isa.yaml → gen_isa → ISS → RTL → formal (shift/timer/epins) → lockstep → bound sweep → GDS → GLS, and README/CAPABILITY updates.

## 5. Suggested order

1. No RTL: usb_ls.s (design note first, per CLAUDE.md small steps), PIO host link, 10BASE-T link-pulse program, write-up section stating the measured 10BASE-T gap.
2. SRAM macro retry (time-boxed, fallback to flops).
3. If approved: A + B → C → rerun Manchester at 10 Mbit/s and 10BASE-T end to end → D/E if area allows. F is future work.
