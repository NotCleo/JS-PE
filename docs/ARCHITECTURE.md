# Architecture

This is the reader's version of the architecture. The normative text, with every per-clock rule, is [docs/spec/ARCH.md](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/ARCH.md); where this page and the spec differ, the spec wins. Section numbers in brackets (ARCH 4.2) point into it.

## 1. The idea in one paragraph

Four identical engines (E0–E3) run 16-bit `guard : operation` instructions from one shared 128 x 16 program memory. An engine holds one *armed* instruction and waits until its guard (a pin edge, a timer event, a FIFO level, a condition) is true; the whole operation then happens in that clock. Nothing protocol-specific is in hardware: the hardware gives events, a timer, a shifter with taps, an ALU, pin actions and byte FIFOs to the host, and the program decides what a "bit", a "frame" or an "acknowledge" is. The reaction from a pad edge to a pad change is 3–4 clk + pad (70–90 ns at 50 MHz) with no instruction fetch in between (ARCH 4.1, D081).

## 2. Block diagram

```
 ui_in[7:0] ──┐  jspe_pins                                  ┌── uo_out[7:0]
 uio_in[7:0] ─┤  2-FF sync → optional filter → lvl[15:0]    ├── uio_out[7:0]
              │  pad config (owner, mode, enable: host only)├── uio_oe[7:0]
              │  owner mux → pad registers ◄── val/oe 4x4 ──┤
              └───────┬─────────────────────────────────────┘
                      │ lvl[15:0]     sig[7:0] ◄► jspe_sig      jspe_lfsr (shared LFSR-32)
        ┌─────────────┼───────────────┬───────────────┬───────────────┐
   ┌────▼─────┐  ┌────▼─────┐    ┌────▼─────┐    ┌────▼─────┐
   │ engine 0 │  │ engine 1 │    │ engine 2 │    │ engine 3 │    jspe_engine x4
   │ seq timer│  │          │    │          │    │          │    (jspe_seq, jspe_timer, jspe_shift,
   │ shift alu│  │          │    │          │    │          │     jspe_alu, jspe_epins)
   └─┬──────┬─┘  └─┬──────┬─┘    └─┬──────┬─┘    └─┬──────┬─┘
     │      └──────┼──────┴────────┼──────┴────────┼──────┴── rx/tx FIFO pair per engine (jspe_fifo x8)
     │ req/gnt     │               │               │                  │
   ┌─▼─────────────▼───────────────▼───────────────▼─┐          ┌─────▼─────┐  H_RX  = ui_in[3]
   │ jspe_arb: slot counter, slot map, address mux   │◄─────────┤ jspe_link │  H_TX  = uo_out[4]
   └────────────────────┬────────────────────────────┘ host req └───────────┘  H_RDY = uo_out[5]
                   ┌────▼─────┐
                   │ jspe_mem │  128x16 flops (built); 512x16 SRAM macro is a deferred retry
                   └──────────┘
```

