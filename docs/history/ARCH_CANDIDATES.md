# ARCH_CANDIDATES — Phase 2a (2026-11-14)

Three candidate architectures.
None is modelled on RP2040 PIO or TI PRU. All numbers are pre-synthesis estimates (cells ±30 %) unless marked *measured*.
Two figures in §§0–8 are superseded by §9: the flop fallback is ≈ 7.7K cells (not 4.8K) and the host link is capped at 1.0K cells (not 0.6K).
**Approved 2026-11-15: Candidate B (Cleo) with the changes in §10** (time-division fetch with prefetch, latched events). §2, §5, §7 and §8 were edited to match; where an older section and §10 differ, §10 wins.

## 0. Common ground

### 0.1 Budget and technology numbers
- 32 tiles ≈ 32K cells nominal; design target ≤ 22K including memory. Logic target ≤ 16K placed cells, so the 256 B flop fallback (≈ 4.8K cells) still fits.
- Placed cells = synthesized cells × 1.25 (CTS, hold and repair buffers; assumption).
- CMOS5L cell areas (*measured*, PDK lib): DFF `dfrbpq_1` 49.0 µm², `mux2_1` 18.1, `mux4_1` 38.1, `nand2_1` 7.3. There is no enable-flop, so every load-enabled bit costs DFF + mux2.
- Slow corner (1.08 V, 125 °C, *measured* from lib tables at moderate load): nand2 0.2–0.3 ns, mux4 0.45 ns, xor2 0.45 ns, DFF clk→Q 0.45 ns, setup 0.2 ns. Paths below assume 0.3 ns per logic level including wire.

### 0.2 Memory options (*measured* from IHP-Open-PDK LEF/lib; CMOS5L uses the SG13G2 SRAM library)

| Memory | Capacity | Area µm² | Tiles | clk→DOUT slow |
|---|---|---|---|---|
| 1P 256x16 | 512 B | 28.1k | 0.9 | — |
| **1P 512x16** | **1 KB** | **45.3k** | **1.45** | **6.3 ns** (3.7 typ) |
| 1P 1024x8 | 1 KB | 49.4k | 1.6 | — |
| 1P 256x32 | 1 KB | 49.5k | 1.6 | 5.1 ns |
| 1P 1024x16 | 2 KB | 79.7k | 2.5 | — |
| 2P 512x16 (dual-port) | 1 KB | 88.5k | 2.8 | 6.4 ns |
| Flop fallback 128x16 | 256 B | ≈ 165k (≈ 4.8K cells) | 5.3 | ≈ 2 ns (mux tree) |

- All macros are synchronous: address/enables are sampled on the clock edge (address setup ≤ 0 ns, hold ≤ 0.85 ns; MEN/WEN setup ≤ 0.85 ns), data appears tacc later. One-cycle read latency.
- A fetch → execute → next-address loop through the 512x16 macro therefore has 20 − 6.3 − 0.85 ≈ 12.8 ns of logic at 50 MHz, 9.5 ns at 60 MHz.
- Fallback = 2048 × (DFF + mux2) + 16 × 128:1 read mux, with a registered read address so it has the same one-cycle interface as the macro. It is 3.6× the macro's area for a quarter of the bits.
- D003's "≈ 80k µm²" is the 2 KB 1024x16; the 1 KB 512x16 is 45k µm². Doubling memory later costs +34k µm² (≈ 1.1 tiles).

### 0.3 Pin plane and the latency floor (identical for every candidate)
- Inputs: 16 pins (ui 8 + uio 8) → 2-FF synchronizer → previous-value flop → rise/fall pulses. Optional 3-sample majority filter per pin (I2C tSP, UART noise).
- Outputs: uo 8 push-pull, uio 8 with OE; `uo_out`, `uio_out`, `uio_oe` are pad registers.
- **Reaction floor:** external edge → pad output change = 2–3 clk + ≈ 10 ns pad/mux = 50–70 ns at 50 MHz (42–60 ns at 60 MHz), and only if the response is already armed in hardware in the cycle the edge becomes visible. Every software step adds ≥ 1 clk.
- Externally clocked target with a half-period budget: theoretical max ≈ 6 MHz; **claimable max 4 MHz** (125 ns half period, ≥ 45 ns margin). This bound is architecture-independent.
- Controller read-back: the partner's reply to an edge we launch is visible ≥ 3 clk later, so the sample point must be programmable relative to the launch edge.

### 0.4 Host link: H1 (async, ratio-locked) vs H2 (SPI)

| | H1: 2-pin async + ready | H2: SPI, host = controller |
|---|---|---|
| Pins | H_RX (ui), H_TX (uo), H_RDY (uo) = 3 | H_SCK, H_MOSI (ui), H_MISO (uo) = 3; + H_CS = 4 for robust framing |
| Bit timing | bit = 16 clk at reset (3.125 Mbaud at 50 MHz), switchable to 8 or 4 clk. RP2350 makes `clk`, so both ends are frequency-locked: zero baud error, re-phase on each start bit | SCK ≤ clk/4 through the synchronizer; H_MISO obeys the 50–70 ns floor → hardware `machine.SPI` ≤ ≈ 5 MHz, faster needs PIO with delayed sampling |
| Bandwidth per direction | 312 / 625 / 1250 KB/s, both directions at once | ≈ 625 KB/s (hardware SPI), ≤ 1.5 MB/s (PIO) |
| Chip → host | **push**: sent the moment an engine produces it; host UART driver buffers in C (IRQ/DMA), immune to MicroPython GC pauses | **pull**: nothing moves unless the host clocks. Python gaps of 0.1–10 ms at 100 KB/s need 10–1000 B of on-chip FIFO (≈ 20 cells/byte) or a continuously polling PIO+DMA host |
| Host → chip back-pressure | H_RDY → host UART CTS (or PIO `wait pin`): stops within 1 byte at hardware speed | status byte per transaction, Python in the loop |
| On-chip FIFO depth needed | 2–4 bytes per stream | tens to hundreds of bytes |
| Cells (link + FIFOs) | ≈ 0.6K + 0.8K | ≈ 0.5K + 2–4K |

