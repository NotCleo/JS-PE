# USB Low-Speed device — design (step 1; not designed in, no RTL/ISS change)

Scope: USB 2.0 LS device; demo = HID keyboard (EP0 + EP1 interrupt IN). Pmod: ROADMAP_V2 §1; the demo board also wires uio4/uio5 to RP2350 GPIOs, which stay inputs with no pulls during USB operation (a few pF more on D+/D−, negligible against LS edge times of 75–300 ns and the 200–450 pF LS load). **E_TX = E0** (owns the pads), **E_RX = E1**, `.slots 0, 1, 0, 1`. Step 2 (usb_ls.s) changes are marked *(s2)*.

## 1. Pins and line states
- Pads: uio4 = D+ ← E0.p0; uio5 = D− ← E0.p1 with pad `inv`; uio6 = pull-up ← E0.p2 (`p2 <- 1` in `.init` = attach; detach = host clears PAD `en`).
- One E instruction per state (A on p0, B on p1): **J** (LS idle: D+ 0, D− 1) `p0 <- 0, p1 <- 0`; **K** `p0 <- 1, p1 <- 1`; **SE0** `p0 <- 0, p1 <- 1`. Both pads change in the same clock.
- Release: `p0 <- z`, then `p1 <- z` (B has no `z`); both lines are at J levels, so the skew is harmless.
- E_RX binds: p0 = uio_in[4] (D+, shift input), p1 = uio_in[5] (D−), p2 = G_TXB. E_TX binds p3 = uio_in[4] (line state). `.filter uio4, uio5` filters each line separately, so it does not remove crossover SE0 (each line has one clean edge, only skewed); USB allows crossover SE0 up to 210 ns at LS (TLST). The program confirms SE0 before an EOP counts (`EOPCHK`, §12). Fallback for worse edges: a USB transceiver on the Pmod.

## 2. Bit timing
- `CLK_HZ` 48 MHz (D126), BIT = 32 clk, P = 31, frac 0.
- E_RX: timer hard sync on both edges of p0 (TCFG `sedge` 3, `spin` p0): every NRZI transition re-times the grid. Tock `SAMPLE` = 13 clk after the synchronized edge (§12: margin both for late edges and for an early EOP).
- Drift: ≤ 7 bits between transitions (stuffing); host and device ±1.5 % each: 7 × 32 × 0.03 = 6.7 clk + ≤ 3 clk edge jitter vs the 19 clk left after the sample point.
- E_TX: own periodic grid, `tsync` per packet, no auto-sync; the next bit is decided between ticks (no tock).

## 3. RX path (E_RX)
- SHCFG: WL 8, LSB first, `diff` + `inv` (NRZI: no transition = 1). TAPCFG `rl` 6, `r1` 1, `lfen`. WL never changes inside a packet (an SHCFG write zeroes RUN).
- Hunt: `smp` (LV = idle level), `wrst`, `@rise p0` (first K), `loop { @tock : shin }`, `cmp S, #0x80` = SYNC.
- Byte loop, inline (no `call`: the link register belongs to the call-mode EOP watcher): `@tock : shin`; `jw` next bit; at a byte end `push S` if DATA is expected, two instructions after the last `shin`, before an early EOP; then the PID step; `jrun` → `@tock : smp` drops the stuff bit. *(s2)* No stuff-error check: the CRC or the key compare catches it.
- After the PID byte: `wcfg LFSR_LO` ← 0xFFFF, so every packet's LFSR covers exactly the bytes after its PID. The ACK test at EOP reads S (last byte), because a 1-byte packet can end before its PID reaches R1; a token's b2 to EP0/EP1 has bits 2:0 = 0, never 0xD2.
- *(s2)* Token check = **key**: LFSR_LO after b1, b2 (CRC5 included), one 16-bit compare at EOP with KEY0 / KEY1, which the RP2350 computes per address. An exact PID compare also checks ~PID.
- DATA (only while R2 holds a matched SETUP/OUT PID): bytes pushed unguarded; residual 0xB001 = good.
- EOP: watcher 0 (CC0 = ~p0 & ~p1; call mode with `EOPCHK`, else abort) → `eop`: SE0 confirm (§12), then dispatch on R1 (PID), S and R2 (0 / token PID / 2 = expect ACK).
- Stream per data packet: [DATA PID, payload, CRC lo, CRC hi, token PID], then EV_OK (1) or EV_CRC (2); EV_INACK (3).

