# BACKLOG — v2 ideas checked against the repo (2026-11-25)

Triage of a private list of questions and ideas (planning notes, not in the repo) against the state of main after D131 (512-word SRAM build shipped) and D200–D204 (USB LS merged). Analysis and measurement only: nothing here is implemented. Verdicts: **covered** (already in the repo, cited), **improve** (worth doing: value, days, RTL or not), **drop** (with the reason). Measurements are in section 3, the ranked list in section 4, the recommendation in section 5.

## 1. Verdicts

| # | Topic | Verdict | Evidence or reason | Value / days / RTL |
|---|---|---|---|---|
| 1 | Cold start + RP2350 GPIO release | **improve** | The chip side is complete and safe: reset leaves pads released and engines stopped (ARCH 9.1); the `.bin` order is CLK → RST → SLOTMAP → MEM (+ read-back) → FILT → PAD `en` = 0 → EXEC → PAD `en` = 1 → RUN (HOSTIF 7); R0–R2, S, P, C, T and the LFSR are not reset (D203 lint). Missing: the board side. No document says which RP2350 GPIOs each demo leaves as inputs; only USB has a line (INTERFACING, `uio[4..6]` inputs without pulls). The host itself drives `ui_in[3]` (link) and, in the UART demo, `ui_in[7]` (PIO partner). | Bring-up reliability; 0.5 d doc + a per-demo GPIO plan in the host examples, checked on the board; no RTL. The plan belongs in the host SDK (a manifest next to the `.bin`), not in a new `.bin` record. |
| 2 | CDR / resync | **covered** for every shipped protocol; hardware options **drop** | Software CDR exists: windowed timer sync (manchester.s, D099), hard sync on each edge (can.s; glitch behaviour measured, CAPABILITY row 9), timer-placed sample points, optional input filter (D033: three equal samples, not a majority vote). UART resyncs per character, SPI/I2C carry a clock, CAN/USB runs are ≤ 6 bits. A phase-limited (SJW) resync would be an RTL change in `jspe_timer` for one documented CAN limit. | SJW resync: write-up future work. Oversampling register: area measured in 3.5, value only for raw capture. |
| 3 | CDC | **covered**; new CDC hardware **drop** | One clock domain, every pad through the 2-FF sync (ARCH 4.1, `jspe_pins` formal). The cost of oversampling an external clock is measured and stated: SPI target ≤ 4.44 MHz (CAPABILITY row 4, D093/D101). An SCK-clocked front end needs a second clock tree, CTS, STA exceptions and CDC formal for one protocol. | Write-up: one paragraph on why single-clock, with the SPI-target cap. |
| 4 | Protocol × role × mode matrix | **covered** (shipped rows); **improve** (planned rows) | CAPABILITY.md §2 is that matrix for the 10 shipped rows: modes, tested configurations, rate with source tag, words, engines, program, tests, freeze status. Missing: rows for what is *not* built (REQUIREMENTS §6 stretch protocols) with status "to write / needs RTL / impossible". | Write-up clarity; 0.5 d; no RTL. Add a §5 "candidates" table to CAPABILITY, rows only appended. |
| 5 | Firmware layout / parameter patching | **improve** (manifest now, patch table later) | Checked: numeric options (`BIT`, `ADDR`, `CSDLY`, `THIRD`…) end up as `.init` immediates, i.e. EXEC records in the `.bin` (6–53 `.init` lines per program), so a host can patch them without re-assembly. Structural options (`PARITY`, `CPHA`, `WL > 8`, `MEMWORDS`) change code through `.if` and need the assembler. A patched `BIT` also leaves the program's `.assume armed BIT − k` proofs behind: a patcher must enforce each program's documented minimum. Running `jspe_asm.py` (1 048 lines, CPython) on the RP2350 is unverified and not needed. | (a) A manifest of named prebuilt images (make target → `prebuilt/`): 1 d, no RTL. (c) An assembler-emitted parameter table (name → EXEC record, field, min/max): 1–2 d, no RTL. |
| 6 | Resident set / hot swap | **covered** in mechanism; **improve** as SDK operations | Hot swap of one engine next to running ones: D088 (load read-back in short RDMEMs), test C2. A resident set works today: STOP → EXEC `jmp entry` → RUN, plus PAD writes. New fact: all ten shipped programs total 429 words and fit in the 512-word memory at once (default ORGs overlap, so it needs an ORG plan); the shared LFSR rules out CAN and USB at the same time. Pad moves: HOSTIF 6 asks for `en` = 0 before a stop/reset when a glitch matters; the full move order (en 0 → STOP/EXEC → owner change → en 1) is not written as one procedure. `host/jspe.py` has `load/send/recv` only. | Multi-protocol demos; 1–1.5 d (SDK functions `park/point/move_pad`, a conflict check over pads, engines, words, LFSR use); no RTL. |
| 7 | Parallel / multi-lane | **drop** for v1 (write-up future work) | Confirmed: an E instruction acts on at most two pins (action A any pin, action B p0/p1 only; ISA 8.1); the shifter has one data tap. Parallel buses need a multi-lane shifter (REQUIREMENTS §6 "multi-bit parallel shift"), not a SerDes. Area is no longer the blocker (3.5); RTL, pad-mapping timing near the D081 path and re-verification are. | Correct the framing in the write-up; ×4 as future work. |
| 8 | Line codes | **covered** in software; hardware Manchester **drop** for v1 | NRZ, inversion (`inv`), NRZI decode (`diff`), stuffing (`rl`/`r1`/ALT stuff), CRC on the LFSR, parity: ISA 5/7; Manchester TX/RX in software (manchester.s, TX 2.27 / RX 2.78 Mbit/s). A Manchester/biphase TX mode is small (3.5) but buys nothing without full 10BASE-T, which is out of scope (D125). | Write-up: list of covered codes; MLT-3 and 4B/5B impossible or out of reach. |
| 9 | DMA | **covered** by software (measured 3.1); hardware **drop** | An engine running `pull → st` or `ld → push/shift` moves 2.5 words/µs with one slot, 5 with two, and its memory traffic cannot change another engine's timing (ARCH 2.2; measured identical pad traces). The software DMA is 4× faster than the fastest host link, so a hardware DMA would only help line-rate packet capture, which needs RTL accelerators that are out of scope. Swapping an engine for DMA hardware loses concurrency (PINS 3.7 uses four). | Nothing to build; use the numbers in the write-up. |
| 10 | Operating roles | **covered** in architecture; two roles lack programs | Roles are programs, not modes (REQUIREMENTS §8; no mode register). Controller, target, transmitter, receiver: rows 1–10 of CAPABILITY (USB LS device included). Monitor and error injection are SHOULD items with named tests MON1/EI1 and no programs (VERIF_PLAN 8, 13). New fact: `uart_rx.s` has no `.pad` at all, so two copies on two engines already are a passive full-duplex UART monitor. | Monitor and EI: see 14 and 13. |
| 11 | Showcase: device personalities | **improve** (host side + small variants) | Split that works: the chip does the wire, the host the device model. I2C: `i2c_tgt` stretches SCL while the host is late (row 6), so EEPROM/RTC/register maps of any size can live on the RP2350; register pointer and auto-increment are host logic today (the target pushes every byte). SPI: the controller owns the clock, so read data must already be in chip memory; `spi_tgt` serves one register byte then a table; a flash-style 0x03 + 24-bit address needs a variant. | High demo value; 2 d for an I2C EEPROM + a sensor register map (host scripts), +1.5 d for a small SPI flash variant; no RTL. |
| 12 | Showcase: sensor profiles | **improve** (host layer on 11) | Host writes new table values between transactions. One word write is atomic; a multi-word reading can tear. A pointer swap (one word) gives a double buffer. Neither target reports the end of a transaction today (`i2c_tgt`'s STOP watcher returns to idle silently; `spi_tgt` pushes nothing), so the variants add an `evt` at STOP / CS rise (+1–2 words) to tell the host when to write. Several I2C addresses on one bus cannot be several engines (one pad owner, D014): it has to be one target with an address mask or table (the address compare is one `cmp R1, R0`; a mask is +1–2 words). | 1 d host generators + 0.5 d multi-address variant; no RTL. |
| 13 | Showcase: fault injection | **improve — top program item** | REQUIREMENTS §5 SHOULD, test EI1, no programs (VERIF_PLAN 13). Feasible as variants: I2C NACK-next-N / extra stretch / ignore address (I2C can wait for the host); UART wrong parity, low stop, break; SPI short/long word and CS glitch (controller variants); stuck SDA by an EXEC'd `p1 <- 0` on a stopped engine; hot-unplug by PAD `en` = 0, which is formally proven (`jspe_pins`: `en` = 0 ⇒ `uio_oe` = 0). Reproducible random faults from a seeded host PRNG; the on-chip LFSR is shared with CRC users. | Unique capability; 2–3 d incl. EI1 at I and R; no RTL. One "fault control word" in memory shared by the variants. |
| 14 | Showcase: monitor | **improve — top program item** | PINS 3.8 defines monitor mode, the no-drive guarantee is formally proven, MON1 is named, programs are missing except UART (see 10). SPI needs two engines (one shifter each: MOSI and MISO on the same SCK/CS); I2C needs a no-drive `i2c_tgt` variant that reports START/STOP/ACK as events. A raw logic analyser is bounded by the host link (3.3): decoded monitoring is the honest claim. | Unique capability; 2 d (I2C + SPI monitors, MON1 at I and R); no RTL. |
| 15 | Showcase: on-chip bridge | **improve — measured feasible (3.4)** | Mailbox word + signal flag G0 bound to a logical pin works today: UART → SPI and I2C → UART run with the host out of the data path; I2C → UART even back-pressures by stretching SCL on chip. | Strong reprogrammability demo; 1–1.5 d to turn the prototypes into programs with tests; no RTL. |
| 16 | Showcase: scenario runner | **improve** | No runner exists (`host/` = `jspe.py` + six examples; no scenario files). A small runner (load image → feed stimulus → collect events → compare → PASS/FAIL) makes 11–15 repeatable on the board and doubles as the hardware bring-up script. | High value per hour; 1.5 d (PC + MicroPython parts); no RTL. |
| 17 | Showcase: pulse / measurement | **covered** (capture exists); programs **improve** | `mov rd, T` is the timer capture (ISA 8.4); measured in 3.3: exact to the input sampling (±1 clk = 20 ns), width and period, 1.3 ms / 21 ms / 335 ms range by prescale. So ROADMAP_V2 accelerator E is not needed. Autobaud is "measure start bit → re-EXEC `uart_rx` with a new BIT" (REQUIREMENTS §7 had it out of scope). PWM/servo/PPM/quadrature are one timer program (E can set two pins in one instant). | 1 d for a measure program (width, period, IR NEC decode on the host) + 0.5 d pulse generator; no RTL. |
| 18 | LIN / Modbus RTU / I2S | **improve** (LIN, Modbus as host layers); I2S **improve, unverified** | LIN and Modbus are UART framing + host logic; `uart_rx` reports a break (0x00 + event 1), `uart_tx` has no break generator (SHOULD extra), so LIN TX needs a small variant or a host-side baud switch. Modbus CRC-16 can run on the host. I2S on one engine is plausible on paper (BCLK by action B, data from a padless `out` pin with SD following, LRCLK one instruction later); not run. Rate: 48 kHz × 2 × 16 bit = 192 KB/s fits link B = 16; 2 × 32 bit = 384 KB/s needs B = 8, i.e. the PIO host link (D090). | LIN/Modbus 1 d; I2S 1.5 d incl. a model; no RTL. |

## 2. Corrections to the notes (each checked)

| Note said | Repo says |
|---|---|
| Memory 128 words; CAN fills it | 512 words since D131; CAN + UART TX + RX = 144 words measured together (CAPABILITY "all"). All ten programs = 429 words. |
| USB LS is future work | Done: usb_ls.s, CAPABILITY row 10, D200–D204 (512-word default build, 142 words). |
| 21 protocol pads = 8 `ui_in` + 5 `uo_out` + 8 `uio` | 7 `ui_in` (H_RX = `ui_in[3]`) + 6 `uo_out` (H_TX, H_RDY = `uo_out[4:5]`) + 8 `uio` = 21 (PINS 2). |
| Load order: REG (SLOTMAP, FILT, PAD) before MEM | CLK, RST, SLOTMAP, MEM + read-back, FILT, PAD `en` = 0, EXEC, PAD `en` = 1, RUN (HOSTIF 7). |
| Faster link needs a chip LINK mode (verify) | LINK = 8 and 4 clk/bit exist on chip (HOSTIF 1); only the RP2350 PIO side is missing (ROADMAP_V2 item 0). |
| Timer capture: verify whether it exists | It does: `mov rd, T` (ISA 8.4), `st T` (D066). Measured in 3.3. |
| Area headroom ~32k µm² placed | Run 10: 457.9k placed = 37.9 % of 1 208 170; the 60 % limit leaves 267.0k placed (≈ 183k synthesized at the measured ×1.458). |
| Two CAN nodes perhaps fit in 512 words | Words fit (2 × 128); the single shared LFSR does not (both nodes compute CRC-15 at once). Same for CAN + USB. |
| Input filter = majority vote, partly | Three equal samples (D033): a pulse shorter than the filter window is removed, it is not a vote. |
| I2S at 48 kHz exceeds the host link | 16-bit stereo fits B = 16; only 32-bit stereo needs B = 8 (PIO link). |
| A monitor needs new RX programs | UART: `uart_rx` drives no pad, so it is a monitor as is. SPI and I2C need variants. |

## 3. Experiments (free: ISS + yosys, no src/ change)

All runs on the golden ISS at 50 MHz unless noted, 4 slot phases where stated. 3.1, 3.3 and 3.4 are reproduced by [sw/tests/test_backlog.py](../../sw/tests/test_backlog.py), which asserts the numbers below (`make swtest`; the three runs > 20 s are marked slow, `make swtest-all`). The 3.2 profile script was not kept.

### 3.1 Software DMA

Programs (one engine, 4 words each): `@hrx pull R1; @hrx pullh R1; st R1, [R0+1]!; jmp` (host FIFO → memory), `ld R1, [R0+1]!; @htx push; @htx pushh; jmp` (memory → host FIFO), `ld S, [R0+1]!; loop { @tick : p0 <- out }; jmp` (memory → pin, 16-bit words, timer-paced).

| Slot map | FIFO → memory | memory → FIFO | memory → pin, cycle-exact (no late edge) |
|---|---|---|---|
| 0xE4 default, 1 slot of 4 | 2.50 words/µs (5.0 MB/s) | 2.50 words/µs | BIT ≥ 12 clk = 4.17 Mbit/s (data still correct at 11) |
| `.slots 0, 1, 0, 2`: 2 slots for the DMA engine, E1/E2 one each, E3 stopped | 5.00 | 5.00 | BIT ≥ 6 = 8.33 Mbit/s (correct at 5) |
| 0x00 solo | 5.56 | 5.00 | BIT ≥ 6 |

Tests: `test_dma_fifo_rates` (±0.03 words/µs, 200-word buffer, data checked), `test_dma_mem_to_pin_min_bit` (exact at the minimum BIT, correct data with a late edge one clk below), `test_dma_no_effect_on_neighbour` (uart_tx pad trace with and without the DMA engine, 4 phases, both maps).

- The fastest host link moves 1.25 MB/s = 0.625 words/µs (B = 4, HOSTIF 8): a software DMA engine is 4× faster than anything that can feed it from the host.
- Effect on other engines' arming bounds: none. Grants depend only on the slot counter and the map (ARCH 2.2); measured: `uart_tx` on E1 or E3 next to a running FIFO → memory loop on E0 gives an identical pad trace in all four phases (and on E1/E2 under the 2-slot map). Giving the DMA engine two slots keeps G = 4, K = 4 (bound 4j + 2) for the engines with one slot, at the price of a fourth engine.
- Derived, not run: a host-pre-encoded Manchester stream (two half-bits per data bit) from memory at BIT 6 is 4.17 Mbit/s TX, 1.8× manchester.s, still 2.4× short of 10BASE-T (2.5 clk half-bit at 50 MHz).

### 3.2 Instruction profile

Engine clocks per wire bit while the engine is active, split by what the engine did in that clock (fire of an instruction class, or "stall" = IR empty or a memory instruction in progress; waiting clocks excluded). CAN and USB TX counted inside the frame only. "Pair" = two consecutive instructions where the second cannot wait; "saved" = clocks from the first fire to the second, i.e. what fusing them would remove.

| Program, rate | clk/bit | busy clk/bit | fires/bit | pin/shift | branch | ALU | stream / timer / mem | stall | top pair (clk saved per bit) |
|---|---|---|---|---|---|---|---|---|---|
| uart_tx 1 Mbaud | 50 | 1.69 | 1.22 | 1.01 | 0.11 | — | 0.10 / 0.01 / — | 0.47 | `jrx`+`pull` 0.19 |
| uart_rx 1 Mbaud | 50 | 1.42 | 1.20 | 0.80 | 0.20 | — | 0.10 / 0.10 / — | 0.22 | `shin`+`push` 0.10 |
| spi_ctrl mode 0 | 8 | 5.62 | 2.92 | 2.15 | 0.24 | 0.12 | 0.26 / 0.15 / — | 2.70 | `push`+`add` 0.50, `add`+`jz` 0.50 |
| spi_tgt mode 0 | 12.5 | 3.08 | 1.39 | 1.06 | 0.11 | 0.03 | — / 0.02 / 0.12 | 1.69 | `ld`+`jmp` 0.86 |
| i2c_tgt 400 kHz | 125 | 3.17 | 1.77 | 1.38 | 0.12 | 0.02 | 0.11 / 0.11 / — | 1.41 | `push`+`tset` 0.46 |
| i2c_ctrl 400 kHz | 125 | 32.35 | 11.90 | 4.10 | 3.49 | 0.56 | 0.44 / 3.31 / — | 20.44 | `call`+`tset` 3.1, `ret`+pin 3.0 |
| ws2812 | 63 | 7.65 | 4.28 | 3.01 | 1.14 | — | 0.12 / 0.01 / — | 3.36 | pin+`jw` 1.0 |
| manchester TX | 22 | 3.00 | 2.29 | 2.02 | 0.13 | — | 0.12 / 0.01 / — | 0.71 | `jrx`+`pull` 0.35 |
| manchester RX | 22 | 2.55 | 1.38 | 1.00 | 0.16 | 0.17 | 0.01 / — / — | 1.17 | `cmp`+`jnz` 0.59 |
| can TX, 8-byte frame | 100 | 20.88 | 6.21 | 1.00 | 2.92 | 2.14 | 0.09 / 0.01 / — | 14.67 | `add #-1`+`jnz` 7.28 |
| can RX, same frame | 100 | 16.27 | 4.58 | 0.99 | 2.30 | 1.15 | 0.10 / — / — | 11.70 | `jnrun`+`add` 4.20, `add #-1`+`jnz` 3.66 |
| usb_ls RX, SETUP + DATA0 | 32 | 7.65 | 3.80 | 1.02 | 2.10 | 0.39 | 0.10 / — / 0.06 | 3.85 | `jw`+`jnrun` 2.30 |
| usb_ls TX, DATA1 8 bytes | 32 | 21.10 | 10.51 | 1.99 | 5.74 | 2.73 | — / — / 0.04 | 10.59 | `jrun`+`add` 2.97, `add #-1`+`jz` 2.00 |

- The micro-loop already fuses the hottest pair of the NRZ programs (pin + pin at 1 clk, no fetch): UART, SPI, Manchester and the targets need 1.4–5.6 busy clocks per bit.
- Stall (slot wait for a fetch after a redirect or an unprefetched word) is the largest busy class wherever busy > 3 clk/bit: the cost is the shared memory port (G = 4), not execution.
- Fused pair that would save most: **decrement-and-branch** (`add Rn, #-1` + `jnz`/`jz`: CAN TX 7.3, CAN RX 3.7, USB TX 2.0 clk/bit), then **branch on run after a shift** (`jnrun`/`jrun`: CAN RX 4.2, USB TX 3.0, USB RX 2.3).
- No shipped program is limited by it: only USB TX is tight (21.1 of 32 clk/bit = 66 %); CAN uses 21 of 100. A 2-slot map for the USB TX engine (3.1) is a free lever with the same effect. Fused instructions: **drop** (decoder RTL next to the fire path, gain only on rows that already pass).

### 3.3 Timer capture with `mov rd, T`

Program (7 words): free-running timer, P = 0xFFFF; `@rise p0 : mov R1, T`, `@fall p0 : mov R2, T`, four `@htx` pushes, `jmp`. Host computes width = R1 − R2 and period from successive R1 (mod 2¹⁶, down-counter).

| Check | Result |
|---|---|
| 20 random pulses, high 5–3 000 clk, low 200–3 000 clk, 4 slot phases, sub-clock edge phases | every pulse captured, OVR 0, no late fire; width error −0.87…+0.81 clk, period error −0.74…+0.89 clk: input quantization only (resolution 1 clk = 20 ns) |
| Prescale 1 (÷16) | error within one step (±16 clk = 320 ns) |
| Input filter on | same errors: both edges see the same added latency |
| Range per prescale (65 536 steps) | 1.31 ms at 20 ns; 21 ms at 320 ns; 335 ms at 5.12 µs; longer by host-side wrap counting |
| Shortest high phase | 1.5 clk (the fall guard is prefetched and armed 1 clk after the rise fires) |
| Shortest low phase while streaming 4 bytes per pulse | 22.5 clk; sustained rate then bounded by the link: 40·B clk per pulse (78 k pulses/s at B = 16, 312 k at B = 4) |
| Timestamps into memory, no host (`@edge : mov R1, T` + `st R1, [R0+1]!`) | exact at ≥ 14 clk per edge (30 edges, 4 phases). `st T` alone would read T in its completion clock, 1–4 clk late depending on the slot |

Tests: `test_capture_width_period` (|error| ≤ 1 step; 20 pulses at prescale 0, 5 at prescale 1 with and without the filter, 4 phases), `test_capture_shortest_phases` (high 1.5 clk and low 22.5 clk pass, low 20.5 fails in some phase), `test_capture_to_memory_min_spacing` (exact at 14 clk, not at 13).

So width, period, duty, tachometer, IR pulse-distance decoding and autobaud are program work today; ROADMAP_V2 accelerator E is not needed.

### 3.4 On-chip bridge

Mechanism, no RTL: the producer stores the byte in a mailbox word and sets G0 (`st S, [Rk]`, `sset #0`); the consumer binds the *input* of one of its logical pins to G0 (BIND source 16; the pin's output stays CS or TXD), makes condition cell 0 = that pin's level, waits `@c0`, then `ld S, [Rk]`, `sclr #0`.

| Bridge | Words (memory) | EXEC init | Result |
|---|---|---|---|
| UART RX (E1) → SPI controller TX (E0, mode 0, one byte per CS) | 23 (producer 9, consumer 14) + 1 mailbox | 11 + 17 | 40 random bytes exact on MOSI in all 4 phases from 1 Mbaud up to **3.57 Mbaud = 357 kB/s** (UART BIT 14) with SPI at 6.25 MHz (BIT 8) or 8.3 MHz (BIT 6). Fails at BIT 13, and at BIT 16 with SPI BIT 20/30: the SPI side needs ≈ 8·BIT + 70 clk per byte (CS low measured 8·BIT + 44 at BIT 6 and 8, 8·BIT + 56 at BIT 20, plus CS inactive) and must be shorter than one UART character; UART cannot be paused, so a real program adds a ring buffer or an overrun `evt` (+2–4 words) |
| I2C target write (E0) → UART TX (E1) | 32 (I2C 24 incl. a 1-word stretch loop `jhi p2, $` on G0, UART 8) + 1 mailbox | 28 + 13 | 12 random bytes, 2 phases, all ACKed and delivered. At UART 1 Mbaud the I2C side limits: **38.8 kB/s** at 400 kHz (incl. START + address). At 115 200 and 28 800 baud the target holds SCL low until the mailbox is empty: on-chip back-pressure, every byte delivered at the UART rate. Reads (R/W = 1) not bridged |

Both run with the host out of the data path; the host only loads the image.

Tests (the two prototype programs are inline in the test; the programs proper are rank 1 in §4): `test_bridge_uart_spi_size` (23 words), `test_bridge_uart_spi_max_rate` (UART BIT 14 and 50 exact in 4 phases at SPI BIT 8 and 6, BIT 13 fails), `test_bridge_spi_cs_low`, `test_bridge_i2c_uart` (32 words; 38.8 ± 0.5 kB/s at UART BIT 50; at BIT 434 and 1736 every byte delivered at the UART pace, i.e. SCL stretched).

### 3.5 Area-only estimates (scratch branch `scratch/area-est`, never merged or pushed; deleted 2026-11-25)

Minimal versions written only to synthesize (`jspe_xshift_a`, `jspe_xshift_ab`, `jspe_xovs`, `jspe_xlfsr2` on local commit e22fb3a), measured on that branch with
`for m in jspe_shift jspe_xshift_a jspe_xshift_ab jspe_xovs jspe_lfsr jspe_xlfsr2; do make -s area B=$m LIB=<sg13g2_stdcell_typ_1p20V_25C.lib>; done`
(yosys, sg13g2 typ lib). Totals, cells / µm²: jspe_shift 453 / 6 617.0, jspe_xshift_a 430 / 6 635.1, jspe_xshift_ab 452 / 7 028.8, jspe_xovs 53 / 1 415.2, jspe_lfsr 366 / 6 309.8, jspe_xlfsr2 514 / 9 002.4 (rerun before deletion, same as the first run). The branch is gone; the numbers below are the record. Placed = synthesized × **1.458**, the factor measured on run 10 ((457.9k − 45.3k macro) / 283.0k synthesized). Headroom at 37.9 %: **267.0k µm² placed** to the 60 % limit.

| Item | What was synthesized | Δ synthesized | Placed | Core % |
|---|---|---|---|---|
| Oversampling capture register | per engine: 16-bit shift register of the engine's input pin every clock (or timer step), enable, rcfg read leg (1 415 µm²) ×4 | 5 661 | 8.3k | +0.7 |
| Timer-clocked shift (minimal A) | `jspe_shift` with tick → `out`-shift and tock → `shin`, 2 SHCFG bits; output via the existing follow pins, words via the existing auto-transfer | +18 per engine (ABC noise; by cell count ≈ 0.15k) ×4 | ≤ 0.9k | < 0.1 |
| + Manchester / biphase-mark TX | the above + half-bit phase and biphase flops, 2 mode bits, line-code value for the follow pin | +412 per engine ×4, + ~0.1k epins mux | 2.5k | +0.2 |
| Second LFSR | `jspe_lfsr` instance (6 310) + per-engine select bit + rcfg read legs | 9 002 | 13.1k | +1.1 |
| All four | | ≈ 16.4k | ≈ 24k | 37.9 → 39.9 |

- Area no longer constrains any of them (ROADMAP_V2's 27k/11k/10k placed were per-cell guesses; the minimal versions are smaller, the second LFSR is close).
- The real costs: D124 freeze, the D127 chain (isa.yaml → gen_isa → ISS → RTL → formal → lockstep → bound sweep → GDS → GLS) and timing: tick/tock would become shift enables feeding S, the FIFO strobes and the shared LFSR step (a cross-engine path), next to the fire path D081 cut.
- Minimal A + B does not make 10BASE-T possible: RX still needs memory transfer at line rate and a 40/60 MHz clock (ROADMAP_V2 §3; out of scope, D125).

## 4. Ranked list (program/host items merged with the open ROADMAP_V2 items)

Scores 1–3: **R** = reprogrammability (a protocol or use not designed in, run on the frozen RTL), **U** = unique capability of an emulator (concurrency, monitor, fault injection, cycle-exact timing), **V** = verification or bring-up evidence. Rank by score per day, ties to the item with a named test. Days = one person, incl. tests at I (and R where the item has an RTL lockstep test).

| Rank | Item | R | U | V | Days | RTL | Basis |
|---|---|---|---|---|---|---|---|
| 1 | On-chip bridges UART → SPI and I2C → UART as programs + tests (ring buffer or overrun event on the UART side) | 3 | 3 | 2 | 1.5 | no | 3.4: both prototypes run; host out of the data path; I2C back-pressure on chip |
| 2 | Fault-injection variants + one fault control word; EI1 at I and R | 2 | 3 | 3 | 2.5 | no | §1 row 13; REQUIREMENTS §5 SHOULD |
| 3 | Monitor programs (I2C no-drive target with START/STOP/ACK events, SPI on two engines; UART = `uart_rx` as is); MON1 at I and R | 2 | 3 | 3 | 2 | no | §1 rows 10, 14; PINS 3.8; no-drive proven |
| 4 | Measurement program: width, period, duty, IR NEC/RC5 decode, autobaud on `uart_rx` | 3 | 2 | 1 | 1 | no | 3.3: ±1 clk today |
| 5 | Scenario runner (PASS/FAIL) + cold-start sequence with a per-demo RP2350 GPIO plan | 1 | 2 | 3 | 2 | no | §1 rows 1, 16; needed for board bring-up anyway |
| 6 | 10BASE-T link pulses (ROADMAP_V2, due 2026-12-08) | 3 | 1 | 1 | 1 | no | ROADMAP_V2 §2; tiny program + Pmod; "link up" on a PC |
| 7 | PIO host link at B = 8/4 (ROADMAP_V2 item 0, due 2026-12-08) | 1 | 1 | 2 | 2 | no | chip side exists (HOSTIF 1); needs the board to verify; unlocks 32-bit I2S, larger RDMEM chunks (D088), faster capture streaming |
| 8 | Resident set / hot swap / pad move as SDK calls + conflict check (pads, engines, words, LFSR) | 3 | 2 | 1 | 1.5 | no | §1 row 6; all ten programs = 429 words |
| 9 | Device personalities: I2C EEPROM (host-backed), sensor register map with live values, multi-address target | 2 | 2 | 1 | 2.5 | no | §1 rows 11, 12 |
| 10 | Image manifest + parameter table for numeric options | 2 | 1 | 1 | 2 | no | §1 row 5 |
| 11 | LIN (break variant) / Modbus RTU host layers | 2 | 1 | 1 | 1 | no | §1 row 18 |
| 12 | I2S TX on one engine (paper-feasible) | 2 | 1 | 1 | 1.5 | no | §1 row 18 |
| 13 | Small SPI-flash personality (`spi_tgt` variant, 0x9F/0x05/0x03 + 24-bit address) | 2 | 1 | 1 | 1.5 | no | §1 row 11 |
| 14 | CAPABILITY "candidates" table + write-up corrections of §2 | 1 | 1 | 2 | 0.5 | no | §1 row 4, §2 |
| 15 | Second LFSR | 2 | 1 | 1 | 4–6 | **yes** | 3.5: 13.1k placed; lifts can.s's 625 kbit/s CRC-window limit (D105), allows CAN + USB or two CAN nodes at once |
| 16 | Oversampling capture register | 1 | 1 | 1 | 4–6 | **yes** | 3.5: 8.3k placed; only raw capture and glitch statistics gain |
| 17 | Minimal timer-clocked shift + Manchester/biphase TX | 1 | 1 | 1 | 6–8 | **yes** | 3.5: ≤ 3.4k placed; no shipped row needs it; 10BASE-T still out (D125) |
| — | Dropped: fused decrement-and-branch (3.2), hardware DMA (3.1), multi-lane shift, SCK-domain CDC, SJW resync, hardware CDR | | | | | | §1 rows 2, 3, 7, 9; 3.2 |

Ranks 1–8 add up to 13.5 days, ranks 1–14 to 22.5 days.

## 5. Recommendation

**(i) Program and host only.** Keep rtl-freeze-v1 + D109 as the shipped src/; plan nothing that needs an RTL change.

Why:
- Every measured gap closes in software: capture exists (3.3), a software DMA outruns the host link (3.1), bridges run on chip (3.4), the profile shows no shipped row limited by a fusable pair (3.2).
- Area is no longer the reason to wait (24k placed for all four candidates, 3.5), but each src/ change still costs the full D127 chain (formal on the touched blocks, lockstep, T6 bound sweep, GDS, GLS 45/45) plus new timing paths next to the D081 cut. That is about a week per feature, taken from ranks 1–9, which score higher on the primary axis (protocols and uses not designed in).
- The board bring-up (host firmware, cold start, GPIO plan) has not started and may surface board-side issues that need those days.

**(ii) as a pre-written option only: + the second LFSR**, the one RTL item with a protocol-level gain (CAN above 625 kbit/s, CAN + USB together, two CAN nodes). It is a self-contained block off the fire path. Leave out the oversampling register and minimal A + B: no shipped or planned row needs them.

| Date | Gate |
|---|---|
| 2026-11-26 … 12-08 | ranks 1–4 and 6 (bridges, fault injection + EI1, monitors + MON1, measurement, link pulses); PIO link (rank 7) as far as possible without the board |
| **2026-12-10 go/no-go** for (ii) | **go** only if all of: ranks 1–4 and 6 merged and green (`make swtest`, `make test`); no open board-side issue; a ≤ 1-page design note (isa.yaml encoding, per-engine select, formal plan) approved; a fresh area/timing estimate. Otherwise **no-go**, and the default is no-go |
| 2026-12-10 … 12-20 | no-go: ranks 5, 8, 9, then 10–14. go: second LFSR on a branch through the D127 chain, ranks 5 and 8 in parallel |
| **2026-12-20** | last src/ change (D124); after it only docs, host, programs |
| 2027-01-05 / 01-10 / 01-15 | final GDS + GLS, docs, submit (ROADMAP_V2 §6) |
