# USB Low-Speed device — design (step 1; not designed in, no RTL/ISS change)

Scope: USB 2.0 LS device; demo = HID keyboard (EP0 + EP1 interrupt IN). Pmod: ROADMAP_V2 §1. **E_TX = E0** (owns the pads), **E_RX = E1**, `.slots 0, 1, 0, 1`. Step 2 (usb_ls.s) changes are marked *(s2)*.

## 1. Pins and line states
- Pads: uio4 = D+ ← E0.p0; uio5 = D− ← E0.p1 with pad `inv`; uio6 = pull-up ← E0.p2 (`p2 <- 1` in `.init` = attach; detach = host clears PAD `en`).
- One E instruction per state (A on p0, B on p1): **J** (LS idle: D+ 0, D− 1) `p0 <- 0, p1 <- 0`; **K** `p0 <- 1, p1 <- 1`; **SE0** `p0 <- 0, p1 <- 1`. Both pads change in the same clock.
- Release: `p0 <- z`, then `p1 <- z` (B has no `z`); both lines are at J levels, so the skew is harmless.
- E_RX binds: p0 = uio_in[4] (D+, shift input), p1 = uio_in[5] (D−), p2 = G_TXB. E_TX binds p3 = uio_in[4] (line state). `.filter uio4, uio5` removes crossover SE0 glitches.

## 2. Bit timing
- `CLK_HZ` 48 MHz (D126), BIT = 32 clk, P = 31, frac 0.
- E_RX: timer hard sync on both edges of p0 (TCFG `sedge` 3, `spin` p0): every NRZI transition re-times the grid. Tock at mid-bit, 16 clk after the synchronized edge.
- Drift: ≤ 7 bits between transitions (stuffing); host and device ±1.5 % each: 7 × 32 × 0.03 = 6.7 clk + ≤ 3 clk edge jitter vs a 16-clk half bit.
- E_TX: own periodic grid, `tsync` per packet, no auto-sync; tock 4 clk after each tick = decision point for the next bit.

## 3. RX path (E_RX)
- SHCFG: WL 8, LSB first, `diff` + `inv` (NRZI: no transition = 1). TAPCFG `rl` 6, `r1` 1, `lfen`. WL never changes inside a packet (an SHCFG write zeroes RUN).
- Hunt: `smp` (LV = idle level), `wrst`, `@rise p0` (first K), `loop { @tock : shin }`, `cmp S, #0x80` = SYNC.
- Byte routine: `@tock : shin`; `jrun` → `@tock : smp` drops the stuff bit; `jw` loop. *(s2)* No stuff-error check: the CRC or the key compare catches it.
- After the PID byte: `wcfg LFSR_LO` ← 0xFFFF, so every packet's LFSR covers exactly the bytes after its PID.
- *(s2)* Token check = **key**: LFSR_LO after b1, b2 (CRC5 included), one 16-bit compare at EOP with KEY0 / KEY1, which the RP2350 computes per address. An exact PID compare also checks ~PID.
- DATA (only while R2 holds a matched SETUP/OUT PID): bytes pushed unguarded; residual 0xB001 = good.
- EOP: watcher 0 (CC0 = ~p0 & ~p1, abort) → `eop`, which dispatches on R1 (PID) and R2 (0 / token PID / 2 = expect ACK).
- Stream per data packet: [DATA PID, payload, CRC lo, CRC hi, token PID], then EV_OK (1) or EV_CRC (2); EV_INACK (3).

## 4. TX path (E_TX) and turnaround
- SHCFG WL 16, LSB first; TAPCFG `rl` 6, `r1` 1, `alt` 2, `lfen`. A handshake is one word: SYNC | PID << 8 (ACK 0xD280, NAK 0x5A80).
- Per bit: `p3 <- out` (bit → B, RUN, LFSR); `jb` hold; else `jhi p3` picks `@tick` J or K; after the tick `jrun` → `p3 <- q` (stuff 0) + forced transition. Static bound tick → next tick guard ≤ 29 clk of 32.
- DATA *(s2)*: IN buffer [total bits 32 + 8n, CRC preset, SYNC | PID, data]; the preset (one constant per DATA0/DATA1, written by the RP2350) makes the LFSR 0xFFFF after word 0, because that boundary has no free clock. At R1 = 16: S ← LFSR ^ 0xFFFF (CRC16, also mid-word for odd n). EOP: `@tick` SE0, SE0, J, release.
- *(s2)* STALL = a buffer of 16 bits [16, –, SYNC | STALL]: no flag needed.
- Turnaround: E_RX waits for the SE0→J edge (`@rise p1`, latched if early), then `sset` G_ACK, G_NAK or G_DAT. E_TX (`@c0` = G_ACK | G_NAK, watcher 1 on G_DAT) clears all three, `sset` G_TXB, `tsync`, first K on the second tick: ≈ 3 bits (handshake) to 3.7 bits (DATA) after EOP. Window 2–6.5 bits (detachable cable; host timeout 16).
- NAK-until-ready *(s2)*: IN key = BUFKEY (memory) → G_DAT, else NAK if the key is KEY0/KEY1 (`OWNCHK` 1) or always (`OWNCHK` 0). Flags G_RDY/G_STL dropped: a host `WRREG SIG` replaces all eight flags and would race the engine flags.