The top module [src/tt_um_notcleo_jspe.v](https://github.com/NotCleo/JS-PE/blob/main/src/tt_um_notcleo_jspe.v) synchronizes `rst_n` once (2 FF) into the internal synchronous active-low reset and wires the blocks. There is one clock, `clk`; no gated or generated clocks.

## 3. Modules

Area: `make area LIB=<sg13g2 typ liberty>` (yosys + abc, typical corner, synthesized, not placed), run 2026-11-20 on commit `0dd7832` (src/ unchanged since `1763346`). Formal: [formal/RESULTS.md](https://github.com/NotCleo/JS-PE/blob/main/formal/RESULTS.md); "properties" counts assertions plus helper invariants, all proven. Unit tests: cocotb files under [test/unit/](https://github.com/NotCleo/JS-PE/tree/main/test/unit).

| Module | Purpose | Inst. | Cells | Area µm² (each) | Formal | Unit tests |
|---|---|---|---|---|---|---|
| [tt_um_notcleo_jspe](https://github.com/NotCleo/JS-PE/blob/main/src/tt_um_notcleo_jspe.v) | top: reset sync, wiring, `_unused` | 1 | 24 | 131 | top: 4 properties, k-induction k=4 from an arbitrary state + whole-chip idle bmc 40; 6/6 mutants | [test/test.py](https://github.com/NotCleo/JS-PE/blob/main/test/test.py) (7) |
| [jspe_engine](https://github.com/NotCleo/JS-PE/blob/main/src/jspe_engine.v) | one engine; glue only (stream mux, requests, status) | 4 | 98 | 815 | via sub-blocks | [test_jspe_engine.py](https://github.com/NotCleo/JS-PE/blob/main/test/unit/jspe_engine/test_jspe_engine.py) (12, lockstep vs ISS) |
| [jspe_seq](https://github.com/NotCleo/JS-PE/blob/main/src/jspe_seq.v) | IR/PF/HR, fetch FSM, decode, guards, watchers, events | 4 | 1 245 | 18 989 | 56 properties, k=1…7; 47/49 mutants (2 equivalent) | engine lockstep |
| [jspe_alu](https://github.com/NotCleo/JS-PE/blob/main/src/jspe_alu.v) | R0–R2, 16-bit ALU, address adder | 4 | 1 099 | 12 374 | none (engine lockstep only) | engine lockstep |
| [jspe_timer](https://github.com/NotCleo/JS-PE/blob/main/src/jspe_timer.v) | T/P/C timer, fraction, prescaler, sync | 4 | 495 | 8 177 | 10 properties, k=2; 18/18 mutants | engine lockstep |
| [jspe_shift](https://github.com/NotCleo/JS-PE/blob/main/src/jspe_shift.v) | S register, bit counter, taps, auto-transfer | 4 | 448 | 6 604 | 7 properties, k=2; 21/21 mutants | engine lockstep |
| [jspe_epins](https://github.com/NotCleo/JS-PE/blob/main/src/jspe_epins.v) | 4 logical pins: bind, edges, pin state | 4 | 294 | 5 089 | 9 properties, k=2; 26/26 mutants | engine lockstep |
| [jspe_link](https://github.com/NotCleo/JS-PE/blob/main/src/jspe_link.v) | host link: UART framing, commands, registers, streams | 1 | 1 508 | 23 803 | 20 properties, k=2 (+ bmc 16 cut tasks); 34/34 mutants | [test_jspe_link.py](https://github.com/NotCleo/JS-PE/blob/main/test/unit/jspe_link/test_jspe_link.py) (5) |
| [jspe_pins](https://github.com/NotCleo/JS-PE/blob/main/src/jspe_pins.v) | sync, filter, pad config, owner mux, pad registers | 1 | 728 | 17 625 | 10 properties, k=4; 20/20 mutants | [test_jspe_pins.py](https://github.com/NotCleo/JS-PE/blob/main/test/unit/jspe_pins/test_jspe_pins.py) (7) |
| [jspe_mem](https://github.com/NotCleo/JS-PE/blob/main/src/jspe_mem.v) | 128 x 16 program/table memory (flops) | 1 | 5 002 | 162 095 | none | none (top-level tests) |
| [jspe_fifo](https://github.com/NotCleo/JS-PE/blob/main/src/jspe_fifo.v) | 4-deep byte FIFO; RX W=8, TX W=9 | 4 + 4 | 138 / 151 | 2 959 / 3 273 | 8 properties, k=2, both widths; 14/14 mutants | [test_jspe_fifo.py](https://github.com/NotCleo/JS-PE/blob/main/test/unit/jspe_fifo/test_jspe_fifo.py) (8) |
| [jspe_arb](https://github.com/NotCleo/JS-PE/blob/main/src/jspe_arb.v) | memory slot arbiter | 1 | 150 | 1 612 | 15 properties, k=4; 6/6 mutants | [test_jspe_arb.py](https://github.com/NotCleo/JS-PE/blob/main/test/unit/jspe_arb/test_jspe_arb.py) (9) |
| [jspe_lfsr](https://github.com/NotCleo/JS-PE/blob/main/src/jspe_lfsr.v) | shared 32-bit LFSR / CRC | 1 | 366 | 6 310 | 5 properties, k=2; 11/11 mutants | [test_jspe_lfsr.py](https://github.com/NotCleo/JS-PE/blob/main/test/unit/jspe_lfsr/test_jspe_lfsr.py) (8) |
| [jspe_sig](https://github.com/NotCleo/JS-PE/blob/main/src/jspe_sig.v) | 8 inter-engine signal flags | 1 | 57 | 862 | 5 properties, k=2; 8/8 mutants | [test_jspe_sig.py](https://github.com/NotCleo/JS-PE/blob/main/test/unit/jspe_sig/test_jspe_sig.py) (7) |

Totals from the same run: one engine 52 049 µm² (x 4 = 208 195), logic without memory 283 465 µm², full top 23 669 cells / 445 560 µm² synthesized. After place and route (GDS run [44625407065](https://github.com/NotCleo/JS-PE/actions/runs/44625407065), PROGRESS ledger): 692 464 µm² placed = 57.3 % of the 1 208 170 µm² core (limit 60 %, D051/D063), 46 813 cells, of which 4 387 flip-flops. The memory alone is 36 % of the synthesized area; the 512 x 16 SRAM macro (45.3k µm²) failed LVS in run 3 and is a deferred retry (D062).

Generated file: [src/jspe_isa.vh](https://github.com/NotCleo/JS-PE/blob/main/src/jspe_isa.vh) holds the encodings, written by [sw/gen_isa.py](https://github.com/NotCleo/JS-PE/blob/main/sw/gen_isa.py) from [docs/spec/isa.yaml](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/isa.yaml). [src/RM_IHPSG13_1P_512x16_c2_bm_bist_bb.v](https://github.com/NotCleo/JS-PE/blob/main/src/RM_IHPSG13_1P_512x16_c2_bm_bist_bb.v) is the macro's black box, used only with `-DJSPE_MEM_SRAM`.

## 4. Shared blocks

| Block | What it does | Numbers that matter |
|---|---|---|
| Pins (ARCH 2.1) | Every input: pad → 2 FF → optional 3-sample filter → `lvl[15:0]`. Every output pad has a 7-bit host-only config `PAD[j]`: enable, open-drain, invert, owning engine, logical pin. Exactly one (engine, pin) owns a pad, so two engines can never drive one pad. | filter +2 clk, removes pulses < 40 ns; `en` is host-only, so a program loaded with all `en` = 0 cannot drive anything (monitor guarantee) |
| Arbiter (ARCH 2.2) | One memory port, four slots. A 2-bit counter picks the slot owner from the 8-bit SLOTMAP; an unused slot serves the host. | default `0xE4` = one slot per engine every 4 clk (max wait 3 clk); `0x00` = solo, E0 every clock |
| Memory (ARCH 2.3) | 128 x 16 flops, read data one clock after the grant. Holds code, `.word` tables and buffers of every engine; no protection. | addresses alias modulo 128; contents undefined after reset or deselect |
| FIFOs (ARCH 2.4) | Per engine an RX FIFO (host → engine, 8 bit) and a TX FIFO (engine → host, 9 bit; bit 8 marks an event code). | depth 4; write to full / read of empty is ignored and sets the engine's sticky OVR |
| Host link (ARCH 2.5) | UART-style link to the RP2350: framing, commands, registers, memory access, stream multiplexing. See [HOST_INTERFACE.md](https://github.com/NotCleo/JS-PE/blob/main/docs/HOST_INTERFACE.md). | 8N1 at 16, 8 or 4 clk per bit; H_RDY flow control |
| Signal flags (ARCH 2.6) | Eight flags G0–G7 that engines set/clear and read as if they were pins (level, rise, fall). | clear wins over set; the host can write all eight |
| LFSR (ARCH 2.7) | One shared 32-bit LFSR with programmable polynomial, left or right form, stepped by any engine's shifter. Used for CRC. | lowest-numbered engine wins on a collision (D074, D084) |

## 5. The engine

| Sub-block | Holds |
|---|---|
| `jspe_seq` | armed instruction IR, prefetch PF, loop partner HR, PC, FP, link L; memory request FSM; decode; guard mux; two condition cells and two watchers; event flags; Z, C; run/stop/step/exec control |
| `jspe_timer` | T (count), P (period), C (compare), 8-bit fraction accumulator, prescaler (÷1/16/256), sync on a pin edge; events `tick` (T reached 0) and `tock` (T = C) |
| `jspe_shift` | S (1–16-bit word), bit counter N, taps: last bit B, parity, run length, differential decode/encode, LFSR step; auto-push/pull at the word boundary |
| `jspe_alu` | R0–R2, 16-bit add/sub/logic/shift-by-one, address adder for `ld`/`st` |
| `jspe_epins` | four logical pins p0–p3, each bound to any of 32 sources (16 pads, 8 signal flags, constants) with invert, rise/fall detect, and an output state (`val`, `oe`, `follow`) |

What makes it fast enough for bit-level protocols without a high clock:

- **Act in the clock of the event.** The guard is evaluated every clock against live events; the operation needs no fetch. Edges and timer events that happen while the engine is busy are latched and answered late, and a second one while the first is pending sets OVR (ARCH 6).
- **Prefetch with jump folding.** One prefetch word; an unconditional `jmp` in the prefetch buffer is replaced by its target and never executes (ARCH 4.2).
- **Micro-loop.** An E-format instruction can repeat itself (`rep`) or alternate with its neighbour (`head`/`tail`) without fetching, until the shifter completes a word (ARCH 4.4). A whole SPI byte is one held pair.
- **Follow pins and auto-transfer.** A pin can follow the shifter's out tap every clock; at a word boundary the shifter can push the finished byte and pull the next one from the FIFOs in the same clock (ARCH 8, D026).
- **Watchers.** Two condition cells (any 2-input function of levels, flags or timer state) can preempt any wait with an abort or a call — used for timeouts, STOP conditions, CS rising (ISA 6).
- **Timer idiom.** *tock = end of a phase, tick = deadline*: normal flow restarts the timer before it reaches 0, so a tick means the phase overran and goes to a watcher (ARCH 7).

## 6. Cycle timing

| Path | Latency (ARCH 4.1, 4.2) |
|---|---|
| pad edge → pin action on a pad | 3–4 clk + pad (70–90 ns at 50 MHz): the reaction floor |
| same with input filter | 5–6 clk + pad |
| pad edge → shift → pin in follow mode | 4–5 clk + pad |
| timer event → pad | cycle-exact, no jitter |
| next instruction after a fire, successor prefetched | 1 clk |
| next instruction, fetch needed (default map / solo) | 2–5 clk / 2 clk |
| unguarded straight-line code | 1 instruction per slot period (4 clk default, 2 clk solo) |

Every program states its timing assumptions; the assembler proves, per path, that the next guard is armed before the next event can arrive (static arming bound, [ISA_REFERENCE.md](https://github.com/NotCleo/JS-PE/blob/main/docs/ISA_REFERENCE.md) section 6). At run time OVR is the witness when an event was missed.

## 7. Memory map

| Space | Size | Contents |
|---|---|---|
| Program/table memory | 128 x 16 (9-bit addresses, alias modulo 128) | code, `.word` tables, buffers of all engines |
| Engine registers | 8 per engine | R0, R1, R2, S, P, C, T, L |
| Engine configuration | index 0–8 | SHCFG, TAPCFG, TCFG, BIND01, BIND23, CC0, CC1, WV0, WV1 |
| Shared configuration | index 16–21 | LFSR_LO/HI, POLY_LO/HI, LFCFG; SIG read-only |
| Host registers | 8-bit address | ID, LINK, SLOTMAP, SIG, FILT, PINS, ECLR, EST0–3, PAD[0–15] |

Initialisation code does not use memory: the assembler emits it as host-injected instructions (`EXEC`, D030).

## 8. Reset, start, reload

- Reset (`rst_n` low ≥ 2 clk, or host `RST e` for one engine): engines stopped, pin states 0, timers stopped, FIFOs empty, pads disabled (`uo_out` = 0 except H_TX = 1, H_RDY = 0; `uio_oe` = 0). Data registers, memory and the LFSR are not reset (ARCH 9.1).
- Start of a program: `RST`, injected initialisation, injected `jmp entry`, `RUN`. One `RUN` mask can start several engines in the same clock.
- The chip keeps no state across deselect: the host reloads the full image every time (ARCH 9.3). One engine can be reloaded while the others run; host memory writes use free slots.

## 9. Timing closure

Sign-off figures from GDS run 44625407065 (PROGRESS ledger), `CLOCK_PERIOD` 20 ns: setup worst slack 1.183 ns (slow) / 8.105 (typ) / 12.128 (fast); hold worst slack 0.097 ns (fast) / 0.273 (typ) / 0.588 (slow); TNS 0. 60 MHz was a stretch goal (D007): ≈ 53 MHz slow corner (estimate from 1.183 ns slack; no run at 16.7 ns). How timing was closed (runs 5–8, D081, D119–D121): [VERIFICATION.md](https://github.com/NotCleo/JS-PE/blob/main/docs/VERIFICATION.md) and [DESIGN_RATIONALE.md](https://github.com/NotCleo/JS-PE/blob/main/docs/DESIGN_RATIONALE.md).