- Stream multiplexing (H1): raw data bytes for the current stream; an escape byte + id switches stream (engine 0–3 data, events, register/program access). Single-stream efficiency ≈ 100 %.
- Program load: write-memory command (address, count, words) into the program memory; 1 KB at 312 KB/s ≈ 4 ms. Reload possible at any time (engines halted by command).
- Status/IRQ: no IRQ pin. Errors and engine events are pushed in-band within ≈ 2 byte times (≈ 6 µs). Debug commands: halt/run/step per engine, read PC and registers.
- UART 1 Mbaud full duplex = 100 KB/s each way: H1 at the slowest setting has 3× headroom with 4-byte FIFOs.
- **Pick: H1, 3 pins**, for all three candidates. Host side: the RP2350 hardware UART with CTS if the board's GPIO map puts UART functions on the chosen pins, otherwise a PIO UART with `wait pin` (works on any GPIO). H2 is rejected because of the pull model.

### 0.5 What the protocols demand (used to judge the candidates)
- R1 Hardware-armed response to an edge (target roles), at the 0.3 floor.
- R2 Several conditions watched at once with different continuations (I2C START/STOP inside a bit loop, SPI CS abort, timeouts).
- R3 Cycle-exact timer-paced outputs (controller, WS2812, Manchester TX).
- R4 ≥ 2 concurrent contexts (UART full duplex is MUST), 3–4 wanted.
- R5 On-chip decisions: the host answers in milliseconds, so address match, ACK/NACK, CRC check, register lookup must happen on chip (compare, count, table read).
- R6 Streams to and from the host with back-pressure (I2C stretch while no data).
- R7 Clock recovery from edges (UART start bit, Manchester, CAN, USB).
- R8 Any single v1 protocol in ≤ 128 words (fallback memory).

---

## 1. Candidate A — LANES: configurable bit-stream lanes + field sequencer

Lineage: a programmable serializer/framer. Hardware moves bits; software moves fields.

**Execution model.** 3 lanes. Each lane = one bit pipeline + one small field sequencer (own PC). Instructions are per field: `TX n`, `RX n`, `XFER n` (1–32 bits), `IDLE n`, `SYNC event`, `EXPECT`, load/push/pull, compare/branch, `CFG`. A field instruction blocks until the lane has moved n bits or an abort event fires. Timing is not in the instructions: it is the lane's timebase configuration.

**Bit plane (per lane, all set by `CFG`):**
- Timebase: 16.8 fractional divider (NCO) giving bit strobe and sample strobe at a programmable phase; hard-sync and SJW-limited resync on a chosen edge; or strobes taken from pin edges (target roles).
- 32-bit TX and RX shifters, MSB/LSB first, bit counter.
- Symbol coder: each bit expands to 1–4 sub-slots from two pattern registers, plus a differential flag: NRZ, RZ, Manchester (both polarities), NRZI, WS2812-style pulse codes. Decoder: level at sample phase, transition direction, differential, pulse-width threshold.
- Stuffer/unstuffer (run length 1–7, ones-only or either polarity), parity, CRC with programmable polynomial (16-bit per lane, one shared 32-bit).
- Drive-and-compare (arbitration) flag; two edge+qualifier event detectors (START/STOP, CS abort).

**Pin model.** Each lane has 5 logical signals (DO, DI, CK, AUX0, AUX1). Every physical output has one owner (lane, signal) and a mode (push-pull, open-drain, tri-state under field control). Inputs can be read by any number of lanes. Shared sync and edge detect (0.3).

**Host interface.** H1. FIFOs carry up to 16-bit fields, so each stream needs wider entries (≈ 0.7K cells for 3 lanes).

**Memory.** 16-bit words. Programs are 15–60 words because there is no per-bit code; a 1P 256x16 (28k µm²) would do; fallback 128 words holds any v1 program.

**Cells (synthesized).**

| Block | Cells | Assumption |
|---|---|---|
| Lane: timebase + resync | 280 | 64 flops, 24-bit add, 16-bit compare |
| Lane: shifters + counter | 290 | 2 × 32 flops, 4-way muxes |
| Lane: coder + decoder | 260 | pattern regs, slot counter, pulse-width counter |
| Lane: stuff, parity, CRC-16 | 190 | programmable polynomial |
| Lane: events + arbitration | 90 | 2 detectors |
| Lane: config registers | 150 | ≈ 60 bits |
| Lane: field sequencer | 420 | PC, IR, 2 × 16-bit regs, compare, decode |
| Lane: pin mux share | 100 | |
| **Lane total × 3** | **1.78K × 3 = 5.3K** | |
| Host link, FIFOs, pin plane, memory glue, CRC-32, misc | 2.5K | |
| **Total** | **7.8K synth ≈ 9.8K placed** | + macro, or + 4.8K fallback = 14.6K (budget 22K) |

**Critical path.** NCO add (24 bit) → strobe → shifter/coder → pad register ≈ 7 ns. Sequencer fetch loop through the SRAM ≈ 10.7 ns. 50 MHz comfortable, 60 MHz plausible.

