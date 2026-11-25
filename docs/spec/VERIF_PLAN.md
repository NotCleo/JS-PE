# VERIF_PLAN — verification plan (Phase 2b, 2026-11-15)

What is checked, against what, with which tool, and when a block, a protocol and the chip count as done.
References: REQUIREMENTS.md (what must work), ARCH.md / ISA.md / HOSTIF.md (how it behaves), programs/*.s (the programs under test).
Test levels in the tables: **I** = ISS, **B** = block, **R** = system RTL, **G** = gate level. Numbers assume clk = 50 MHz.

## 1. Levels

| Level | Checks | Tool, place, command | Phase |
|---|---|---|---|
| L0 static | lint; no latches, no combinational loops; `isa.yaml` self-check; generated `src/jspe_isa.vh` up to date | `make lint`, yosys `check -assert` inside `make area`, `sw/gen_isa.py --check` | every commit |
| L1 ISS | assembler, instruction semantics, cycle timing, every paper program against protocol checkers | pytest in `sw/tests`, `make swtest` | 4 |
| L2 block | each `jspe_*` block against ARCH.md; reference functions imported from the ISS | cocotb + Icarus, `test/unit/<block>/`, `make unit B=<block>` | 6 |
| L3 system RTL | whole chip through the top ports: host-link model loads images, bus models talk to the pads, ISS in lockstep | cocotb, `test/test_<protocol>.py`, `make test` | 5, 7 |
| L4 formal | properties of section 6 | SymbiYosys, `formal/<block>.sby` | 8 |
| L5 gate level | subset of L3 on the post-layout netlist | `make gl` (`GATES=yes`), GDS action | 5, 8 |
| L6 STA, physical | timing at 20 ns, DRC/LVS, Tiny Tapeout precheck, utilisation | LibreLane reports of the GDS action | 5, 8 |

The FPGA build and the RP2350 demos in `host/` are bring-up aids, not sign-off evidence.

## 2. Models and checkers (`sw/tests`, shared by L1, L3, L5)

"Independent" = written from the protocol standard, sharing no code with the ISS or the assembler.

| Model | Role | Checks it makes |
|---|---|---|
| `iss` | golden model of the chip (ISA.md, ARCH.md 4–9, HOSTIF.md), one step per clk | reference for lockstep; counts guards that fired late from a latched flag; functional coverage bins (section 10) |
| pad model | resolves every pad from the chip's `out`/`oe` and the partner's drivers; open-drain lines as wired-AND with a pull-up rise delay (0, 300 ns, 1000 ns) | contention (two push-pull drivers disagree); `uio_oe & uio_out` on an open-drain pad; any drive in a monitor test |
| host-link model | host side of HOSTIF.md: escapes, all operations, CTS behaviour on H_RDY (stops within one byte), reply and notice parser, `.bin` player | unexpected notices; every reply well-formed; byte loss |
| UART model (independent) | sender and receiver: 5–9 data bits, parity, stop length, baud offset, per-edge jitter, stop bit narrowed to a window | every edge time: bit lengths, mean bit time per frame, stop-bit drift; framing, parity, break |
| SPI controller / target models (independent) | modes 0–3, MSB/LSB first, programmable clock-to-MISO delay | setup and hold of the chip's outputs against the model's own sampling edges; CS timing |
| I2C bus checker, controller and target models (independent) | START/STOP decode, ACK, stretching by either side | UM10204 timing table for Sm/Fm/Fm+; SDA change while SCL is high other than START/STOP |
| WS2812 decoder (independent) | classifies every high pulse and low gap | T0H/T1H/T0L/T1L windows, reset time |
| Manchester encoder / decoder (independent) | edge-time based; frequency offset and edge jitter | mid-bit edge spacing, polarity |

## 3. Lockstep

- The testbench samples `ui_in`/`uio_in` at every rising clk edge and gives the same values to the DUT and to the ISS. The ISS returns the expected `uo_out`, `uio_out`, `uio_oe` after that edge. All 24 output bits are compared in every clock from the release of reset. The first mismatch fails the test with clock number, pad, expected, got and the last ISS trace lines.
- The host link is part of the comparison (H_TX, H_RDY are pads). Both sides parse the same H_RX waveform. A clock-level detail that HOSTIF.md leaves open is decided in the ISS first and written back into HOSTIF.md before the RTL is written.
- System tests use top ports only, so the same test runs on RTL, on the fallback-memory build and on the netlist. Internal state is reached through the host link (`RDREG ESTn`, `RDMEM`, `EXEC push`). Images for the fallback build are assembled with `--mem 128` and, for programs whose default `ORG` reaches past 127 (i2c_ctrl, i2c_tgt, ws2812, manchester), with `-D ORG=…`.
- Engine-level lockstep (L2, `jspe_engine`) may probe inside: PC, `irv`, S, R0–R2, T, flags, pin state, memory requests, FIFO strobes.
- Memory is undefined until written: X in RTL simulation, an "undefined" marker in the ISS. A fetch of an unwritten word fails the test.
- Phases: every L1 protocol test runs in the four slot phases (`RUN` delayed by 0–3 clk) and, for roles clocked from outside, with the partner shifted by 0, ¼, ½, ¾ clk. L3 runs phase 0 plus one random phase per seed; the seed is printed.
- Metastability is not simulated. The synchronizer is checked structurally (two flops, no logic between, section 6).

## 4. L1 — toolchain and ISS tests

| ID | Subject | Content |
|---|---|---|
| A1 | encodings | encode → decode round trip of all 67 instructions with corner field values; each of the 65 536 words decodes to exactly one instruction or to `undefined` (3 416 words, including the removed `stgl`, `sbc`, `swap`, `not` and `wcfg SIG`) |
| A2 | `isa.yaml` | no mask/value collision; fields outside fixed bits; guard field at [3:0]; encoding table of ISA.md 11 parsed and compared |
| A3 | assembler | every directive; several files with local symbols, `-D` for all files and for one, `--mem 128` overflow, address and pad overlap errors (D047); nested `.if`; `.init`/`.entry` → EXEC records in order; `.pad` enabled only after initialisation; `li` expansion; `jmp` relaxation at ±64; both `loop` forms; errors with line numbers (guard on JF/IM/RX, unencodable action pair, rel7 range, jump inside `.init`) |
| A4 | `.deadline` | the 9 `.deadline` paths of the paper programs in every variant (data bits, parity, stop, CPHA, rates); one word added to a path → error |
| A5 | images | `.hex`, `.bin`, `.lst`; a `.bin` replayed through the host-link model leaves the ISS in the same state as a direct load |
| S1 | instructions | every instruction with its flags and the corner cases of ISA.md 8 (rd = T, L is 9 bits, `ld` with rd = rb, write-back modes, A and B on one pin, `out` with `shin`); random operands against plain Python arithmetic |
| S2 | shifter | word length 1–16 x MSB/LSB x `inv` x `diff`; N and `wend`; PAR restart and `smp`; RUN, limit, `r1`; the four `q` sources; MIS; auto-transfer possible / blocked for `apush`, `apull`, both |
| S3 | timer | `tsync`, `tset`, `tstop`; tick at k + V + 1, tock at k + V − C + 1; average period with `frac` over 256 periods; prescale 16 and 256; hard sync; windowed sync (stopped, before tock, after tock, in the tick clock); a start in the tick clock |
| S4 | events | each rule of ARCH.md 6: waiting clears, busy latches, doubled event sets OVR only when consumed, timer starts clear tick/tock, abort clears all, level guards never latch, a late sample reads the live level |
| S5 | watchers | 16 selects x LUT; abort and call; both in one clock; `ret` re-executes; watcher during `ld`/`st` (a store granted in that clock is not written); none while stopped or under EXEC |
| S6 | host control | `RST`; `RUN` of several engines in one clock; `STOP`/`RUN` resume with IR still armed; `STEP`; `EXEC` with a guard wait, a memory access, a jump; `STOP` cancels a pending `EXEC`, and `RUN`/`STEP`/`EXEC` are rejected while one is pending (D045); `halt`; trap for each undefined class; notices |
| S7 | streams | `push`/`pull`/`evt` at a full or empty FIFO → OVR; read and write in one clock; H_RDY threshold; stream change after 4 entries; 0x1B escaping; events in order with data |
| S8 | pins | bind sources 0–31 and invert; no edge after a BIND write; pad modes (push-pull, `od`, `inv`, `en` = 0, `uo` pads, host-link pads); follow one clock behind S; input filter |
| S9 | shared blocks | signal flags set and cleared by 2–4 engines in one clock, `rcfg SIG`; LFSR left/right against CRC-8, CRC-15, CRC-16, CRC-32 vectors; lowest engine wins; write beats step |
| T1 | fetch timing | every row of the table in ARCH.md 4.2, in all slot phases, with slot maps 0xE4, 0x00, 0x44, 0x24 |
| T2 | micro-loop | the eight rows of ARCH.md 4.4, including entry at the tail and word length 1 |
| T3 | jump folding | `jmp` in PF; `jmp` arriving with IR empty; two jumps in a row; fold cancelled by a redirect |
| T4 | memory instructions | `ld` 2–5 clk, `st` 1–4 clk; cancelled by a watcher |
| T5 | static bound | for every `.deadline` path: measured arming delay ≤ computed bound over slot maps 0xE4, 0x00, 0x44, 0x24 x 4 slot phases x 4 partner phases (D054); late-fire count = 0 and OVR = 0 at the rates named in the program headers; late-fire count > 0 just above them |
| T6 | static bound, every step (D100) | every guarded instruction and loop of every paper program (manchester also with `CVCHK = 0`): arming delay of the next guarded instruction ≤ the assembler's bound for the path taken at the measured wait (not-prefetched step when the wait is below the slot gap), slot maps 0xE4, 0x00, 0x44, 0x24 x 4 slot phases, random pad toggles and host bytes with event spacing down to 0 clk; every `.assume armed` holds, and every `.deadline` holds, in the in-contract worst cases (header rates, minimum spacings, ±2 %, jitter) |
| T6 | isolation | pad trace of an engine alone = its trace with three other engines running `ld`/`st` loops (D018) |

## 5. L2 — block tests

Every block test also covers reset in the middle of an operation and back-to-back operations.

| Block | Corner cases beyond normal operation |
|---|---|
| `jspe_pins` | synchronizer latency exactly 2 clk; filter with pulses of 1, 2, 3 samples; every pad mode x value x enable; `od` never drives 1; `en` = 0; `uo` pad ignores `od` and `z`; writes to PAD[4], PAD[5] ignored, H_TX/H_RDY forced; several pads with one owner; reset values |
| `jspe_arb` | slot maps 0xE4, 0x00, 0x44, 0x24; all four engines and the host requesting at once; host served only in unused slots; address, data and write-enable mux |
| `jspe_mem` | both builds: read latency 1 clk, write then read, back-to-back accesses; fallback aliasing modulo 128; X before the first write |
| `jspe_fifo` | fill, drain; write when full and pop when empty ignored; write + pop in one clock when empty, half, full; `free`; flush; 8- and 9-bit width |
| `jspe_link` | every operation and reply; bit times 16, 8, 4 and the LINK change sequence; re-phasing on each start bit; H_RDY rule; literal 0x1B both ways; notice priority; every row of HOSTIF.md 9; break inside a command; breaks of 11, 15 and 25 bit times deliver no data byte (D044); data byte back to back after `SEL` to a full stream and after the H_RDY rule; pending memory write and the lost-write error; `WRMEM`/`RDMEM` with n = 1 and 256 and wrap at 512 |
| `jspe_sig` | all set/clear combinations from four engines; host write wins |
| `jspe_lfsr` | left and right form; halves written separately; write beats step; lowest engine wins |
| `jspe_timer` | list S3 |
| `jspe_shift` | list S2 |
| `jspe_alu` | 12 operations and the IM forms with operands 0, 1, 0x7FFF, 0x8000, 0xFFFF plus 10 000 random pairs; address adder in the four modes |
| `jspe_epins` | 32 bind sources; previous-level flop; actions `0 1 z d out q f t`; follow; LD and MIS |
| `jspe_seq` | lists T1–T4 and S4–S6; reset during a memory access; `STOP` and `RUN` in every state of the memory FSM |
| `jspe_engine` | lockstep against the ISS engine: random legal programs with random pin levels, FIFO traffic and grant patterns (≥ 200 seeds x 20 000 clk), then the paper programs |
| `tt_um_notcleo_jspe` | reset synchronizer; `ena` ignored; every output assigned |

## 6. L4 — formal properties

Prove by induction where stated, otherwise bounded with the depth recorded; every block has cover statements showing the interesting states are reachable.

| Block | Properties |
|---|---|
| `jspe_fifo` (prove) | count ≤ 4; `empty`, `full`, `free` agree with the count; write when full and pop when empty change nothing; a symbolic word comes out after exactly as many pops as there were entries before it; flush empties |
| `jspe_arb` (prove) | `e_gnt[i]` ⇔ `e_req[i]` ∧ slot owner = i; at most one grant among engines and host; `h_gnt` ⇒ the slot owner is not requesting; `mem_en` ⇔ a grant, address/data/write-enable are the granted client's; with map 0xE4 a held request is granted within 3 clk |
| `jspe_pins` (prove) | `en` = 0 ⇒ `uio_oe` = 0 and the pad output 0 (monitor guarantee); `od` ⇒ never `uio_oe` ∧ `uio_out`; a pad output depends only on its configured owner (pin-ownership exclusivity); `uo_out[4]`, `uo_out[5]` are H_TX, H_RDY whatever is configured; PAD registers change only by a host write; filtered level changes only after three equal samples; `lvl` is two flops behind the pad with no logic between |
| `jspe_link` (bounded) | parser state always legal; a break returns it to the data state; H_RDY high ⇔ data state or just after `ESC` (D080) ∧ stream selected ∧ fewer than 2 free entries; no FIFO entry written while the FIFO is full (`rx_wr` toward a full FIFO is ignored by jspe_fifo and raises `rx_ovf`, D086), at most one FIFO write per clock; an escape sequence is never interleaved; `EXEC` reaches only a stopped engine; SLOTMAP changes only while all engines are stopped and none has an injected instruction pending; memory request held until granted |
| `jspe_seq` (bounded ≥ 40, induction where possible) | decoder: `undefined` equals the predicate generated from `isa.yaml` for all words, exactly one format decodes; one memory request at a time; request → armed within 5 clk under the slot assumption; after a redirect nothing fetched before it executes; not active ⇒ no fire, no request, event flags 0; an event in a busy clock is consumed, latched or recorded as doubled, never dropped; no event flag survives a waiting clock; OVR rises only when a doubled flag is consumed or on a FIFO error; watcher fires only when enabled, on the rising cell, while running, and suppresses the fire of that clock; trap stops the engine with PC unchanged, halt with PC on the next instruction (D086) |
| `jspe_timer` (prove) | T counts down by one per enabled clock; tick ⇔ running ∧ T = 0, tock ⇔ running ∧ T = C; one-shot stops after its tick unless restarted in that clock; a start loads T, clears `FA`, `PSC`, `skip` and wins over the count step; windowed sync only when stopped or T < C |
| `jspe_shift` (prove) | bits of S above the word are 0 after a shift; N ≤ `wl`; `wend` as defined in ISA.md 7; at most one shift per clock; auto-transfer strobes only at `wend` with both FIFOs able; `smp` changes neither S nor N |
| `jspe_epins` (prove) | rise and fall never together and equal to the level change; no edge in the clock after a BIND write; a following pin takes the data bit unless an action names it |
| `jspe_sig`, `jspe_lfsr` (prove) | flag update formula of ARCH.md 2.6; LFSR step for a symbolic state in both forms; priority rules |
| top (bounded from any initial state) | after `rst_n` has been low for 3 clk: `uo_out` = 0x10, `uio_out` = 0, `uio_oe` = 0, and they stay there while H_RX is idle (no X, no drive before load) |

## 7. Protocol tests

Pass always includes: independent checker agrees, lockstep clean (R, G), no unexpected notice, OVR = 0 unless the test provokes it.

### 7.1 UART (`uart_tx.s` on E0, `uart_rx.s` on E1)
Timing criteria (D040). BIT = nominal bit time in clk (50 MHz / baud, not rounded).
- **mean**: the mean bit time over a frame is within ±1 % of BIT. Measured on back-to-back frames as (start edge → next start edge) / bits per frame.
- **bit**: every single bit is within 1 clk of BIT. Measured on 0x55 characters, where every bit boundary is an edge.
- **drift**: the stop-bit sample point lies within ±(BIT/2 − 2 clk) of the centre of the stop bit. TX: the sample point of a receiver that times from the start edge at the nominal rate, against the stop bit as transmitted. RX: the pad time of the chip's own stop-bit sample (firing clock minus the synchronizer latency of ARCH.md 4.1) against the sender's stop bit, for senders at −2 %, 0 and +2 %. At I it is read from the ISS sample trace. At R and G it is shown through the pads: the sender holds the stop level only inside that window (characters ending in a 0 bit) and no event 1 may appear.
- Expected from a timer-level model of ARCH.md 7 (to be confirmed by the ISS): bit deviation < 1 clk at every rate; mean error ≤ 0.1 % (0.4 % at 3 Mbaud); TX drift ≤ 1 clk; RX drift with a ±2 % sender 10.3 clk (8N1) and 12.3 clk (9 data bits + parity) at 1 Mbaud against a limit of 23 clk, 4.55 clk (measured, ISS) at 3 Mbaud against 6.33 clk; 9 data bits + parity at its 2.1 Mbaud limit (D100) 7.39 clk against 9.9 clk.

| ID | Test | Pass criteria | Levels |
|---|---|---|---|
| U1 | TX 8N1 at 1 200, 9 600, 19 200, 38 400, 57 600, 115 200, 230 400, 460 800, 921 600, 1 000 000 baud (`frac` for non-integer ratios) | **mean**, **bit** and **drift** (TX) hold at every rate | I, R (three rates), G (1 M) |
| U2 | TX formats: 5–9 data bits x parity none/even/odd x stop 1, 1.5, 2 at 1 Mbaud and 115 200 | decoded correctly; stop length exactly BIT, BIT + BIT/2, 2·BIT between back-to-back characters (9 data bits: up to 15 clk longer) | I, R |
| U3 | TX back to back: 256 characters queued | start to start exactly 10·BIT for all; first start bit after idle exact | I, R, G (16 characters) |
| U4 | RX 8N1 at the U1 rates; sender at −2 %, 0, +2 %; 1 000 back-to-back characters at 1 Mbaud from the +2 % sender | all characters, no event; **drift** (RX) holds for the three senders | I, R, G (1 M) |
| U5 | RX formats as U2 (sender stop 1, 1.5, 2) | characters and parity accepted; **drift** (RX) holds for 9 data bits + parity with senders at ±2 % | I, R |
| U6 | RX errors: stop bit low; wrong parity; both; break of 3 character times; start glitch shorter than half a bit; 1-clk noise pulse with FILT off and on | byte then event 1 / event 2; break = 0x00 + event 1 once, reception resumes at the next start edge; glitch and filtered pulse ignored | I, R |
| U7 | full duplex 1 Mbaud both ways, 4 KB random data each way (with 0x1B), link at 16 clk per bit with H_RDY; then TX at 115 200 with RX at 1 M | no loss, no OVR, TX stays back to back, both directions independent | I, R, G (64 bytes) |
| U8 | overflow: a 256-word `RDMEM` reply occupies H_TX while RX receives 160 characters | OVR notice once; delivered bytes are an in-order subset; reception continues | I, R |
| U9 | SHOULD: 3 Mbaud TX and RX (BIT 16 + `frac` 170); 300 baud (`BAUD` 300 selects prescale 16, one character each way, sender +2 %); inverted lines (`INV`); MSB first (`MSB`) (D057) | as U1/U4; at 300 baud the **bit** limit is one timer step (16 clk) because of the prescaler | I |

### 7.2 SPI controller (`spi_ctrl.s`)
| ID | Test | Pass criteria | Levels |
|---|---|---|---|
| SC1 | modes 0–3 x MSB/LSB first x BIT/SMP = 8/6, 10/5, 50/25, 500/250; n = 1, 2, 16, 255 bytes, random data | both directions correct; SCK period exactly BIT inside a byte; idle level = CPOL; MOSI changes only at launch edges; CS low for the whole transfer | I, R, G (modes 0 and 3, n = 4) |
| SC2 | CS timing with CSDLY = 6, 16, 1 000; changed between transfers by `EXEC` | setup = CSDLY + BIT (+ SMP for CPHA 0) + 2 clk, exact for CSDLY ≥ 9; hold = CSDLY + 11…16 clk (WL > 8: …20; slot phase of the last edge); inactive = max(CSDLY + 8, 20) clk with the next transfer's host bytes waiting (D091) | I, R |
| SC3 | target clock-to-MISO delay swept from 0 to (SMP − 3) clk − 10 ns (D081), and one clk more | correct up to the documented limit, wrong beyond it | I, R |
| SC4 | host data arrives late mid-transfer; H_TX blocked by an `RDMEM` reply | SCK idles with CS low, no byte lost or repeated | I, R |
| SC5 | SHOULD: BIT 6 write-only; BIT 4 with slot map 0x00 | SCK and MOSI as SC1 | I |

### 7.3 SPI target (`spi_tgt.s`)
| ID | Test | Pass criteria | Levels |
|---|---|---|---|
| ST1 | modes 0–3 x SCK 0.1, 1, 2 MHz (MUST) and 4 MHz (SHOULD) x 4 partner phases x 4 slot phases; command 0, 1, 7 followed by 1, 2, 7 response bytes | byte during the command = 0x00, then table[c], table[c+1], … | I, R, G (mode 0, 2 MHz) |
| ST2 | MISO drive under the timing rule (D101: CS high ≥ 25 clk, first sampling edge ≥ 7 clk after CS falls) | high-Z while CS is high; driven ≤ 4 clk + pad after CS falls; margin test: still in time at CS high 21 clk, late by ≤ 1 clk at 20 clk, data correct in both (D093) | I, R |
| ST3 | CS raised after 1…7 bits of the command and of each response byte | next transfer correct from its first bit | I, R, G (one position) |
| ST4 | outside the timing rule (shared bus): CS high for only 5 clk, first sampling edge 32 clk after the previous CS rise and 11 clk after CS falls (D100, D101); SCK pulses while CS is high anywhere in that time (D055) | transfer correct, no OVR | I, R |
| ST5 | SCK and MOSI activity while CS is high | ignored, MISO stays released | I, R |
| ST6 | SHOULD: 6 MHz with slot map 0x00 | as ST1 | I |

### 7.4 I2C controller (`i2c_ctrl.s`, target model on the bus)
| ID | Test | Pass criteria | Levels |
|---|---|---|---|
| IC1 | write 1, 2, 16 bytes; read 1, 2, 16 bytes with ACK and a final NACK; write, repeated START, read; STOP | bus decode equals the script; returned bytes hold data and the sampled ACK | I, R, G (write 1 + read 1) |
| IC2 | timing with TLOW/THIGH = 242/240 and a 1 000 ns rise, and 72/32 with a 300 ns rise, THD = 12 in both (D042) | tLOW ≥ 4.7/1.3 µs, tHIGH ≥ 4.0/0.6, tHD;STA ≥ 4.0/0.6, tSU;STA ≥ 4.7/0.6, tSU;STO ≥ 4.0/0.6, tBUF ≥ 4.7/1.3, tSU;DAT ≥ 250/100 ns, tHD;DAT ≤ 3.45/0.9 µs; inside a byte SDA changes ≥ 300 ns after SCL is driven low | I, R |
| IC3 | NACK on the address and on a data byte | reported in the returned byte; a following STOP step is correct | I, R |
| IC4 | target stretches SCL for 2 µs and 500 µs in bits 1, 8 and 9; for 1.2 ms; H_TX blocked by an `RDMEM` reply at a byte end | transfer correct, high phase ≥ THIGH after SCL reads high; at 1.2 ms event 3, both lines released, the next step is accepted; with H_TX blocked SCL stays low, no byte lost, no event 3 | I, R |
| IC5 | every I2C test | `uio_oe` only together with `uio_out` = 0 on SCL and SDA | I, R, G |
| IC6 | SHOULD: TLOW/THIGH = 20/16, THD = 0 (0.83 MHz measured, 60 clk/bit, D096) with a 120 ns rise | Fm+ timing table | I |

### 7.5 I2C target (`i2c_tgt.s`, controller model on the bus)
| ID | Test | Pass criteria | Levels |
|---|---|---|---|
| IT1 | addresses ADDR, ADDR ± 1, 0x00, 0x7F; own address changed by `EXEC` | ACK only for the own address; nothing reaches the host for others | I, R |
| IT2 | write 1, 2, 16 bytes at 100 and 400 kHz | each byte acknowledged; host receives the address byte, then the data | I, R, G (2 bytes, 400 kHz) |
| IT3 | read 1, 2, 16 bytes at 100 and 400 kHz; controller ACKs all but the last | bytes from the host on the bus; lines released after the NACK | I, R, G (2 bytes, 400 kHz) |
| IT4 | START, repeated START and STOP at each of the 9 bit positions of the address, a written byte and a read byte | partial byte dropped, lines released, next transfer correct | I, R |
| IT5 | write while an `RDMEM` reply blocks H_TX; read with host data 0, 10 µs, 1 ms late | SCL held meanwhile, no byte lost; SDA changes ≥ TSU + 2 clk (TSU + 2..5, ≥ 440 ns with TSU 20, D095) before SCL is released (≥ 400 ns with a 300 ns rise) | I, R |
| IT6 | timing | SCL hold ≤ 4 clk + pad after SCL falls; every SDA change of the chip THD + 3…4 clk after the SCL fall at the pad (320–340 ns with THD 13, D049, D081); the chip never changes SDA while SCL is high | I, R |
| IT7 | controller at minimum tHIGH/tLOW (Fm), 4 partner phases x 4 slot phases | as IT2/IT3 | I |
| IT8 | ACK setup with zero rise time at 100 kHz (TSU 13, the `.assert` limit) and 400 kHz (TSU 20): writes and a read address (D097) | ACK on SDA ≥ 300 ns (100 kHz) / ≥ 150 ns (400 kHz) before the chip releases SCL; measured TSU + 3..6 clk | I, R |

### 7.6 Programs not designed in (`ws2812.s`, `manchester.s`)
| ID | Test | Pass criteria | Levels |
|---|---|---|---|
| W1 | 64 LEDs, random colours plus all-0 and all-1 | decoder returns the bytes; T0H 0.4, T1H 0.8, T0L 0.85, T1L 0.45 µs each within ±150 ns; bit period 63 clk across byte boundaries; then low ≥ 280 µs | I, R, G (8 LEDs) |
| W2 | host pauses 10 µs and 100 µs inside a frame | line low during the pause, resumes on a byte boundary, no pulse shorter than 21 clk | I, R |
| W3 | 256 LEDs with H_RDY flow control | no pause inside the frame | I |
| M1 | TX frames of 1, 2, 64 bytes at BIT 50 and 22 | independent decoder returns the bytes; mid-bit edges exactly BIT apart, no gap between bytes, line low after the last bit | I, R |
| M2 | RX at BIT 50, 22, 18 (RX limit, D100): preamble 0x55 and 0xAA (first bit 0 and 1), payload 1 and 64 bytes, exactly 2 preamble bits (01, 10), sender offset ±2 %, edge jitter: each mid-bit edge up to BIT/4 − 2 clk off one bit after the previous one (cycle to cycle, random and alternating extremes, D098) | payload bytes then one event 4; no event 4 at the start of a frame | I, R |
| M3 | RX errors: no delimiter; line stuck in the SFD and in mid-frame; frames 3 bits apart; H_TX blocked; a missing mid-bit transition (D098) | `CVCHK = 1` (default, D099): no delimiter and a line stuck before the SFD give no event; a frame ends with exactly one event 4 at a missing transition (any data bit) or a stuck line, the partial byte dropped (a violation in a byte's last bit: that byte is complete and delivered), the bytes before it intact. `CVCHK = 0`: no delimiter → only event 4; missing transition: bytes before it intact, then ≤ 4 junk bytes and ≤ 2 event 4. Both: next frame complete; OVR set and byte alignment kept | I, R |
| M4 | loop-back E0 → E1 over `uo_out[3]` → `ui_in[4]`, both polarities | payload equal | I, R, G |
| F1 | flexibility evidence | `git diff rtl-freeze -- src/ info.yaml` empty for the commits adding these programs; W1 and M4 pass at I, R and G | CI |

## 8. System tests

| ID | Test | Pass criteria | Levels |
|---|---|---|---|
| H1 | reset, `SYNC`, load and read back random 512-word images at 16, 8, 4 clk per bit; fallback build with 128 words | read-back equal; ID correct | I, R, G (16 clk) |
| H2 | each operation; initialisation by `EXEC`; `STEP` through a program | EST and register read-outs equal the ISS after every step | I, R |
| H3 | host sends at full rate into the stream of a stopped engine; then `ESC STOP` while not ready | H_RDY rises with 2 entries free, no byte lost, command accepted | I, R |
| H4 | each row of HOSTIF.md 9 | chip behaviour and notice as listed; link usable afterwards | I, R |
| H5 | `rst_n` at a random clock of every protocol test; deselect modelled as all state X followed by reset | pads at reset values within 3 clk; reload and rerun pass | R, G (one protocol) |
| H6 | echo program (`@hrx : pull S`, `@htx : push S`) with 100 KB at each link rate | throughput = raw link rate within 1 %, no loss | I, R (16 clk) |
| C1 | four engines (PINS.md 3.7): I2C target, UART TX, UART RX, SPI target, all partners active for 200 000 clk | each checker passes; each engine's pad trace equals its solo run | I, R, G (20 000 clk) |
| C2 | E3 reloaded (SPI target → WS2812) while E1/E2 stream UART at 1 Mbaud | no error on the running engines | I, R |
| MON1 | SHOULD: all pads `en` = 0; passive decode of UART (two lines), SPI and I2C traffic between two models (programs written in Phase 7) | decoded data correct; `uio_oe` = 0 and no protocol `uo` pad changes during the whole test | I, R |
| EI1 | SHOULD: error injection variants (UART wrong parity, low stop bit, break; I2C forced NACK and extra stretch; SPI short word, CS glitch) | the partner model reports exactly the injected error | I, R |
| RND1 | random legal programs on four engines with random pad and host traffic, ≥ 200 seeds x 20 000 clk; a second set containing undefined words | lockstep clean; trap exactly where the ISS traps | R |

## 9. Gate level and STA

- Gate level: the tests marked G, unchanged, on the netlist of the GDS action, with the PDK cell models and the SRAM behavioural model (or the fallback netlist). Limit: 30 min of simulation. A mismatch against RTL is triaged as: X from an unreset flop, a reset or initialisation dependency, or a real synthesis difference.
- STA (LibreLane, `CLOCK_PERIOD` 20): setup slack ≥ 0 in the slow corner and hold slack ≥ 0 in the fast corner after routing; goal ≥ 2 ns setup slack; zero slew, capacitance and fan-out violations. The only unconstrained inputs are `ui_in`, `uio_in` and `rst_n` into their first synchronizer flop. The eight path groups of ARCH.md 12 are read from the report and compared with the estimates; a path above 14 ns is reviewed. Slack at 16.7 ns is recorded for the 60 MHz stretch goal.
- Physical: DRC, LVS and antenna clean, Tiny Tapeout precheck passes, placed cell area + macro ≤ 60 % of the die and `make area LIB=…` ≤ the area cap of each block (ARCH.md 11, D051).
- SRAM macro retry (Phase 8, deferred, one attempt, after all protocols pass on the flop build): fix the LVS supply-name mismatch (`VDD!`/`VSS!`/`VDDARRAY!` in the extracted layout vs the netlist names) via netgen equates or the macro's powered netlist, starting from commit 6896dd8 (D058, D060–D062). Adopt only if gds + LVS + precheck + gl_test are all green; otherwise stay on flops.
- Hold buffers (Phase 8, investigate, no change before): GDS run 43849101041 inserted 2 417 `dlygate4sd3` hold-delay cells, more than its 2 275 flip-flops; they likely explain part of the 1.42 placed/synthesized ratio (D064). Find the cause (flop-memory read path, clock skew across the 2 048 memory flops, `PL_RESIZER_HOLD_SLACK_MARGIN` 0.1 / `GRT_RESIZER_HOLD_SLACK_MARGIN` 0.05) from the hold report, and estimate the area recoverable before changing any setting.
- Engine area lever (Phase 8, only if the hardened design needs area, D070): the 8:1 operand muxes of `jspe_alu`, the write-back port on R0–R2 / S and the second tap mux of `jspe_shift`. Options: D051 (a) ALU destination limited to R0–R2 and S (user decision, ISA change); decoded strobes from `jspe_seq` instead of the per-block decode (≈ −0.9k µm² per engine measured with `synth -flatten`). Any change reruns the full engine lockstep.
- FIFO area lever (Phase 8, only if the hardened design needs area, D075): write at entry 0, shift up on push, head picked by a one-hot AND-OR from `v` (−2.1k µm² over the eight FIFOs, D072). Rejected for now because `rdata` would no longer come straight from flops (ARCH 2.4, P5); adopting it needs the spec change and the P5 timing rechecked first.
- IR drop (Phase 8, manual, D061): the flow's PSM check and IR-drop report are disabled because PSM cannot model the SRAM pins on the stripe layer. Estimate by hand from the final run: peak current I = peak power (OpenROAD power report, slow and typical corners, 50 MHz, switching activity of the worst protocol mix) / VDD; worst path = Metal1 rail from the farthest cell to its stripe (half the stripe pitch) + one Metal4 stripe over the full die height, R = sheet resistance x length / width per segment (PDK values), current shared over the stripes in proportion. Pass: worst drop ≤ 5 % of VDD (60 mV). The write-up states that IR drop is a hand estimate, not a signed-off analysis, and why. Status (2026-11-20, docs/IR_DROP.md, run 44625407065): pass per net.
  - Per net: worst 32.3 mV = 2.7 % (lumped, 2× power, single-edge feed); realistic 6.2 mV = 0.5 %.
  - VDD+VSS combined: worst 64.6 mV = 5.4 % (over 5 %, inside 10 %); realistic 12.3 mV = 1.0 %.
  - Corner: typ power only; the slow-corner report is only in the full artifact.

## 10. Coverage

- Functional bins, collected by the ISS: each instruction executed; each guard code on each guarded format; each conditional jump taken and not taken; each row of the tables in ARCH.md 4.2 and 4.4; each event-flag case (seen live, latched, consumed from the flag, doubled, overrun); both watcher modes; each undefined class; FIFO full and empty; auto-transfer possible and blocked. Goal: all bins hit by L1; a bin that cannot be hit is listed with its reason.
- Every protocol and system test is one function with an ISS backend and a DUT backend, so the bins hit at L1 are exercised at L3 through lockstep.
- Code coverage of the RTL (Verilator, L3 suite): goal ≥ 95 % of lines, every pad bit toggled both ways; each uncovered line explained.

## 11. Done criteria

| Item | Done when |
|---|---|
| Phase 4 (ISS) | `make swtest` passes: A1–A5, S1–S9, T1–T6 and every test marked I, each program in at least two configurations with its error cases |
| RTL block | unit test passes with the corner list of section 5; lint clean; cell area ≤ cap; reviewer findings fixed |
| Protocol | all its R tests pass on both memory builds, lockstep clean, reviewer findings fixed |
| Formal | every property of section 6 proven or bounded to its recorded depth; every cover reached |
| Chip sign-off | all MUST rows of section 12 pass at R and G; RND1 clean; STA and physical criteria of section 9 met; F1 shown in the git history |

## 12. Traceability of the MUST requirements

| Requirement (REQUIREMENTS.md 4) | Tests |
|---|---|
| UART TX/RX 8N1, baud 1 200–1 M: mean error ≤ ±1 % over a frame, every bit within 1 clk (D040) | U1, U4 |
| UART RX tolerance ±2 %: stop-bit sample point drift within ±(BIT/2 − 2 clk) (D040) | U4, U5 |
| UART 5–9 data bits, parity, stop 1/1.5/2 | U2, U5 |
| UART framing and parity error per character | U6 |
| UART back-to-back frames | U3, U4 |
| UART full duplex 1 Mbaud without overrun | U7 |
| SPI controller modes 0–3, bit order, N bytes with CS held | SC1 |
| SPI controller 100 kHz – 6.25 MHz, MISO sampling with pad latency | SC1, SC3 |
| SPI controller CS setup, hold, inactive time programmable | SC2 |
| SPI target modes 0–3 up to 2 MHz, register device | ST1 |
| SPI target MISO high-Z while CS inactive | ST2 |
| SPI target CS abort and resynchronisation | ST3, ST4 |
| I2C controller 100/400 kHz timing | IC2 |
| I2C open-drain SDA and SCL | IC5, `jspe_pins` property |
| I2C controller START, repeated START, STOP, multi-byte, ACK/NACK | IC1, IC3 |
| I2C controller clock stretching with timeout | IC4 |
| I2C target address match | IT1 |
| I2C target write and read | IT2, IT3 |
| I2C target START/STOP at any bit position | IT4 |
| I2C target stretching; 100 and 400 kHz controllers | IT5, IT2, IT3, IT7, IT8 |
| WS2812 timing, reset, ≥ 64 LEDs | W1–W3 |
| Manchester TX/RX ≥ 1 Mbit/s, polarity, edge-recovered timing | M1–M4 |
| Zero RTL diff for the not-designed-in programs; ISS, RTL, GL | F1 |
| Status visible, reload at any time, deselect | H1–H5 |
| Timing at 50 MHz, area, precheck, GL test | section 9 |

SHOULD items with tests: concurrency C1, C2; monitor MON1; error injection EI1; U9, SC5, ST6, IC6.

## 13. Known gaps and risks

- ISS and RTL come from the same reading of the spec. Countered by the independent protocol checkers, the formal properties written from ARCH.md, and the reviewer pass per block.
- Asynchronous inputs: only sub-clock phase is swept; metastability and analog edges (I2C rise) are delays in the pad model.
- Slow rates are long in simulation (1 200 baud = 417 000 clk per character): long streams at L1 only, one character at L3.
- SRAM macro: failed LVS in Phase 5 (D062, cause corrected in D128); the retry (D128–D130) passed and the SRAM build ships (D131), simulated with the PDK behavioural model. The flop fallback (`-DJSPE_MEM_FLOP`, `make test MEM=flop`) keeps every test runnable.
- Monitor and error-injection programs do not exist yet (Phase 7); their tests are SHOULD.
