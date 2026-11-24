# USB Low-Speed device — design (step 1; not designed in, no RTL/ISS change)

Scope: USB 2.0 LS device; demo = HID keyboard (EP0 + EP1 interrupt IN). Pmod: ROADMAP_V2 §1. **E_TX = E0** (owns the pads), **E_RX = E1**.

## 1. Pins and line states
- Pads: uio4 = D+ ← E0.p0; uio5 = D− ← E0.p1 with pad `inv`; uio6 = pull-up ← E0.p2 (`p2 <- 1` in `.init` = attach; detach = host clears PAD `en`).
- One E instruction per state (A on p0, B on p1): **J** (LS idle: D+ 0, D− 1) `p0 <- 0, p1 <- 0`; **K** `p0 <- 1, p1 <- 1`; **SE0** `p0 <- 0, p1 <- 1`. Both pads change in the same clock.
- Release: `p0 <- z`, then `p1 <- z` (B has no `z`); both lines are at J levels, so the skew is harmless.
- E_RX binds: p0 = uio_in[4] (D+, shift input), p1 = uio_in[5] (D−), p2 = G_TXB, p3 = G_RDY. E_TX binds p3 = uio_in[4] (line state). `.filter uio4, uio5` removes crossover SE0 glitches.

## 2. Bit timing
- `CLK_HZ` 48 MHz (D126), BIT = 32 clk, P = 31, frac 0.
- E_RX: timer hard sync on both edges of p0 (TCFG `sedge` 3, `spin` p0): every NRZI transition re-times the grid. Tock at mid-bit, 16 clk after the synchronized edge.
- Drift: ≤ 7 bits between transitions (stuffing); host and device ±1.5 % each: 7 × 32 × 0.03 = 6.7 clk + ≤ 3 clk edge jitter vs a 16-clk half bit.
- E_TX: own periodic grid, `tsync` per packet, no auto-sync; tock 4 clk after each tick = decision point for the next bit.

## 3. RX path (E_RX)
- SHCFG: WL 8, LSB first, `diff` + `inv` (NRZI: no transition = 1). TAPCFG `rl` 6, `r1` 1, `lfen`. WL never changes inside a packet (an SHCFG write zeroes RUN).
- Hunt: `smp` in idle (LV = J), `@rise p0` (first K), `loop { @tock : shin }`, `cmp S, #0x80` = SYNC.
- Byte routine: `@tock : shin`; `jrun` → `@tock : smp`, `jb` = stuff error (seventh 1); `jw` loop.
- PID: `cmp S, #pid` per accepted PID; an exact match also checks ~PID. Others ignored.
- Token tail (b1, b2) compared with a 4-word table the RP2350 writes at SET_ADDRESS (EP0, EP1, CRC5 precomputed): no CRC5 on chip; a corrupt token fails the compare.
- DATA: `wcfg LFSR_LO` ← 0xFFFF after the PID byte; bytes pushed unguarded; residual 0xB001 = good.
- EOP: watcher 0 (CC0 = ~p0 & ~p1, LUT 0x1, abort) → `eop`, which acts on the packet state.
- Events: SETUP, OUT (data good, after its bytes), CRC error, IN-ACKed.

## 4. TX path (E_TX) and turnaround
- SHCFG WL 16, LSB first; TAPCFG `rl` 6, `r1` 1, `alt` 2. A handshake is one word: SYNC | PID << 8 (ACK 0xD280, NAK 0x5A80, STALL 0x1E80).
- Per bit, at the tock: `p3 <- out` (bit → B, RUN, LFSR); `jrun` → `p3 <- q` (stuff 0, booked); `jb` hold; else `jhi p3` picks `@tick` J or K. ≤ 20 clk of 28.
- DATA: words from the IN buffer (`ld`), LFSR ← 0xFFFF after word 0, then `rcfg` + `xor` 0xFFFF = CRC16, then EOP: `@tick` SE0, SE0, J, release.
- Turnaround: E_RX waits for the SE0→J edge (`@rise p1`), then `sset` G_ACK, G_NAK or G_DAT. E_TX (`@c0` = G_ACK | G_NAK, watcher 1 on G_DAT) clears it, `sset` G_TXB, `tsync`, first K on the second tick: SYNC starts ≈ 2.6 bits after EOP. Spec window 2–6.5 bits (6.5 for a detachable cable, not 7.5; host timeout 16).
- NAK-until-ready: E_RX answers an IN with G_DAT only if G_RDY (written by the RP2350) and the token matches the armed endpoint, else G_NAK; G_STL turns NAK into STALL.

## 5. LFSR sharing
Right form, POLY 0xA001, set in `.init`; both engines set `lfen`. Half-duplex keeps them apart: E_RX shifts only from a host SYNC to its EOP, E_TX only inside its own packet (≥ 2 bits after that EOP), and E_RX waits `@fall p2` (G_TXB) before hunting, so it never shifts our packet. Each side loads 0xFFFF itself: no handover.

## 6. Chip vs RP2350
- Chip: SYNC/PID/token match, data receive + CRC16, ACK/NAK/STALL, DATA send, resend until ACK.
- RP2350: descriptors, SETUP decoding, address (writes the token table), bus reset (polls PINS: SE0 > 2.5 µs), toggle bit (writes the PID with each buffer, re-arms after IN-ACKed: 0 chip words vs ~4), drops duplicate OUT data by PID, sets G_RDY/G_STL.
- IN buffer: 7 words [endpoint tag, bit count, SYNC|PID, 4 data words]; the other endpoint gets NAK.
- Host link B = 8: one byte per 80 clk vs one per 256 received.

## 7. Word budget
| Part | Words |
|---|---|
| E_RX: hunt 6, byte 6, PID 10, token 12, data + CRC 10, EOP/IN/ACK 14, errors 4 | 62 |
| E_TX: dispatch 5, bit 13, data/CRC 12, EOP 6 | 36 |
| Tables + buffer | 11 |
| Total | 109 / 120 |

Cuts if over: stuff-error check −2, STALL −3 (NAK; Windows then times out DEVICE_QUALIFIER), merge DATA0/1 compare −2. SRAM build: CRC5 and toggle on chip, two buffers.

## 8. Possibly RTL (flag only)
1. No toggle in action A, no `z` in B: NRZI TX needs the per-bit branch.
2. SHCFG write zeroes RUN: fixed WL 8 on RX, token compared by bytes.
3. FIFO depth 4: the RP2350 must drain received bytes in time.