**Stress cases.**
- I2C target + stretching: SCL edges are the strobes; START/STOP detectors abort the field at any bit; ACK is a 1-bit TX field; stretching must be a designed-in lane feature ("hold CK low while the sequencer is between fields"). Feasible. Controller arbitration: via the drive-compare flag. Feasible because it is designed in.
- SPI target: bit path is hardware, so max SCLK = the floor, 4 MHz. Register emulation: 3–4 clk from last sample to loaded TX shifter, inside the 6 clk available at 4 MHz.
- UART RX: hard-sync on the start edge, sample at mid-bit phase, 1-clk resolution (≥ 16× up to 3.1 Mbaud). Full duplex = 2 lanes. Feasible.
- CAN: stuffing, CRC-15, arbitration compare and SJW resync are all pipeline features. Feasible, and only because each one was anticipated.
- USB LS: NRZI + stuffing + CRC-5/16 at 33.33 clk/bit via the NCO with resync on every edge. Needs two-pin symbols and SE0 detect/generate designed in. Feasible at packet level.
- 10 Mbit Manchester: TX feasible at 60 MHz (3-clk sub-slots); not at 50 MHz (2.5 clk). RX at 6 samples/bit: marginal.

**Roles that are pure software.** None completely. Controller vs target is a timebase-source setting, monitor is an RX-only lane mode, tester compare needs `EXPECT` hardware. Error injection only where a hook exists (parity invert, CRC corrupt, bit-flip mask).

**Top 3 risks.**
1. Generality cliff: any bit-level behaviour the pipeline did not anticipate falls back to 1-bit fields at a few instructions per bit. This is the primary judging axis, and the post-freeze demos could land exactly there.
2. Configuration-space verification: timebase × sync × coder × stuffer × CRC × pin mode × abort timing. The ISS becomes a second RTL.
3. Ordering semantics inside the pipeline (stuff vs CRC vs coder vs resync vs abort) are subtle to specify and hard to change after freeze.

---

## 2. Candidate B — Cleo: reactive engines, every instruction guarded

Lineage: synchronous reactive languages (await / abort-when / parallel threads), applied to pins. The unit of programming is "when this event happens, do this, in that same clock cycle".

**Execution model.**
- 4 identical engines (E0–E3), one shared program memory, one clock tick = one synchronous instant for all engines.
- Every 16-bit instruction is `guard : operation`. The engine holds it in its instruction register until the guard is true, executes the operation in that same cycle, then takes the next. Guard `always` gives ordinary straight-line code at the memory-slot rate, one instruction per 4 clk.
- Granularity: per event (an edge or a timer tick), 1–3 instructions per bit; data movement per word.
- Because the waiting instruction is already in the engine, the operation is the hardware-armed response of R1: no fetch between event and action.
- **Watchers** (2 per engine): a programmable condition that preempts whatever the engine is doing and jumps to a vector (abort), or calls it and returns (resume). The condition is a LUT2 over 16 engine-local signals (4 pin levels, 4 rises, 4 falls, tick, tock, drive-mismatch, user flag), 12 configuration bits. This is R2: START/STOP at any bit, CS abort, SE0, arbitration loss, timeout.
- **Micro-loop**: `loop n { 1 or 2 instructions }` keeps the pair in the engine and repeats it n times with no fetch. While it runs, the engine is a hardware bit pump at the rate of its guard events.
- Timing: per-engine 16-bit down-timer with period reload. Two events per period: `tick` (zero) and `tock` (programmable compare, e.g. the sample point). One-shot use via `tset`. 8-bit fractional period (3 Mbaud, USB 33.33 clk/bit), prescale ÷1/16/256 (up to 335 ms), optional hard-sync rule "restart on chosen edge" for clock recovery (R7). No per-engine clock dividers and no cycle counting by the programmer.
- Memory port: time-division (D018; fixed priority E0 > E3 until 2026-11-15). A free-running slot counter gives engine Ei the port in every fourth clock, used or not, so no engine can delay another. Each engine keeps one prefetched successor beside its instruction register: after a wait of ≥ 4 clk the next instruction is armed in the clock after the guard fires. Otherwise the delay is fixed: 2–5 clk from request to armed (≤ 3 clk slot wait + 2 clk registered read), the same for E0–E3. An action fused to a guard is cycle-exact once its instruction is armed. Rules and the static bound: §10.2.
- Events (D019): pin edges and timer events that arrive while the engine is fetching or executing are latched in per-engine sticky flags and consumed by the next guard that names them, so a late instruction answers late instead of missing the edge. A guard that consumes a doubled event raises the engine's overrun flag. Rules: §10.3.
- Inter-engine: 8 signal flags usable as guards and conditions (handshakes, half-duplex turn-around, RTS).

Illustrative only (the ISA is Phase 2b):
```
; UART RX 8N1. p0 = RX, period = bit time, tock = mid-bit
rx:    @fall p0 : tsync                ; start edge restarts the bit timer
       @tock    : jpin p0, rx          ; not low any more -> glitch
       loop 8 { @tock : shin p0 }      ; held instruction, no fetch
       @tock    : jnpin p0, ferr       ; stop bit must be high
       push S ; jmp rx

; I2C target front end. p0 = SCL, p1 = SDA (open-drain)
       watch 0, fall(p1) & p0 -> start ; START at any bit position
       watch 1, rise(p1) & p0 -> idle  ; STOP at any bit position
start: loop 8 { @rise p0 : shin p1 }
       cmp S, #ADDR ; jne idle
       @fall p0 : p1 <- 0              ; ACK, 2-3 clk after SCL falls
       @fall p0 : p1 <- z, p0 <- 0     ; release SDA, stretch SCL
       @hrx     : pull S ; p0 <- z     ; continue when the host has data

; SPI mode 0 controller byte at clk/8. p0 = SCLK, p1 = MOSI, p2 = MISO
       loop 8 { @tick : p0 <- 0, p1 <- S.out ; @tick : p0 <- 1, shin p2 }
```