## 5. LFSR sharing
Right form, POLY 0xA001, LFSR_HI 0, set in `.init`; both engines set `lfen`. Half-duplex keeps them apart: E_RX shifts only from a host SYNC to its EOP, E_TX only inside its own packet (≥ 2 bits after that EOP); E_RX waits `@fall p2` (G_TXB) before hunting, so it never shifts our packet. E_RX loads 0xFFFF, E_TX the preset: no handover.

## 6. Chip vs RP2350
- Chip: SYNC/PID/token match, data receive + CRC16, ACK/NAK/STALL, DATA send + CRC16, resend until ACK, disarm on IN-ACK and on every SETUP/OUT to us (a stale buffer can't answer a new request).
- RP2350: descriptors, SETUP decoding, keys, bus reset, toggle bit (in the PID it writes), drops duplicate OUT data by PID.

## 7. Word budget (step 2, assembled)
| Routine | Est. | Real |
|---|---|---|
| E_RX: hunt + SYNC + PID + push loop / byte / EOP dispatch + SETUP-OUT / DATA end + txw / ACK / IN | 62 | 16 / 5 / 16 / 13 / 6 / 15 = **71** |
| E_TX: IN start / handshake + idle / start / bit loop + CRC / EOP | 36 | 6 / 6 / 8 / 21 / 5 = **46** |
| Buffer + keys | 11 | 10 |
| Total | 109 | **127 / 128**; `-D OWNCHK=0`: **120** |

Over the estimate: per-bit TX loop (21, the J/K branch + CRC splice), EOP dispatch (keys, disarm). Cuts left: OWNCHK 0 (−7). SRAM build: OWNCHK 1, two buffers.

## 8. Possibly RTL (flag only)
1. No toggle in action A, no `z` in B: NRZI TX needs the per-bit branch (21 words).
2. SHCFG write zeroes RUN: fixed WL on RX; CRC init needs a preset on TX.
3. FIFO depth 4 and no flow control toward the host (section 9).

## 9. Host rules (step 2)
1. **Keep-alive** (SE0 2 bits + J every ms) has no K, so no SYNC: only watcher 0 fires; R1 = 0xFFFF ("no packet") falls to the SETUP/OUT path, whose key compare fails, so nothing is answered. **Bus reset** (SE0 ≥ 10 ms) takes the same path; the RP2350 detects it with two `RDREG PINS` reads ≥ 3 µs apart both showing SE0 (keep-alive SE0 is 1.33 µs), then writes address-0 keys and BUFKEY 0. Limitation: a key equal to 0, 0xFFFF, 0xB001 or the LFSR state after a NAK aliases (≈ 2⁻¹⁴ per address).
2. **SET_ADDRESS**: the status stage is an IN at address 0 answered from a zero-length DATA1 buffer. Only on its EV_INACK does the RP2350 write the new KEY0/KEY1 (one WRMEM), well inside the host's 2 ms recovery. A lost host ACK leaves the chip at address 0, where the host's retry is sent.
3. **IN-buffer updates**: WRMEM only takes memory slots that no engine requests (ARCH 2.2), so engines are never delayed. It has no reply, so it never holds H_TX (the RX stream), and E_TX takes no host stream. Arm with one WRMEM from BUF to BUFKEY (KEY0 rewritten unchanged, BUFKEY last); write only while disarmed (after EV_INACK or a data event). No `RDMEM` while the bus is active.
4. **RX FIFO drain**: a full DATA packet is 12 bytes + 1 event at ≥ 256 clk per byte (5.3 µs, more with stuffing), and token PID + event arrive back to back. The chip→host link must drain one entry per < 256 clk: **LINK B ≤ 16** (160 clk/byte; B = 8 recommended). A 4-byte `RDREG` reply holds H_TX for 4 bytes (320 clk at B = 8): allowed. The RP2350 UART buffer must absorb the link rate (HOSTIF §1: ≥ 4 KB ring). Overflow: `push` drops the byte, sets OVR, notice `ESC 0x81`; **the chip still ACKs** (CRC is checked on the bus bits). On that notice the RP2350 treats the last data packet as lost and STALLs the transfer, so the host retries the request.
