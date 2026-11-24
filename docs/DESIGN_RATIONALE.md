# Design rationale

The decisions that shaped the chip, what I rejected, and why. Every decision has a line in [docs/DECISIONS.md](https://github.com/NotCleo/JS-PE/blob/main/docs/DECISIONS.md) (format: decision | alternatives rejected | why | cost | protocols); the D-numbers below point there. The candidate architectures and their scoring are in [docs/history/ARCH_CANDIDATES.md](https://github.com/NotCleo/JS-PE/blob/main/docs/history/ARCH_CANDIDATES.md).

## 1. Summary

| # | Decision | Rejected | D-IDs |
|---|---|---|---|
| 1 | Four reactive engines; every instruction is `guard : operation`, held until the guard fires | A "LANES" (hardware bit lanes + field sequencer), C "CONDUCTOR" (one event-driven CPU + timed pin channels), an eFPGA fabric | D009, D017 |
| 2 | One memory port, strict time division (4 slots), 1-word prefetch with jump folding; slot map set at load time | fixed priority between engines; depth-2 prefetch; run-time slot donation | D018, D025, D027 |
| 3 | Guard events are latched; a late instruction answers late, a second event sets OVR | instantaneous (unlatched) events | D019 |
| 4 | Micro-loop as 2 bits inside the pin/shift instruction, ended by the shifter's word boundary | a `loop n` header word; a separate iteration counter | D024 |
| 5 | Follow pin + word-boundary auto-transfer instead of a shift-on-edge unit | an autonomous shifter armed by one instruction | D020, D026 |
| 6 | Bit-level helpers are generic taps (parity, run length, differential, shared LFSR), no line-code engines | fixed NRZI / Manchester / stuffing / CRC pipelines | D013, D029 |
| 7 | One owner per output pad, host-only drive enable | engine-side output binding with OR/priority resolution; fixed pin roles | D014, D033 |
| 8 | 3-pin UART-style host link, clock-ratio-locked, push model with escape-multiplexed streams | SPI host link (pull model); host on `uio`; more pins | D001, D011, D031 |
| 9 | Configuration and start go through the ISA (`wcfg`, host-injected `EXEC`); no init code in memory | a host configuration bus with read-back muxes | D030 |
| 10 | Static arming bound in the assembler; waits credited only by a stated `.assume` | trusting simulation alone; crediting prefetch implicitly | D054, D100 |
| 11 | 16-bit data path | 8-bit data path with a 16-bit timer | D022 |
| 12 | Program memory in flops (128 x 16) after the SRAM macro failed LVS | further macro attempts before the deadline | D003, D010, D062, D063 |
| 13 | Pads load the engine's registered pin state (+1 clk latency) to close timing | keeping the combinational pin-value → pad path | D081 |

## 2. The decisions

### 2.1 Reactive engines (D009, D017)

The judging axis is protocols that were not designed in. LANES would have been fast for the protocols whose bit lanes I built and useless for the rest; CONDUCTOR puts every bit through one CPU and runs out of reaction time once two protocols run. An eFPGA fabric did not fit the area. Cleo makes the *wait* the instruction: an engine sits on one armed instruction and acts in the clock its event arrives, so the reaction is 3–4 clk + pad (ARCH 4.1) no matter what the protocol is. Everything protocol-specific is a program, which is what let WS2812, Manchester and CAN run later with no RTL change.

### 2.2 Time-division memory (D018, D025, D027)

D010 first proposed fixed priority E0 > E1 > E2 > E3. That makes E3's fetch time depend on what the others do, and no static timing claim survives that. Strict time division makes every engine's fetch latency a function of the slot counter and a load-time map only: 2–5 clk with the default map, 2 clk solo. One prefetch word plus jump folding covers every MUST path; a second prefetch word (≈ 45 cells per engine) would only have helped SPI target multi-byte reads at 4 MHz in the default map, which the solo map already covers (ARCH 13).

### 2.3 Latched events (D019)

With instantaneous events, an edge that arrives while the engine is fetching is simply lost and the protocol breaks silently. Latching turns that into "answered late", and the doubled flag makes a truly missed event visible as OVR. The cost is the stale-event hazard: a guard armed after an event fires on it at once. That bit the SPI target in Phase 4 (26 of 84 cases, D055) and is now a documented idiom (ISA 4).

### 2.4 Micro-loop in the instruction (D024)

A loop header word costs an un-prefetched fetch on every turnaround; an iteration counter is a second counter next to the shifter's bit counter that already knows where the word ends. Two loop bits in the E instruction (`rep`, `head`, `tail`) cost no fetch on entry and exit on the word boundary. A whole SPI byte is one held pair.

### 2.5 Follow pin and auto-transfer (D020, D026)

Phase 2b looked at a separate shift-on-edge unit to push the SPI target past 4 MHz. It was ≈ 60–75 cells per engine, a second mechanism for what the micro-loop already does, and S would change under a running instruction. The follow pin state (a pin tracks the shifter's out tap every clock) and auto-push/pull at the word boundary cost ≈ 35 cells per engine and serve any shifted stream, not only SPI (ARCH 13).

### 2.6 Generic taps, no line-code engines (D013, D029)

Fixed NRZI, Manchester, stuffing and CRC pipelines are what a protocol chip normally has, and each one is a protocol designed in. Taps on the shifted bit (parity, run length with limit, differential, an alternate drive source `q`, a step of the shared 32-bit LFSR) cost one branch per bit instead of a hardware mode. CAN's bit stuffing (run-length 5 + `q`) and CRC-15 (shared LFSR) are built from these.

### 2.7 One owner per pad (D014, D033)

Engine-side output binding with OR or priority resolution allows two engines to drive one pad. Making the pad pick its owner — and making the drive enable writable only by the host — rules out contention by construction and gives a monitor mode for free: a program loaded with every `en` = 0 cannot drive anything (ARCH 2.1; proven at top level, a_safe). Signal flags reuse the same bind sources, so engines coordinate through the pin machinery (D033).

### 2.8 3-pin host link (D001, D011, D031)

All eight `uio` pins are the only bidirectional/open-drain group, so the host stays on `ui_in`/`uo_out` (D001). An SPI host link is a pull model: the chip would need hundreds of bytes of FIFO or continuous polling. An asynchronous link at a fixed ratio to `clk` (16, 8 or 4 clk per bit) with a CTS-style ready line lets the chip push stream data and events when it has them, using a hardware UART on the RP2350. 1 Mbaud full-duplex UART moves 32 % of the slowest link setting (HOSTIF 8).

### 2.9 Configuration through the ISA (D030)

A host configuration bus would need a second access path into every engine register plus read-back muxes. Instead the host injects instructions into a stopped engine (`EXEC`), and initialisation is emitted by the assembler as injected instructions rather than stored code — which matters on a 128-word memory.

### 2.10 Static arming bound (D054, D100)

Every program claim is of the form "the next guard is armed before the next event". The assembler computes that bound per path from the slot map and turns `.deadline` into an assembly error. D054 fixed the formula for non-uniform maps after the ISS caught a 13-clk path; D100 made it sound by crediting a prefetch only when the program states the wait (`.assume armed m`), which the ISS then checks in contract (T6). This tightened several published limits (uart_rx 1-clk stop→start, Manchester RX BIT ≥ 18).

### 2.11 16-bit data path (D022)

An 8-bit path saves ≈ 290 synthesized cells per engine (7 % of the total) but costs two registers for every timer constant and 9-bit address, no 9-bit UART or I2C word and no 16-bit SPI word in one shift, and grows the I2C controller from 41 to ≈ 50 words. After D018 the execute path is ≈ 8 ns either way (ARCH 13).

### 2.12 Flop memory (D003, D010, D062, D063)

The plan was one 512 x 16 IHP SRAM macro (45.3k µm²) with a 128 x 16 flop fallback. GDS run 3 with the macro was clean on DRC and timing but failed LVS on supply names inside the macro (`VDD!` vs `VDD`, D062). I had set that run as the last macro attempt, so the flop build became the product: 162k µm² synthesized, 36 % of the logic, and the reason CAN fills the whole memory. The macro is the first future-work item.

### 2.13 Registered pin state to the pads (D081)

GDS run 44184771880 failed setup in the slow corner by 1.17 ns, all on one path: pin level → guard → fire → shift → out tap → pin value → owner mux → pad register, with about 7 ns in three long, weakly driven nets. Feeding the owner mux from the engine's registered pin state cut the path in two and added one clock of pad latency to every program (headers updated after D081). Timing then closed with 1.56 ns slack (run 5) and 1.183 ns in the sign-off run 8.

## 3. Smaller decisions worth knowing

| Topic | Decision | D-IDs |
|---|---|---|
| ISA trim | `stgl`, `sbc`, `swap`, `not`, `wcfg SIG` removed after the Phase 3 review; 67 instructions | D048 |
| Area rule | placed cell area ≤ 60 % of the *core* (724.9k µm²), not the nominal die | D051, D063, D083 |
| Area freeze | from Phase 7 RTL changes are bug fixes only; one since the freeze (D109) | D083, D109 |
| Reload while running | short `RDMEM` read-back chunks so running streams wait < 2 000 clk | D088 |
| Clock check in the image | `.bin` CLK record; the loader refuses a wrong `clk` | D089 |
| Formal method | wrappers around unmodified RTL, assume-guarantee: link proven against a free host, so the sequencer's host obligations need no host rule | D106, D107, D114, D116 |
| Flow tuning | AREA synthesis and fanout limits tried and reverted after failing slow-corner setup | D119, D120, D121 |
| Gate-level sign-off | all 45 tests on the netlist, locally, with the PDK flop models | D122, D123 |