**Bit plane.** No line-code, stuffing or framing engines. Instead, taps on the engine's shift stream, enabled by instruction:
- parity accumulator; run-length counter with programmable limit (stuffing becomes one branch, `jrun`); differential in/out (NRZI = "toggle if zero", "xor with previous sample");
- one shared 32-bit LFSR with programmable polynomial and direction, stepped by the tapped bit (CRC-5/7/8/15/16/32);
- pin operation with two pin actions (0, 1, toggle, data, ~data, release, toggle-if-data) plus a shift, so Manchester, WS2812 and differential pairs are instruction sequences;
- timer capture into a register (edge timestamps: IR receive, autobaud, logic-analyser monitor).
- Optional extension, not needed for v1: word-boundary auto-transfer (push / store to memory when the bit counter wraps) so a micro-loop never has to exit.

**Pin model.**
- Each engine sees 4 logical pins p0–p3, bound to any of the 16 synchronized inputs; programs are pin-relocatable.
- Each physical output has exactly one owner (engine, logical pin) and a mode (push-pull, open-drain, inverted); two engines can never drive one pin. Any number of engines may read a pin.
- Host-only drive-enable mask: a pin outside the mask cannot be driven by any program. This is the hardware guarantee behind monitor mode.
- Open-drain: value 0 → `uio_oe` = 1 with `uio_out` = 0; value 1 → released.

**Host interface.** H1. `pull`/`push` are ordinary operations with guards `hrx` (byte available) and `htx` (space); 4-byte FIFO per engine per direction.

**Memory.** 1P 512x16: program and tables/buffers share it; a table read takes the engine's next memory slot (2–5 clk). Estimated program sizes: UART TX 7, UART RX 9, SPI controller 14, SPI target with register table 24, I2C controller 45, I2C target 45, WS2812 10, Manchester TX+RX 25, CAN 2.0A ≈ 150–200 words. Fallback 128 words holds any single v1 protocol and UART full duplex; CAN needs the macro.

**Cells (synthesized).**

| Block | Cells | Assumption |
|---|---|---|
| Engine: instruction pair + loop control | 110 | 2 × 16-bit IR |
| Engine: PC, link, watcher vectors, next-PC | 150 | 9-bit addresses |
| Engine: timer (T, period, compare, fraction, sync) | 300 | 66 flops |
| Engine: shifter S, bit counter, taps | 150 | 16-bit S, 8-bit N |
| Engine: general registers R0–R2 | 100 | 3 × 16 bit |
| Engine: ALU + operand/result muxes | 300 | 16-bit add/sub/logic, registered flags |
| Engine: 2 condition cells (LUT2) | 80 | 12 config bits each |
| Engine: logical pins (bind, in-mux, out, edge) | 110 | 4 × 16:1 |
| Engine: guard mux, decode, control | 220 | |
| **Engine total × 4** | **1.52K × 4 = 6.1K** | ≈ 285 flops each |
| Host link (H1) | 600 | incl. debug read-back |
| Stream FIFOs | 800 | 8 × 4 bytes |
| Pin plane | 500 | sync, owner muxes, pad regs |
| Slot counter + address mux, shared LFSR-32, signals, config | 700 | |
| **Total** | **8.7K synth ≈ 10.9K placed** | + macro (1.45 tiles), or + 4.8K fallback = 15.7K (budget 22K) |

An 8-bit data path would save ≈ 300 cells per engine; decide in 2b after the paper programs.
The table is the estimate as evaluated. The prefetch buffer and event flags of §10 add ≈ 140 cells per engine; current totals are in §10.6.

**Critical path.**
Since D018 instructions execute only from the instruction register, never straight from the SRAM output.
- P1 fetch: SRAM 6.3 → instruction/prefetch register mux 0.5 → setup 0.2 = **≈ 7 ns**. Address side: instruction register 0.45 → guard mux 0.9 → fire + branch select (registered flags) 1.0 → fetch-pointer mux 0.45 → slot mux 1.5 → MEN setup 0.85 = **≈ 5 ns**.
- P2 execute: instruction register 0.45 → operand mux 0.9 → 16-bit add 5.5 → result mux 0.9 → setup 0.3 = **≈ 8 ns** (was ≈ 14 ns from the SRAM output). Fits 50 and 60 MHz; the 16-bit add no longer limits 60 MHz.
- P3 reaction: sync → pin mux → condition cell → guard (live event or flag) → pin action → owner mux → pad register ≈ 7 ns.