## 4. TX path (E_TX) and turnaround
- SHCFG WL 16, LSB first; TAPCFG `rl` 6, `r1` 1, `alt` 2, `lfen`. A handshake is one word: SYNC | PID << 8 (ACK 0xD280, NAK 0x5A80).
- Per bit: `p3 <- out` (bit → B, RUN, LFSR); `jb` hold; else the J/K choice; after the tick `jrun` → `p3 <- q` (stuff 0) + forced transition. Static bound tick → next tick guard ≤ 29 clk of 32. J/K choice (§12): `TXJK` 1 keeps the line state in E_TX's C register (`xor C, R2`, cleared per packet; E_TX uses no tock); `TXJK` 0 reads D+ back with `@tock : jhi p3` TOCK = 6 clk before the drive tick (`.deadline` proves the drive is armed in time).
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
| Step 6: inline byte loop, `ecrc` before `hunt` | | −2 → 125 (`EOPCHK` 0, `TXJK` 0) |
| Builds (§12) | | 128 words: **128** (`EOPCHK` 1 +3); 512 words: **142** (`PIDCHK` +8, `EOPCHK` 3 +7, `TXJK` +2) |

Over the estimate: per-bit TX loop (21, the J/K branch + CRC splice), EOP dispatch (keys, disarm). Cuts left: OWNCHK 0 (−7; it also NAKs INs for other LS devices behind the same hub, which collides there). Options in 128 words: `-D OWNCHK=0 -D PIDCHK=1 -D EOPCHK=0` = 126 (PID checks, no SE0 confirm); `-D TXJK=1 -D EOPCHK=0` = 127. The 512-word build is chosen with `-D MEMWORDS=512 --mem 512` (§12).

## 8. Possibly RTL (flag only)
1. No toggle in action A, no `z` in B: NRZI TX needs the per-bit branch (21 words).
2. SHCFG write zeroes RUN: fixed WL on RX; CRC init needs a preset on TX.
3. FIFO depth 4 and no flow control toward the host (section 9).