**Stress cases.**
- I2C target + stretching (400 kHz, 125 clk/bit): watchers catch START/STOP anywhere; `loop 8 { @rise SCL : shin }`; ACK and SCL hold are fused to the falling-edge guard, so SCL is held 50–70 ns after it falls (tLOW min is 1300 ns). The ACK guard is armed ≤ 9 clk after the last address bit (≥ 30 clk of SCL high available) and a START/STOP handler ≤ 5 clk after its watcher. Feasible. Controller arbitration: one condition cell (SDA driven high, read low, SCL high) aborts to a handler. Feasible, and nothing I2C-specific exists in hardware.
- SPI target: bit path is a 2-instruction micro-loop, so max SCLK = the floor, **4 MHz**, on any engine. Register emulation: last command bit → first response bit on the pad takes ≤ 6 clk with one table access (a read that also drives MISO) and ≤ 10 clk with two. The launch may be late (latched fall event) as long as MISO is valid before the next rising edge: 4 MHz needs the one-access form, two accesses reach 3.3 MHz, and the MUST rate of 2 MHz fits four (§10.5).
- UART RX: start edge → `tsync`, sample at `tock`; 1-clk resolution = 50× oversampling at 1 Mbaud, ≥ 16× up to 3.1 Mbaud. The start-edge guard is re-armed ≤ 9 clk after the stop-bit sample; 15 clk are available at 1 Mbaud with a 2 % fast sender, 5 clk at 3 Mbaud, where the loop must close in one fetch (2b). Full duplex = two engines with no shared state, 7 + 9 words.
- CAN 500 kbit/s (100 clk/bit): drive at `tick`, sample at `tock`, compare (arbitration and bit error), `jrun` (stuffing), LFSR tap (CRC-15): ≈ 6 instructions per bit. Hard sync by `tsync` at SOF; SJW-limited resync is a resuming watcher that adjusts the timer in software. Feasible.
- USB LS (33.33 clk/bit, fractional timer, hard-sync rule on every edge): differential shift-in with run tap, `jrun`, loop: 3–4 instructions per bit (≤ 16 of 33 clk at the slot rate); SE0 through a condition cell; reply due in 6.5 bit times = 216 clk. Feasible at bit and packet level; a full device stack is out of scope.
- 10 Mbit Manchester: TX feasible at 60 MHz (2-instruction micro-loop on a 3-clk tick), not at 50 MHz. RX at 60 MHz (edge → `tset 4` → sample) has ±1 clk margin and needs the auto-transfer extension to offload data: marginal, not claimed.

**Roles that are pure software.** All six: controller, target, transmitter, receiver, monitor, pattern generator/tester. Error injection is also pure software (a different program or host-supplied parameters). Hardware contributes only the drive-enable mask for the monitor guarantee.

**Top 3 risks.**
1. One memory port for four engines. Since D018 the port is time-divided, so programs cannot interfere, but unguarded code runs at one instruction per 4 clk: every path between two events has to fit 4 clk per word not already prefetched, and the turnaround paths (SPI target command → response, UART stop → start) are the tight ones. Bounded statically by the assembler and witnessed at run time by the overrun flag (D019); the slot, prefetch and event-flag rules must be specified exactly and modelled cycle-exactly in the ISS.
2. Feature interaction inside the engine: watcher preemption × micro-loop × multi-clock memory operations × slot waits × event flags. Mitigation: one ordered rule list per clock instant in the spec, lockstep random testing, formal checks on arbiter and preemption.
3. Instruction density: 4-bit guard + 4-bit operation + 8-bit operand may need two instructions where one was assumed, and at the slot rate each extra word costs 4 clk: it eats the margin on the SPI-target turnaround and lengthens the inter-byte gap of the 12.5 MHz controller. The paper programs in 2b and the ISS in Phase 4 settle this before RTL; at the MUST rates the sketched turnaround paths use ≤ 60 % of the available clocks (§10.5).

---

## 3. Candidate C — CONDUCTOR: one event-driven CPU + timed pin channels

Lineage: timer-processor units and timed ports. Software decides, hardware places the edges in time.

**Execution model.** One 16-bit CPU (8 registers, 1 instruction/clk, RAM variables, call/return). 8 pin channels raise events (match, edge, word done). A hardware priority encoder vectors the CPU to the handler of the highest pending event; handlers run to completion. Concurrency = any number of handler sets, limited by channels and CPU time. Granularity: one handler per event or per word. Timing is expressed as timestamps: each channel has a compare register ("at time T do this pin action") and a capture register ("this edge happened at time T") against a free-running 24-bit timebase.

**Bit plane.** Per channel: 8-bit shifter clocked by match or by a pin edge, period auto-reload, pre-armed pin action. Everything else (parity, CRC, stuffing, line codes) is software on the ALU.

**Pin model.** A channel owns one pin (selectable), open-drain per channel, so conflicts cannot occur. Shared sync; edge detect per channel with a level qualifier from a second pin.

**Host interface.** H1; the CPU does stream demultiplexing in software (2 shared FIFOs).

**Memory.** 1P 512x16 holding program, variables and stack. Programs are 60–200 words per protocol. Fallback 128 words holds UART full duplex or SPI; I2C target is marginal.

**Cells (synthesized).**

| Block | Cells | Assumption |
|---|---|---|
| CPU: register file 8 × 16 | 480 | 128 flops, two 8:1 read muxes |
| CPU: ALU, shifter, flags | 300 | |
| CPU: PC, SP, IR, decode | 350 | |
| Event dispatch | 180 | pending/mask, priority encoder, vectors |
| Channel × 8 | 260 × 8 = 2.1K | compare 16, capture 16, period 16, shifter 8 |
| Timebase | 80 | 24 bit |
| Host link, FIFOs, pin plane, memory glue, misc | 1.85K | |
| **Total** | **5.3K synth ≈ 6.7K placed** | + macro, or + 4.8K fallback = 11.5K (budget 22K) |

**Critical path.** Single-cycle CPU: SRAM 6.3 → register read mux → ALU → write-back ≈ 14–15 ns. 50 MHz yes; 60 MHz needs a pipeline stage.

**Stress cases.**
- I2C target + stretching: edge events and a pre-armed "on SCL fall hold SCL" action; START/STOP via the qualified edge detector. Feasible. Arbitration: software compare per bit. Feasible.
- SPI target: bits move in the channel shifter, but the byte-boundary lookup is dispatch (2 clk) + handler (5–6) ≈ 8 clk. **Max 2 MHz alone**; with another handler running (non-preemptive, ≈ 12 instructions) the MUST rate is at risk.
- UART RX: channel samples on match and raises one event per byte; full duplex at 1 Mbaud is feasible.
- CAN: ≈ 20 instructions per bit (stuffing and CRC in software). 500 kbit/s feasible alone; resync from capture timestamps is elegant.
- USB LS: ≈ 25 instructions per bit against 33 clk. Not feasible.
- 10 Mbit Manchester: TX from pre-encoded bytes is marginal at 60 MHz; RX not feasible.

**Roles that are pure software.** All, but every role's speed and jitter then depend on handler length and on what else is running.

**Top 3 risks.**
1. Reaction latency and schedulability: every program needs a worst-case-latency argument, and adding a second protocol can break the first.
2. Novelty: the brief's FAQ calls a CPU-style design "the most common approach", and the prize is for the most novel designs.
3. Software volume and a speed ceiling near 1 Mbit/s for anything with per-bit work; the fallback memory is too small for part of the MUST list.

---

## 4. Rejected early: eFPGA fabric
A std-cell LUT4+FF site with muxed routing costs ≈ 60 configuration flops plus muxes ≈ 100 cells, so 16K cells buy ≈ 160 sites: one small protocol at a time, slow mux-chain routing, a place-and-route toolchain instead of an ISA and assembler, and a fabric that cannot be verified in the time available. The idea survives only as B's condition cells.

## 5. Stress-case matrix

| Case | A LANES | B Cleo | C CONDUCTOR |
|---|---|---|---|
| I2C target + stretch, 400 kHz | yes (stretch designed in) | yes (watchers + fused hold) | yes |
| I2C controller arbitration | yes (designed in) | yes (condition cell) | yes (software) |
| SPI target max SCLK | 4 MHz | 4 MHz on any engine; register lookup 4 MHz with one table access, 3.3 MHz with two | 2 MHz alone |
| UART RX ≥ 16× + full duplex 1 Mbaud | yes | yes (50×) | yes |
| CAN 500k: stuff, arbitration, resync | yes if all three designed in | yes, ≈ 6 instr/bit of 100 clk | yes alone, ≈ 20 instr/bit |
| USB LS 1.5 Mbit, NRZI + stuffing | packet level, if 2-pin symbols designed in | packet level | no |
| 10 Mbit Manchester | TX at 60 MHz, RX marginal | TX at 60 MHz, RX marginal | TX marginal, RX no |
| SPI controller max | 12.5 MHz | 12.5 MHz (micro-loop) | ≈ 6 MHz alone |
| Not-designed-in bit behaviour | 1-bit fields, slow | native | native but slow |

## 6. Scoring (1–5, 5 best; generality ×3 and originality ×2 because flexibility is the primary criterion and the prize is for novelty)

| Criterion | A | B | C | Why |
|---|---|---|---|---|
| Generality (protocols not designed in) ×3 | 3 | 5 | 4 | A is fast only where anticipated; B is general at the bit level and can decide on chip; C computes anything but reacts slowly |
| Area ×1 | 3 | 3 | 4 | 9.8K / 10.9K / 6.7K placed; all fit with the fallback |
| Timing risk ×1 | 4 | 3 | 3 | A has no fetch in the bit path; B and C execute through the SRAM in one cycle |
| Verification effort ×1 | 2 | 4 | 3 | A: configuration cross-product; B: one small engine × 4 with exact per-instant semantics; C: CPU + peripherals + timing analysis per program |
| Originality ×2 | 3 | 4 | 2 | B's guard-on-every-instruction + watchers + micro-loop is not a common shape; C is the common one |
| Write-up story ×1 | 3 | 5 | 3 | B programs read like the protocol's timing description; the ISS is the executable semantics |
| **Weighted total** | **27** | **38** | **29** | |

## 7. Recommendation: Candidate B (Cleo)

1. It is the only candidate where a protocol nobody anticipated costs nothing extra: bit-level behaviour is instructions, yet the response to an edge is as fast as dedicated hardware because the waiting instruction is the armed response.
2. It meets every MUST and reaches the SHOULD rates (SPI target 4 MHz, controller 12.5 MHz, UART 3 Mbaud) without protocol-specific blocks; margins and conditions after the approval changes are in §10.5. CAN and USB LS at bit/packet level need no dedicated hardware.
3. All six roles and error injection are pure software, which is the evidence REQUIREMENTS §3 asks for.
4. One engine verified once and instantiated four times; synchronous per-instant semantics make the ISS, lockstep and formal work tractable.
5. Area leaves ≈ 5K cells of headroom even with the flop fallback.

Taken from the other candidates: A's fractional timebase with hard-sync and its stream taps (parity, run length, CRC), C's timestamp capture and real ALU.

Deliberately left out of v1: parallel 2/4-bit shift (Dual/Quad SPI), 10 Mbit Manchester RX, USB device stack, more than 4 concurrent contexts, dual-port memory. The micro-loop auto-transfer extension is specified in 2b only if the paper programs need it.