## 9. Host rules (step 2)
1. **Keep-alive** (SE0 2 bits + J every ms) has no K, so no SYNC: only watcher 0 fires; R1 = 0xFFFF ("no packet") falls to the SETUP/OUT path, whose key compare fails, so nothing is answered (tested: 3 keep-alives reach `eop`, no byte, no handshake, the next SETUP works). **Bus reset** (SE0 ≥ 10 ms) takes the same path. The RP2350 detects it on its own GPIOs wired to uio4/uio5 (demo board, confirmed; inputs, no pulls): SE0 on every read of a ≥ 2.5 µs window with reads < 1.3 µs apart, e.g. 3 reads 1.25 µs apart (keep-alive SE0 is 1.33 µs; two EOPs are ≥ 13.8 µs apart, so sparse polls could join two of them), or SE0 timed by PIO, then it writes address-0 keys and BUFKEY 0. Not by `RDREG PINS`: its 4-byte reply holds H_TX, and at a packet tail that overflowed the FIFO (1 of 108 offsets swept at B = 16). Key aliasing: checked for all 128 × 16 (address, endpoint): keys distinct, none equal to 0, 0xFFFF, 0xB001, and no post-NAK LFSR state equals KEY0 — no real address is affected.
2. **SET_ADDRESS**: the status stage is an IN at address 0 answered from a zero-length DATA1 buffer. Only on its EV_INACK does the RP2350 write the new KEY0/KEY1 (one WRMEM), well inside the host's 2 ms recovery. Limitation: if the host's ACK is corrupted on the wire, the host has finished the status stage and moves to the new address while the chip stays at 0; the host's retries at the new address fail and it resets the port, which recovers (keys back to address 0).
3. **IN-buffer updates**: WRMEM only takes memory slots that no engine requests (ARCH 2.2), so engines are never delayed. It has no reply, so it never holds H_TX (the RX stream), and E_TX takes no host stream. Arm with one WRMEM from BUF to BUFKEY (KEY0 rewritten unchanged, BUFKEY last); write only while disarmed (after EV_INACK or a data event). No `RDMEM` while the bus is active.
4. **RX FIFO drain, LINK B = 16** (48 MHz: 3 Mbaud, RP2350 hardware UART; B = 8 needs the PIO host link, later). Worst case = 8 payload bytes with no stuffing: 12 bytes + 1 event, bytes ≥ 256 clk apart, the last CRC byte, token PID and event within ~70 clk. The link sends a byte in 160 clk (an event in 320), so between bytes the FIFO drains to ≤ 1 entry and the tail burst peaks at 3 of 4 entries. Measured (ISS, B = 16 link model, host reading continuously, 4 slot phases): peak 2 entries, no overrun. The only requirement on the RP2350 is that its UART receive buffer never fills (HOSTIF §1: ≥ 4 KB ring) and the rule **no `RDMEM`/`RDREG`/`SYNC` while the bus is active** (a reply holds H_TX; HOST_INTERFACE §7). Fallback only: if an overrun notice `ESC 0x81` still arrives, the byte was dropped but **the chip already ACKed** (CRC is checked on the bus bits), so the RP2350 treats the last data packet as lost and STALLs that transfer; the host then retries the request.

## 10. TX verification (step 4, ISS)
`sw/tests/test_usb_tx.py`, 80 tests, OWNCHK 1 and 0, slot phases 0–3: IN → DATA0/DATA1 for n = 0, 1, 4, 7, 8 bytes (all-0xFF and all-0x00 included); CRC16 from the buffer preset (residual 0xB001 at the host); host ACK → EV_INACK, BUFKEY 0, next IN NAK; resend until ACK; other-endpoint IN → NAK, buffer kept; STALL (stays armed); a SETUP disarms a stale buffer. EOP: SE0 2 bits ± 2 clk, J driven 1 bit, then both pads released.

| Turnaround (host SE0→J to first K), bits | ACK | NAK | DATA | STALL |
|---|---|---|---|---|
| phases 0–3 | 2.66–2.69 | 2.69–2.72 | 3.16–3.19 | 3.16–3.19 |

All within 2–6.5 (detachable cable), ≥ 3.3 bits of margin. Assembler bound vs ISS: every guarded step of both engines over a mixed sequence (SETUP/DATA, IN/DATA/ACK, NAK, bad CRC, keep-alive, STALL, foreign IN), 4 phases: 2 128 steps (OWNCHK 1) and 2 224 (OWNCHK 0); none above its bound. Statically, the stuff-bit `smp` path through `ret` has no bound ("?" in the listing); its measured steps are covered by the sweep.

## 11. Enumeration and review (step 5)
- `sw/tests/test_usb_enum.py`: host model (`usb_host.py`, USB retry rules) + RP2350 stand-in over the real host link (B = 16, WRMEM only, no reply commands): bus reset, GET_DESCRIPTOR(device) at 0, SET_ADDRESS (keys switched on its EV_INACK), device / configuration / HID report descriptors, SET_CONFIGURATION, then report descriptor + SET_IDLE while the first report is being armed, 10 key reports; NAK-until-ready throughout; one corrupted SETUP DATA retried. Time scaled (reset 1 500 clk). Cocotb `test/test_usb_ls.py`: GET_DESCRIPTOR(device) in RTL/ISS lockstep (21 s).
- Fix (0 words): the image now holds the address-0 keys (0x7C00, 0x0860) and E_RX presets LFSR_LO = 0xFFFF in `.init`; before, the first EOP after boot matched KEY0 = 0 against LFSR 0 and armed a phantom data packet.
- RP2350 rules added: (a) attribute EV_INACK to what was armed, in stream order; (b) on any SETUP/OUT data event write BUFKEY 0 first and wait until it has landed (UART backlog + 2 bytes) before re-arming, because an arm WRMEM in flight can land after the chip's disarm; an EV_INACK inside that window belongs to the stale arm; (c) PIDCHK 0: a data event whose closing token byte is not SETUP/OUT means a corrupted token disarmed the buffer: re-arm it; (d) PIDCHK 0: a DATA PID other than C3/4B was ACKed by the chip; for SETUP, STALL the data/status stage so the host retries the request; OUT data is lost (limitation; PIDCHK 1 removes it).
- A bus reset in the middle of a SETUP/OUT closes the pending data packet as EV_CRC (no bytes); the next SETUP works at once (tested).