## 8. Decisions confirmed by the user (2026-11-15, D017)
1. Architecture = Candidate B. Confirmed.
2. Memory = one 1P 512x16 macro (1 KB, 45k µm²), single port; fallback 128x16 flops. Confirmed, except the fetch arbitration: time-division slots + prefetch instead of fixed priority (D018, §10.2).
3. Host link = H1 on 3 pins (H_RX, H_TX, H_RDY), bit time locked to `clk`; SPI link rejected. Confirmed.
4. 4 engines. Confirmed. 16-bit vs 8-bit data path: settled in 2b with numbers.
5. v1 exclusions listed in §7. Confirmed.
6. Added by the user: latched events with an overrun flag (D019, §10.3); a shift-on-edge assist is evaluated in 2b (D020, §10.4).

---

## 9. Adjustments before approval (2026-11-14; they describe B before the changes of §10)

The choice stands: **Candidate B (Cleo)**. Adjustments:

1. Fallback memory cost 4.8K → 7.7K cells (≈ 3.7 mapped cells/bit, not 2.3). Totals with fallback: A 17.5K, B 19.1K, C 14.4K. Logic cap for B tightens to ≤ 14K placed cells.
2. Host link budget 0.6K → 1.0K hard cap: load, read-back, stream mux, halt/run/step, register read. Anything more needs a DECISIONS line. B becomes 9.1K synth ≈ 11.4K placed.
3. The 8-bit data-path option gains weight (60 MHz stretch, smaller engine). Still a 2b decision after the paper programs.
4. Area by µm² is not tight (≈ 25 % of the die), so a 2 KB macro or the dual-port 512x16 remains affordable if 2b shows memory or fetch contention is the limit. Not adopted now.

---

## 10. Approval and changes (2026-11-15)

The user approved Candidate B (Cleo) with the changes below (D017–D020). Where an older section and this one differ, this one wins.

### 10.1 Confirmed as proposed
4 engines; one 1P 512x16 macro with the 128x16 flop fallback; host link H1 on 3 pins (H_RX, H_TX, H_RDY); the v1 exclusions of §7; D011–D016. The 16-bit vs 8-bit data path is settled in 2b with numbers (paper-program sizes, cells per engine, Fmax).

### 10.2 Memory port: time-division slots + prefetch (D018, replaces fixed priority)
- A free-running 2-bit counter `slot` (0 at reset, +1 every clk) gives engine Ei the port in every clock with `slot = i`. The slot is Ei's whether it uses it or not. An unused slot can serve the host (program load, read-back) but never another engine, so the grant depends on the counter alone and no program can change another program's timing.
- One access per slot: instruction fetch, table read or table write. A read issued in clock n is in an engine register in clock n + 2. Instructions execute only from the instruction register; there is no execute-from-SRAM bypass (it would save 1 clk and put the 6.3 ns access time back on the execute path).
- Prefetch: each engine holds the armed instruction (or micro-loop pair) plus one prefetched successor, and fetches in its next slot whenever that buffer is empty or being emptied. When a guard fires, the buffered instruction is armed in the following clock. Armed = in the instruction register with its guard evaluated; if the guard is true it fires in that clock.
- A taken jump or a watcher vector discards the buffer and any read in flight, and requests the target in the same clock.

| Case | Clocks from one instruction firing to the next being armed |
|---|---|
| Successor prefetched (guaranteed when the firing instruction had been armed ≥ 4 clk) | 1 |
| Successor not prefetched, taken jump, watcher vector | 2–5 (slot wait 0–3 + read 2) |
| j words in a row that could not be prefetched | ≤ 4j + 1 |
| Table read or write | data in its register 2–5 clk after the instruction fires; counts as one word |

- **Worst-case fetch latency: 5 clk** from request to armed (3 clk slot wait + 2 clk read), identical for E0–E3 and independent of the other engines. Before: 1 clk on E0, and on E1–E3 whatever the higher engines' unguarded runs left.
- Rate: unguarded code runs at 1 instruction per 4 clk on every engine (12.5 M/s at 50 MHz), two on consecutive clocks when the second was prefetched. Event-paced code with one instruction per event and events ≥ 4 clk apart never waits for memory.
- Static bound: for every path from one guard to the next the assembler counts the words and table accesses that cannot have been prefetched (w) and reports 4w + 1 clk. Against the program's declared minimum event spacing it proves each guard is armed before its event, or states by how much it can be late. It does this one program at a time; the other engines do not enter. Wait-free loops are legal (nobody can be starved). The only hidden variable is the slot phase, so the ISS confirms a bound by sweeping 4 phases.
- Formal properties: (1) grant_i ⇔ `slot = i`; (2) a request in clock r is armed by r + 5; (3) after a redirect no buffered or in-flight word executes; (4) the event properties of §10.3.
- Paths: fetch ≈ 7 ns, address ≈ 5 ns, execute ≈ 8 ns (§2, critical path). The 16-bit add is off the SRAM path, so 60 MHz no longer argues for the 8-bit option; only area does.
- If one engine ever needs more than one access per 4 clk, a host-set 4-entry slot table (e.g. E0 E1 E0 E2) keeps every bound static for ≈ 30 cells. Not adopted; 2b decides from the paper programs.

### 10.3 Events: latched, consumed, overrun (D019)
- Per engine, one sticky flag for each guard event: rise and fall of its 4 logical pins, tick, tock (10 flags).
- In every clock an engine is either *waiting* (an armed guard is false) or *busy* (firing, executing, fetching, in a memory access). While busy, each event that is not consumed in that clock sets its flag. While waiting, the armed guard sees events live, exactly as before, and all flags are held clear: an event older than the wait is stale.
- A guard is true when its event is live or its flag is set, and firing consumes that one event. An instruction therefore fires at the later of "event happened" and "instruction armed": an edge is answered late, never dropped.
- Overrun: each flag also remembers whether it has absorbed more than one event. A guard that consumes such a flag sets the engine's sticky overrun flag: the response is at least one whole event late. The flag is reported to the host in-band and can be tested by the program. Events the program never waits for cannot raise it, so a program whose guards are all armed in time (§10.2 bound) never sets it. It is the run-time witness of the static bound.
- Timer: restarting the timer (`tsync`, `tset`) clears the tick and tock flags. A late answer to a tick shortens the interval to the next tick, so paced outputs still depend on being armed in time; the bound proves it and the overrun flag catches a miss of a whole period.
- Host data needs no flag: `hrx`/`htx` are FIFO levels that persist until `pull`/`push`; a host byte arriving at a full FIFO sets the same overrun flag. Inter-engine signals are levels already. Watchers are evaluated in hardware every clock and need no flags; a watcher abort clears them.
- Rejected: flags that also latch while a guard waits. The data edges of one UART frame would fire the next start-bit guard and raise overrun in every frame, and a program-side flush would race with a real start edge.
- Cost ≈ 80 cells per engine (21 flops). Same-clock ordering goes into the 2b rule list. Formal: every event is seen live, latched, or recorded as absorbed; no flag survives a waiting clock.

### 10.4 Shift-on-edge assist: evaluate in 2b (D020)
- Candidate: an autonomous shift per engine, armed by one instruction. On a selected edge of a selected logical pin, S shifts one bit (in, out with a pin action, or both) with no instruction involved, and a bit-count event marks the word boundary. The instruction stream stays free for the word-boundary work (table read, `push`/`pull`).
- What it has to beat: the micro-loop already shifts on an edge without a fetch. The 4 MHz ceiling is the synchronizer + pad round trip (50–70 ns) against a half period (§0.3), and since D018 a word boundary also costs slots (§10.5). The evaluation therefore covers (a) launching MISO from the sampling edge, a full-period budget that reaches ≈ 8 MHz by the same margin rule and may need no hardware; (b) shifting that continues while instructions handle the word boundary; (c) the word-boundary auto-transfer of §2.
- Limit known now: a response that depends on the command just received needs one table access (≤ 6 clk), which caps it near 4.4 MHz whatever the assist does. Only streamed or pre-loaded data can go higher.
- Adopt only if it costs ≤ 500 cells for all four engines together and contains nothing SPI-specific: it must serve any edge-clocked serial role (I2C target, PS/2 device, JTAG TAP, SWD target). Otherwise the rejection and its reason go into DECISIONS.md.

### 10.5 Recomputed timing at 50 MHz (D018 + D019)
"Before" figures are for E0 under fixed priority; E1–E3 had no bound of their own.

| Item | Before | After, any engine |
|---|---|---|
| Next instruction armed after one fires | 1 clk | 1 clk if prefetched, else 2–5 clk; ≤ 4j + 1 clk for j words |
| Unguarded code | 1 instruction per clk | 1 instruction per 4 clk |
| SPI target bit path | 4 MHz on E0, 2 MHz on any engine | 4 MHz (micro-loop, no fetch; floor-limited as before) |
| SPI target last command bit → first response bit on the pad | 4–5 clk | ≤ 6 clk with one table access (a read that also drives MISO), ≤ 10 clk with two |
| SPI target max SCLK with register lookup | 4 MHz | 4 MHz with one access, 3.3 MHz with two; the MUST rate of 2 MHz fits four (18 of 19 clk) |
| UART RX sampling | 50× at 1 Mbaud, cycle-exact | unchanged (armed in a micro-loop) |
| UART RX stop sample → start-edge guard armed | 3 clk | ≤ 9 clk (push, jmp, target); 15 clk available at 1 Mbaud with a 2 % fast sender |
| UART RX at 3 Mbaud (SHOULD), 5 clk available | 3 clk | 9 clk as sketched: start edge caught up to 4 clk late (24 % of a bit), not lost. ≤ 6 clk with the jump prefetched, 0 if the prefetcher follows it → 2b |
| I2C target ACK and SCL hold after SCL falls | 2–3 clk + pad | unchanged (fused to the armed guard) |
| I2C target last address bit → ACK guard armed | 3 clk | ≤ 9 clk; ≥ 30 clk available (tHIGH min at 400 kHz) |
| I2C target START/STOP watcher → handler armed | 1 clk | ≤ 5 clk; ≥ 30 clk to the next SCL edge (tHD;STA) |

SPI budget, by the margin rule of §0.3: MISO valid ≥ 55 ns before the controller's sampling edge (45 ns margin + 10 ns setup), i.e. pad register updated ≤ T − 5.25 clk after the clock in which the last sample fires (T = SCLK period in clk). A launch later than the falling edge counts because that edge is latched (D019).

### 10.6 Budget
Prefetch + fetch control ≈ 60 cells and event flags ≈ 80 cells per engine: engine 1.52K → 1.66K. Chip 9.1K → 9.7K synth, 11.4K → 12.1K placed; with the flop fallback 19.1K → 19.8K (budget 22K). Logic cap 14K placed unchanged. An adopted assist adds ≤ 0.5K synth.

### 10.7 Carried into 2b
1. ISA shaped for the slot rate: at most one un-prefetched word between an event and its response on turnaround paths. Candidates: a table read that also drives a pin; micro-loop entry without a separate header fetch; a prefetcher that follows unconditional jumps.
2. Every paper program states its minimum event spacing and the assembler's bound per guard.
3. Per-instant rule list covering slots, prefetch, redirects and the event flags.
4. Assist evaluation (§10.4); 8- vs 16-bit numbers; slot table only if a paper program needs it.