## 12. Builds and slow edges (step 6)
`MEMWORDS` (128 default, `-D MEMWORDS=512 --mem 512`) sets the defaults; each is overridable with `-D`. BUF = 118 or 246 (one-word `li`; 246 also makes a 512 build fail at `--mem 128`).

| Option | 128 words (128) | 512 words (142) | Words |
|---|---|---|---|
| `OWNCHK` NAK only our address | 1 | 1 | +7 |
| `PIDCHK` exact PIDs | 0: corrupted PID → RP2350 STALLs (§11 d) | 1 | +8 |
| `EOPCHK` SE0 checks before EOP | 1: SE0 < 9–10 clk ignored | 3: SE0 < 21–22 clk (≥ 440 ns) ignored | +1 + 2n |
| `TXJK` J/K state | 0: D+ read 6 clk before the drive | 1: register | +2 |

- **EOP confirm.** Watcher 0 in call mode: `eop` delays with `ld R0, [R0]` and tests SE0 with `jnc0` (neither changes flags); SE0 gone → `ret` re-executes the preempted wait (events kept), so a crossover costs nothing. The handler must not change Z/CY: a crossover between `cmp` and its jump would take the wrong branch (seen: LFSR re-preset by the PID path, EV_CRC). Assembler fix: mnemonics may contain digits (`jc0`, `jnc0` were rejected).
- **Early EOP.** Real crossover SE0 means rises cross the threshold later than falls by the skew, so the D+ edge E_RX syncs on can be late by the skew while the EOP's SE0 (a fall) is not: SE0 arrives BIT − SAMPLE − skew after the last sample. The last byte's `push` therefore follows its `shin` with no taken jump, and the ACK test reads S. With late falls (SE1) the next edge can come up to the skew after the sample point: SAMPLE 13 balances both.
- **Measured (ISS, 4 phases, `test_usb_edges.py`).** Fixed skew: 512 words pass SE0 skew ≤ 10 clk (210 ns), SE1 ≤ 12; 128 words SE0 ≤ 8 clk (167 ns), SE1 ≤ 12. Random per-edge delays up to 14 clk (300 ns) with crossover SE0 0–10 clk (512) / 0–8 clk (128) or SE1 at every transition: SETUP, OUT, IN + ACK, keep-alive, NAK all correct. TX: chip's own pads seen 14 clk late, both builds plus `TXJK` 1 in 128 words: packets correct; `TXJK` 0 read-back passes up to 20 clk (417 ns) of delay.
- **RTL lockstep** (`test/test_usb_ls.py`, `USB_DEFS` selects options): default build and `TXJK=1 EOPCHK=0` pass. It caught two reads of never-written datapath registers (X in RTL, 0 in the ISS): S at the first EOP (ACK test) and C (`xor C, C`); `.init` now writes both (0 words).
- **128-word limit.** Crossover SE0 of 9–10 clk (188–210 ns) ends a packet (the host retries); the 512-word build covers the full 210 ns. `-D OWNCHK=0 -D EOPCHK=3 -D TXJK=1` = 127 words also covers it, at the cost of OWNCHK (§7).
