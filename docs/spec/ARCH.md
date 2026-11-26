# ARCH — Cleo architecture (Phase 2b, 2026-11-15)

Candidate B of docs/history/ARCH_CANDIDATES.md with D017–D021 and the 2b decisions D022–D034. Where this file and docs/history/ARCH_CANDIDATES.md differ, this file wins.
Instruction semantics and programmer-visible registers: ISA.md. Host protocol: HOSTIF.md. Pads: PINS.md.

Conventions
- Clock *k* is the interval between rising edges *k* and *k+1*. Conditions are evaluated on register outputs during a clock; every update happens at the edge that ends it.
- *Armed* = in the instruction register IR with its guard being evaluated. *Fires* = guard true in that clock; the operation takes effect at the end of the same clock.
- Numbers are for clk = 50 MHz (20 ns). Cell counts are yosys cells (what `make area` prints), ±30 % until measured.

## 1. Overview

Four identical engines E0–E3 execute 16-bit `guard : operation` instructions from one shared 512x16 memory (IHP SRAM macro, D131; 128x16 flop fallback with `-DJSPE_MEM_FLOP`, D062/D063). An engine holds its armed instruction until the guard's event happens and then acts in that clock, so the reaction to a pin edge is 3–4 clk + pad (D081) with no fetch in between. Everything protocol-specific is program; hardware provides events (pin edges, timer tick/tock, host FIFO levels, condition cells), a shifter with stream taps, a timer, an ALU, and pin actions.

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
                   │ jspe_mem │  1P 512x16 SRAM macro (shipped, D131), or 128x16 flops (-DJSPE_MEM_FLOP)
                   └──────────┘
```
Top module `tt_um_notcleo_jspe`: synchronizes `rst_n` (2 FF) into the internal synchronous active-low reset, instantiates the blocks, ties `ena` and unused inputs into `_unused`.

## 2. Shared blocks and interfaces

All ports are synchronous to `clk`. Widths in bits. "pulse" = high for one clock.

### 2.1 jspe_pins — pin plane (D014, D033)
| Port | Dir | W | Meaning |
|---|---|---|---|
| `ui_in`, `uio_in` | in | 8+8 | raw pads |
| `lvl` | out | 16 | synchronized levels, `lvl[7:0]` = ui_in, `lvl[15:8]` = uio_in |
| `filt` | in | 16 | per-input filter enable (host register FILT) |
| `e_val`, `e_oe` | in | 16+16 | registered pin state (value, drive enable) of logical pin p of engine e at index 4e+p; the pad register adds one clock (D081) |
| `pad_we`, `pad_idx`, `pad_wdata` | in | 1+4+7 | host write of one pad configuration (index 0–7 uo, 8–15 uio) |
| `h_tx`, `h_rdy` | in | 1+1 | host-link outputs, forced onto uo_out[4], uo_out[5] |
| `uo_out`, `uio_out`, `uio_oe` | out | 8+8+8 | pad registers |

- Input path: pad → FF1 → FF2 → `lvl`. With `filt[k]` = 1 the level passes a 3-sample filter (output changes only after three equal consecutive samples; +2 clk, removes pulses < 40 ns) before `lvl`.
- Pad configuration `PAD[j]` (7 bits, reset 0): `en` (drive enable), `od` (open-drain), `inv`, `eng[1:0]`, `lp[1:0]`. Exactly one (engine, logical pin) owns a pad, so two engines can never drive one pad. Any number of pads may share an owner.
- Output function, registered: `v = e_val[4·eng+lp] ^ inv`, `o = e_oe[4·eng+lp]`.
  - uio pad: `en=0` → out 0, oe 0. `od=0` → out `v`, oe `o`. `od=1` → out 0, oe `o & ~v` (drives low or releases, never drives high).
  - uo pad (no output enable): `en=0` → 0, else `v` (`od`, and a released pin, cannot be expressed; see PINS.md).
  - uo_out[4] = `h_tx`, uo_out[5] = `h_rdy` regardless of configuration; writes to PAD[4], PAD[5] are ignored.
- `en` is host-only: no instruction can write a pad configuration. A program loaded with all `en` = 0 cannot drive anything; this is the monitor guarantee.

### 2.2 jspe_arb — memory port arbiter (D018, D027)
| Port | Dir | W | Meaning |
|---|---|---|---|
| `slotmap` | in | 8 | host register SLOTMAP: engine that owns slot s at bits [2s+1:2s], reset `0xE4` (slot s → engine s) |
| `e_req`, `e_we`, `e_addr`, `e_wdata` | in | 4, 4, 4x9, 4x16 | engine requests, held until granted |
| `h_req`, `h_we`, `h_addr`, `h_wdata` | in | 1, 1, 9, 16 | host-link request |
| `e_gnt`, `h_gnt` | out | 4, 1 | grant, combinational, same clock |
| `mem_en`, `mem_we`, `mem_addr`, `mem_wdata` | out | 1, 1, 9, 16 | to jspe_mem |

- `slot` = free-running 2-bit counter, 0 at reset, +1 every clock. Owner `o = slotmap[slot]`.
- `e_gnt[i] = e_req[i] & (o == i)`. `h_gnt = h_req & ~e_req[o]`. An unused slot serves the host and never another engine: an engine's grant times depend on the counter and the slot map only.
- SLOTMAP is a load-time setting: the link accepts a write only while all four engines are stopped and none has an injected instruction pending. Default: one slot per engine every 4 clk. `0x00` = solo (E0 every clock), `0x44` = E0/E1 alternate, `0x24`-style maps weight engines. An engine that owns no slot must stay stopped.
- One access per granted clock. Maximum wait for engine i = (largest gap between its slots) − 1: 3 clk default, 0 solo.

### 2.3 jspe_mem — program and table memory (D010, D015)
| Port | Dir | W | Meaning |
|---|---|---|---|
| `en`, `we`, `addr`, `wdata` | in | 1, 1, 9, 16 | sampled at the clock edge |
| `rdata` | out | 16 | word addressed in the previous clock, valid in the clock after `en & ~we` |

- Built (default, D131): IHP `RM_IHPSG13_1P_512x16_c2_bm_bist` behind this interface (BIST pins tied off, bit mask all ones), with a macro power grid on its 40 supply columns (D128–D130). Fallback `-DJSPE_MEM_FLOP` (D062/D063): 128x16 flops with registered read address; `addr[8:7]` ignored, so addresses alias modulo 128.
- Contents are undefined after reset and after deselect. No engine may run before the host has loaded it.

### 2.4 jspe_fifo — stream FIFO, depth 4 (x8)
| Port | Dir | W | Meaning |
|---|---|---|---|
| `wr`, `wdata` | in | 1, W | write; ignored when full |
| `rd` | in | 1 | pop; ignored when empty |
| `rdata`, `empty`, `full`, `free` | out | W, 1, 1, 3 | head entry and levels, registered |
| `flush` | in | 1 | synchronous clear (engine reset) |

- Per engine: RX (host → engine, W = 8) and TX (engine → host, W = 9; bit 8 marks an event code). Write and read in the same clock are both honoured, except at the limits, which follow the registered levels: a write to a full FIFO is ignored even if a pop frees an entry in that clock (an engine `push` then sets OVR), and a pop of an empty FIFO is ignored even if a write arrives in that clock (D046).
- Engine guards: `hrx` = RX not empty, `htx` = TX not full.

### 2.5 jspe_link — host link (D011, D016, D030, D031)
| Port | Dir | W | Meaning |
|---|---|---|---|
| `h_rx_s` | in | 1 | synchronized H_RX (= `lvl[3]`, unfiltered) |
| `h_tx`, `h_rdy` | out | 1, 1 | registered; the pad register in jspe_pins adds a second flop, so a link decision reaches the pad two clocks later (D078) |
| `rx_wr`, `rx_wdata`, `rx_free` | out, out, in | 4, 8, 4x3 | RX FIFO write side |
| `rx_ovf` | out | 4 | pulse: host byte dropped at a full RX FIFO (sets that engine's overrun flag) |
| `tx_rd`, `tx_rdata`, `tx_empty` | out, in, in | 4, 4x9, 4 | TX FIFO read side |
| `e_run`, `e_stop`, `e_step`, `e_rst` | out | 4 each | pulses to the engines |
| `e_exec`, `exec_word` | out | 4, 16 | inject one instruction into a stopped engine |
| `e_stat` | in | 4x16 | engine status words |
| `e_ovr`, `e_halt`, `e_trap` | in | 4 each | level flags; a rising edge queues a notice |
| `e_xm` | in | 4 | injected instruction pending (engine `xm`): `EXEC`, `RUN`, `STEP` to that engine are refused (HOSTIF 9, D077) |
| `e_clr` | out | 8 | pulses: clear overrun ([3:0]) and halt/trap ([7:4]) flags |
| `slotmap`, `filt` | out | 8, 16 | host registers |
| `pad_we`, `pad_idx`, `pad_wdata` | out | 1, 4, 7 | pad configuration write |
| `sig`, `sig_we`, `sig_wdata` | in, out, out | 8, 1, 8 | signal flags read / host write |
| `lvl` | in | 16 | for the PINS read-back register |
| `h_req`, `h_we`, `h_addr`, `h_wdata`, `h_gnt`, `mem_rdata` | | | memory client, same handshake as an engine |

Framing, commands and registers are in HOSTIF.md. Cap 24.0k µm² without the FIFOs (D079; was 1.0K cells, D016).

### 2.6 jspe_sig — inter-engine signal flags
Eight flags G0–G7. Inputs: `set`, `clr` (8 bits each) from every engine, `sig_we`/`sig_wdata` from the host. Per flag and clock: `G' = (G | S) & ~C` with S, C = OR over engines (clear wins over set); a host write replaces the result. Engines read the flags through pin binding (sources 16–23), so a flag has level, rise and fall like a pin, and all eight at once with `rcfg SIG`. There is no toggle request and no engine write of all eight (D048).

### 2.7 jspe_lfsr — shared 32-bit LFSR (D013)
State `LFSR[31:0]`, polynomial `POLY[31:0]`, `right` (form). Inputs per engine: `step`, `bit`, and a 16-bit configuration write port (halves of LFSR and POLY, LFCFG). One step: left form `fb = bit ^ LFSR[31]; LFSR' = (LFSR << 1) ^ (fb ? POLY : 0)`; right form `fb = bit ^ LFSR[0]; LFSR' = (LFSR >> 1) ^ (fb ? POLY : 0)`. In one clock any write (any index, any engine) blocks every step; of several writes the lowest-numbered engine's wins, of several steps the lowest-numbered engine's bit is used (D074, D084). Output `lfsr[31:0]` to all engines (`rcfg`).

## 3. Engine

### 3.1 Sub-blocks
| Module | Contents |
|---|---|
| `jspe_seq` | IR, HR, PF, PC, FP, L; memory request FSM; decode; guard mux; condition cells and watchers; event flags; flags Z, C; run/stop/step/exec control |
| `jspe_timer` | T, P, C, fraction accumulator, prescaler, sync rule; `tick`, `tock` |
| `jspe_shift` | S, bit counter N, taps (B, PAR, RUN, LV, LD), auto-transfer control |
| `jspe_alu` | R0–R2, 16-bit adder/logic/shift-by-one, operand and result muxes, memory address adder |
| `jspe_epins` | 4 logical pins: bind mux (32:1), invert, previous-level flop, rise/fall; output state `val`, `oe`, `fol` |

### 3.2 jspe_engine ports
| Port | Dir | W | Meaning |
|---|---|---|---|
| `rst` | in | 1 | global reset OR host engine reset |
| `run`, `stop`, `step`, `exec`, `exec_word` | in | 1, 1, 1, 1, 16 | host control (2.5) |
| `lvl`, `sig` | in | 16, 8 | pin levels, signal flags |
| `req`, `we`, `addr`, `wdata`, `gnt`, `mem_rdata` | out x4, in, in | 1, 1, 9, 16, 1, 16 | memory client |
| `rx_rd`, `rx_rdata`, `rx_empty`, `rx_ovf` | out, in, in, in | 1, 8, 1, 1 | host → engine stream |
| `tx_wr`, `tx_wdata`, `tx_full`, `tx_empty` | out, out, in, in | 1, 9, 1, 1 | engine → host stream; `tx_empty` only feeds EST bit 15 (D065) |
| `val`, `oe` | out | 4, 4 | pin state value and drive enable, registered (D081: the owner mux sits between this register and the pad register) |
| `sig_set`, `sig_clr` | out | 8 each | signal flag requests (one-hot from `sset`/`sclr`) |
| `lf_step`, `lf_bit`, `lf_we`, `lf_idx`, `lf_wdata`, `lfsr` | out x5, in | 1, 1, 1, 3, 16, 32 | shared LFSR |
| `stat`, `ovr`, `halted`, `trap`, `xm` | out | 16, 1, 1, 1, 1 | status to the link, registered (the link sees the state at the start of the clock, D077) |
| `clr_ovr`, `clr_ht` | in | 1, 1 | pulses from the link (`e_clr`): clear `ovr`; clear `halted` and `trap`. A clear wins over the engine's own set in that clock, which is lost (D084); `rx_ovf` wins over `clr_ovr` (D065) |

### 3.3 Sequencer state
| Register | W | Meaning |
|---|---|---|
| `run`, `xm`, `stepm` | 1, 1, 1 | running; executing one host-injected instruction; stop after the next completed instruction (`STEP`) |
| `IR`, `irv`, `PC` | 16, 1, 9 | armed instruction and its address (IR empty: the address being fetched for it) |
| `HR`, `hrv` | 16, 1 | other instruction of a micro-loop pair |
| `PF`, `pfv`, `FP` | 16, 1, 9 | prefetched successor; FP = its address (present or still to be fetched) |
| `L` | 9 | link register |
| memory FSM | 3 | `mo_v` (an `ld`/`st` is in progress), `fl_v`, `fl_ld` (a read was granted in the previous clock; it is a load). The request and its kind are functions of this state and IR (D068) |
| `wen`, `wprev` | 2, 2 | watcher enables; previous value of each condition cell |
| `F`, `D`, `ovr` | 10, 10, 1 | event flags, doubled bits, overrun (section 6) |
| `Z`, `Cy` | 1, 1 | ALU flags |
| `halted`, `trap` | 1, 1 | stopped by `halt` / by an undefined instruction |
| `waiting` | 1 | EST bit 10: the previous clock was a waiting clock (section 6) |

## 4. Cycle-level timing

### 4.1 Input and output latency
A pad level that changes during clock −1 is in FF1 during clock 0 and on `lvl` during clock 1. The engine's rise/fall pulse (`lvl` after bind and invert, compared with its previous-level flop) is true during clock 1. An armed guard fires in clock 1; the pin state changes at edge 2 and the pad register at edge 3 (D081).

| Path | Latency |
|---|---|
| pad in → fused pin action on pad out | 3–4 clk + pad (70–90 ns): the reaction floor |
| same with input filter on | 5–6 clk + pad |
| pad in → shift → pin in follow mode | 4–5 clk + pad (the pin state follows S one clock after it changes, the pad one clock later) |
| timer event → pad out | pad register changes one clock after the end of the tick/tock clock: cycle-exact, no jitter |

### 4.2 Memory slots and fetch (D018, D025, D027)
- An engine raises one request at a time. Priority inside the engine: data access of the executing `ld`/`st`, then IR refill, then prefetch into PF. A new read is requested only when none is in flight, except that a redirect retargets or replaces a pending or in-flight fetch in the same clock.
- A read granted in clock g is on `mem_rdata` during g+1 and captured at the end of g+1 into IR (if IR is empty or being emptied in g+1), else PF, or into the destination register of a `ld`. A fetched instruction is armed in g+2.
- IR refill (after a redirect, or when an instruction completes with PF empty and nothing in flight) is requested in the clock that causes it, so *request → armed* = 2 clk + slot wait = **2–5 clk** with the default map, 2 clk solo.
- Prefetch: whenever `pfv = 0`, no read is in flight and the engine is active, FP is requested. When IR completes and PF is valid (or its word arrives in that clock), the successor is armed in the next clock. This is guaranteed when the completing instruction was armed ≥ 4 clk (default map).
- Jump folding: an unconditional `jmp` that is in PF is never executed. In the clock it is valid in PF the fetcher sets `FP ← target`, clears `pfv` and requests the target. A `jmp` that arrives while IR is empty goes to IR and executes there with the same timing. Either way an unconditional jump costs one fetch and no execute clock: its target is requested in the clock after the `jmp` arrives. The successor of an `ld`/`st` is never prefetched (the data access has priority, and no read is requested in a clock in which a read arrives), so a `jmp` behind an `ld`/`st` is never in PF: it is fetched into the empty IR after the access completes and executes there (D070). With the default map a guarded instruction that waits ≥ 9 clk therefore has the instruction *behind* a following `jmp` armed 1 clk after it fires. A `jmp` to itself is folded without end: the fetcher uses every slot of the engine and IR stays empty; a program parks with `@never : nop`.
- Redirect (taken jump, `call`, `ret`, watcher, loop corner cases of 4.4): `irv, pfv, hrv ← 0`, any read in flight is discarded, `PC ← target`, `FP ← target + 1`, target requested in the same clock.

Fire of instruction X in clock f → next instruction armed:

| Case | Armed in clock |
|---|---|
| successor valid in PF, or arriving at the end of f | f + 1 |
| successor read granted in g ≥ f | g + 2, i.e. f + 2 … f + 5 |
| taken jump, `call`, `ret`, watcher vector | g + 2, i.e. f + 2 … f + 5 |
| micro-loop continues (same instruction or pair partner) | f + 1 |
| `ld`: completes in g + 1, data usable in g + 2 (g = grant of the data access, f ≤ g ≤ f + 3) | f + 2 … f + 5 |
| successor of `ld`: requested in g + 2, armed 2 clk after its own grant | g + 6 = f + 6 … f + 9 (solo: g + 4) |
| `st`: written in g (f ≤ g ≤ f + 3) | f … f + 3 |
| successor of `st`: requested in g + 1, armed 2 clk after its own grant | g + 6 = f + 6 … f + 9 (solo: g + 3) |
| j words in a row that could not be prefetched | ≤ f + 4j + 1 |

Worked example, engine 1 (slots in clocks ≡ 1 mod 4), X = taken jump firing in clock 2:
```
clock      2      3      4      5      6      7      8      9     10     11
slot       2      3      0      1      2      3      0      1      2      3
          X fires, req(target) ......  gnt    RD   Y armed (may fire in 7)
                                               req(Y+1) ........  gnt    RD   → PF valid in 11
```
Unguarded straight-line code runs at one instruction per slot period (4 clk default, 2 clk solo).

### 4.3 Execution classes
| Class | Clocks from fire to completion |
|---|---|
| E (pin/shift instant), JP, JF, GM, IM, RX register ops, `wcfg`/`rcfg`, `jmp`, `call` | 1 (effects at the end of the fire clock) |
| `ld` | completes one clock after its grant (2–5 clk) |
| `st` | completes in its grant clock (1–4 clk) |

All architectural effects of `ld`/`st` (destination write, base write-back, memory write) happen in the completion clock. A watcher that fires in any clock up to and including the completion clock cancels the instruction with no effect: no destination write, no write-back, no memory write (D046).

### 4.4 Micro-loop (D024)
E-format instructions carry a 2-bit loop field: `rep` (repeat self), `head`, `tail` (pair). The loop is governed by the bit counter N of the shifter: it ends in the clock of the shift that completes a word (`wend`), unless that word is handed over by auto-transfer (section 8). An instruction that does not shift never ends the loop.

| Instruction fires | Next armed (clock f + 1 unless noted) |
|---|---|
| `rep`, no end | same instruction |
| `head`, no end, `hrv = 0` (first pass) | `HR ← IR`, `hrv ← 1`, IR ← tail from PF (ordinary advance) |
| `head`, no end, `hrv = 1` | IR ↔ HR |
| `tail`, no end, `hrv = 1` | IR ↔ HR |
| `tail`, no end, `hrv = 0` (entered at the tail) | redirect to PC − 1 (the head); 2–5 clk |
| `rep` or `tail` with loop end | ordinary advance to PC + 1 (PF) |
| `head` with loop end, `hrv = 1` | ordinary advance: PF holds the word after the tail |
| `head` with loop end, `hrv = 0` | redirect to PC + 2 (skips the tail); 2–5 clk |

While a pair is loaded (`hrv = 1`) FP = address after the tail, so PF holds the loop successor. `hrv` is cleared by every loop end and every redirect. Under host exec the loop field is ignored.

### 4.5 Timelines used by the paper programs (default slot map)
| Path | Bound |
|---|---|
| UART RX: stop-bit sample → start-edge guard armed | 1 clk when the bit time ≥ 13 clk (`push` placed before the stop wait, `jmp` folded during it) |
| UART TX: stop tick → start-bit instruction armed | ≤ 10 clk (`jrx`, `pull`, guard): exact and back to back for bit time ≥ 11 clk |
| SPI controller: bit loop; first launch after `tsync` | held pair, 1 clk; ≤ 5 clk (SCK period ≥ 6 clk in the default map, 4 clk solo) |
| SPI target: last command bit sampled (clock t) → S holds table word | ≤ t + 6 (clock in which the follow pin's state loads it), pad changes at edge t + 8: 9–10 clk + pad after the sampling edge at the pad |
| SPI target: same → sampler armed for the next byte | ≤ t + 10 |
| I2C target: SCL fall → SCL hold on the pad; → ACK or data on SDA | 3–4 clk + pad (fused to the armed guard); THD + 3…4 clk + pad (timer one-shot started by the same fall, D049) |
| I2C controller: SCL read high → next guard armed | ≤ 14 clk (`ret`, `shin`, guard); phases ≥ 16 clk |
| WS2812: third tick of a bit → first tick instruction of the next byte armed | ≤ 14 clk (tick spacing 21) |
| Manchester TX: last mid-bit edge → first instruction of the next byte armed | ≤ 10 clk (half bit ≥ 11 clk) |
| Manchester RX: sample → next sample armed | ≤ 10 clk while hunting, ≤ 14 clk at the delimiter (bit time ≥ 15 clk) |

## 5. Per-clock rule list (one engine)

Evaluated in this order; "this clock" = c.

1. **Reset.** `rst` → state of section 9.1; nothing else applies.
2. **Events of the clock.** Pin `lvl/rise/fall` (3.1), `tick/tock` (section 7), `hrx/htx`, condition cells `cc0/cc1` (ISA.md 6).
3. **Activity.** `active = run | xm`. If not active: nothing fires, no request is raised, `F, D ← 0`; continue at 9.
4. **Watchers** (only if `run & ~xm`): watcher k fires if `wen[k]`, its mode is abort or call, and `cc_k & ~wprev[k]`. If both fire, watcher 0 wins and watcher 1 is lost for this occurrence.
5. **Fire.** `fire = irv & guard(IR) & ~watcher & ~(memory instruction in progress)`.
6. **Effects.**
   - Watcher: redirect to its vector. Call mode: `L ← PC` first (PC = the preempted or next instruction, which is executed again after `ret`). Abort mode: `F, D ← 0`. An `ld`/`st` in progress is cancelled (a store granted in this clock is not written).
   - Fire: the operation of ISA.md. One instruction fires per clock.
   - `ld`/`st` in progress: completes per 4.3.
   - Undefined instruction armed: `trap ← 1`, `run ← 0`; injected by `EXEC` it also ends the injection (`xm ← 0`, `irv ← 0`, PC unchanged, D086); nothing else changes.
7. **Next instruction.** Redirect, loop rule (4.4), or advance: `IR ← PF` (or the word arriving this clock), `PC ← FP`, `FP ← FP + 1`; if neither is available `irv ← 0` and IR refill is requested. `PC ← FP`, `FP ← FP + 1` happen on every advance, also when no word is available: PC is the address of the instruction in IR or, while IR is empty, of the word being fetched for it (a prefetch of that address that is pending or in flight becomes the IR refill). The watcher call (`L ← PC`) and `EST` use this PC; under `EXEC` PC keeps the address of the stopped program (D046). In step or exec mode `run`/`xm` are cleared when an instruction completes; after exec `irv ← 0` and PC is unchanged unless the instruction was a jump.
8. **Event flags.** Section 6.
9. **Fetch control.** Request per 4.2; fold a `jmp` in PF.
10. **Datapath updates** with the values decided above: timer, shifter and taps, registers, pin state, FIFO strobes, signal and LFSR requests.
11. `wprev ← cc` (every clock, also when stopped).

Same-clock interactions between engines: signal flags (2.6), LFSR (2.7). Nothing else is shared; memory is separated by slots and pads by ownership.

## 6. Events: latched, consumed, overrun (D019)

Ten events per engine: rise and fall of p0–p3, tick, tock. Each has a flag `F` and a doubled bit `D`.
- **Waiting clock**: active, `irv`, guard false, no memory instruction in progress, no watcher. All `F, D ← 0`. The armed guard sees its event live.
- **Busy clock**: any other active clock (firing, IR empty, memory access, watcher). For every event e: if it is consumed in this clock → `ovr |= D[e]`; `F[e] ← F[e] & live[e]`; `D[e] ← 0`. Otherwise if `live[e]`: `F[e]` set, and `D[e]` set if `F[e]` was already set.
- An edge or timer guard is true when `live | F`. It consumes its event when its instruction fires. Guards `always`, `hrx`, `htx`, `c0`, `c1`, `never` consume nothing; `c0`/`c1` are levels and are not latched.
- `tsync`, `tset` and an automatic timer sync clear `F, D` of tick and tock, including a tick or tock that is live in the clock of the start (the clear wins; condition cells and watchers still see that live pulse). A watcher abort clears all, including events live in the abort clock. Not active: all clear (D046).
- `ovr` is sticky. It is also set by: a host byte arriving at a full RX FIFO; `push`/`evt` on a full TX FIFO; `pull` on an empty RX FIFO. It is cleared by `ovclr`, engine reset, or the host; an `ovclr` whose own guard consumes a doubled flag leaves it set, and a host clear wins over the engine setting it in the same clock (D065, D084). Its rising edge queues a notice to the host.
- Sampling instructions read the live level in the clock they fire, also when they fire late from a flag.

Consequence: an instruction fires at the later of "event happened" and "armed". The static bound of ISA.md 9 proves the guard is armed first; `ovr` is the run-time witness when a whole event was missed.

Rule for programs: a guard that fires in its first armed clock has had no waiting clock, so it also sees flags latched while the engine was busy, including events the program did not mean to answer (an SCK edge while CS was high, a tick of a free-running timer). Where that matters the guard needs one waiting clock, or the timer is restarted first.

## 7. Timer (D012, D028)

State: `T`, `P`, `C` (16 bit), fraction accumulator `FA` (8), prescale counter `PSC` (8), `trun`, `tone` (one-shot), `skip`. Configuration TCFG: `frac`, `pre`, `sedge`, `spin`, `swin` (ISA.md 5).
- Step enable `st`: every clock (`pre` = 0), every 16th (1) or every 256th (2), counted by `PSC` from the last start.
- Per clock with `trun & st`: if `skip` → `skip ← 0`; else if `T = 0` → **tick**; one-shot: `trun ← 0`; periodic: `T ← P`, `{carry, FA} ← FA + frac`, `skip ← carry`; else `T ← T − 1`.
- **tock** = `trun & st & ~skip & (T = C)`.
- Starts (at the end of clock k): `tsync` → `T ← P`, periodic. `tset r` → `T ← r`, one-shot. Both clear `FA`, `PSC`, `skip` and set `trun`. `tstop` clears `trun`. A start (instruction or automatic sync) replaces the count step of its clock (decrement, reload, one-shot stop); the tick and tock of that clock are still reported.
- Automatic sync on the selected edge of logical pin `spin` (live pulse, evaluated every clock): `swin = 0` → same as `tsync` on every such edge (hard sync). `swin = 1` → only if the timer is stopped or already past tock (`T < C`): `T ← P`, one-shot (an edge-started, non-retriggerable window). An instruction start in the same clock wins; an automatic sync in the clock a `tstop` fires restarts the timer (D084).
- Resulting timing with `pre = 0`: after a start in clock k with value V, tick is in clock k + V + 1 and tock in clock k + V − C + 1. Periodic period = P + 1 + frac/256 clk on average.
- Idiom used by the programs: *tock = end of a phase, tick = deadline*. A phase is started with `tset (C + length − 1)`; normal flow restarts the timer before it reaches 0; a tick therefore means the phase overran and is routed to a watcher.
- The timer keeps running while its engine is stopped.

## 8. Shifter, taps, pins (D013, D026, D029)

- `S` holds a right-aligned word of WL = 1…16 bits; bits above the word are zero after any shift. MSB-first: out tap `S[WL−1]`, new bit enters `S[0]`. LSB-first: out tap `S[0]`, new bit enters `S[WL−1]`.
- Bit counter N: 0 at a word boundary; a shift sets `N ← WL − 1` if it was 0, else `N − 1`. `wend` = the shift that makes a word complete.
- One shift per instant at most. Order inside an instant: (1) a sampling shift `shin` takes the input pin, (2) pin actions, data actions seeing S *after* (1) and after an auto-pull, (3) the shift of an `out` action.
- Taps on the shifted bit: `B` (last bit), parity `PAR` (restarts at each word), run length `RUN` with limit, optional differential decode/encode, optional step of the shared LFSR. A sample without a shift (`smp`) updates B, RUN and the differential state and adds its bit to PAR; it leaves S, N and the LFSR alone. `mis` = input-pin level ≠ last data bit this engine drove.
- Auto-transfer at `wend` (configuration bits `apush`, `apull`): if every enabled transfer is possible (TX FIFO not full / RX FIFO not empty) the completed word's low byte is pushed and/or S is loaded with the next host byte in that same clock and a micro-loop continues; otherwise nothing is transferred and the loop ends as usual.
- Logical pin output state: `oe`, `val`, `fol`. With `fol` = 1 the pin value is the current out tap of S, sampled every clock. Pad function: 2.1.
- Exact definitions: ISA.md 4–7.

## 9. Reset, start, stop, reload

### 9.1 Reset values
`rst_n` low for ≥ 2 clk (synchronized) resets everything below; the host engine reset `RST e` does the same for one engine and its two FIFOs.

| State | Value |
|---|---|
| Engines | stopped; `irv, pfv, hrv, xm, halted, trap, ovr` = 0; PC, L = 0, FP = 1 (= PC + 1, D109); `F, D` = 0; `wen` = 0; Z, Cy = 0 |
| Engine configuration | SHCFG = 0x0007 (8-bit words, MSB first, input p0), all other configuration registers 0; N = 0; taps 0 |
| Pin state | `oe, val, fol` = 0 for all logical pins |
| Timer | stopped; `FA, PSC, skip` = 0 |
| Data registers R0–R2, S, P, C, T; memory; LFSR and POLY | **not reset**, undefined until written |
| Pads | PAD[j] = 0 → `uo_out` = 0 except H_TX = 1 and H_RDY = 0 (ready); `uio_oe` = 0 |
| Link | bit time 16 clk, parser idle, no stream selected, SLOTMAP = 0xE4, FILT = 0, signals = 0 |
| FIFOs | empty |

### 9.2 Host control of an engine
- `STOP`: `run ← 0` at the end of the clock. Nothing else changes: IR stays armed, pins hold, the timer runs. `RUN` resumes exactly there. A pending injected instruction is cancelled without effect, including its memory access while it waits for a slot and an `ld` granted in that clock: `xm ← 0`, `irv ← 0` (D045, D069). Two one-clock cases are defined behaviour (HOSTIF 3, D086): an injected instruction that fires in the STOP clock completes, and an injected `st` granted in that clock is written.
- Memory accesses across `STOP`: an access that has been granted finishes whether or not the engine is active (a read is captured, an `ld` or `st` completes). A request that has not been granted is withdrawn while the engine is not active and raised again after `RUN` (D046).
- `STEP`: runs until one instruction completes, then stops. A trap, `STOP` or `RUN` ends step mode (D069).
- `EXEC w` (engine stopped): `IR ← w`, `irv ← 1`, `xm ← 1`, `FP ← PC + 1`; PF, HR and a read in flight are discarded (a loaded loop pair is entered again from PC, D069). An `ld`/`st` of the stopped program that was still waiting for its slot is dropped without effect; it executes again when the program is refetched from PC (D067). The instruction executes with normal semantics, including guard waits and memory access. On completion `xm ← 0`, `irv ← 0`; an undefined injected word traps and also ends the injection (`xm ← 0`, `irv ← 0`, PC unchanged, D086). A jump sets PC; anything else leaves PC unchanged, so the stopped program is refetched from PC on `RUN`. `EXEC` to a running engine is ignored and reported as a link error; so are `EXEC`, `RUN` and `STEP` to an engine whose injected instruction is still pending (`xm = 1`).
- Start of a program = `RST`, injected initialisation instructions, injected `jmp entry`, `RUN`. Several engines can be started in the same clock with one `RUN` mask.
- `halt` instruction: `run ← 0`, `halted ← 1`, notice to the host. Undefined instruction: `trap`.

### 9.3 Reload and deselect
The design keeps no state across deselect or reset. Reload is always the full sequence of HOSTIF.md 6 (reset or `RST`, write memory, read back, pad configuration with `en` = 0, injected initialisation, `en` = 1, `RUN`). A reload of one engine while others run is allowed: host memory writes use free slots and do not disturb running engines; the host must not overwrite words a running engine uses.

## 10. Memory map

| Space | Size | Contents |
|---|---|---|
| Program/table memory | 512 x 16 (SRAM build, D131; 128 x 16 aliased modulo 128 on the flop fallback) | instructions, `.word` tables and buffers of all engines, no fixed regions and no protection. Any engine can `ld`/`st` any word. Addresses 9 bit, wrap modulo 512 (128 on the flop fallback). |
| Engine registers | 8 per engine | R0, R1, R2, S, P, C, T, L (ISA.md 3) |
| Engine configuration | index 0–8 | SHCFG, TAPCFG, TCFG, BIND01, BIND23, CC0, CC1, WV0, WV1 (`wcfg`) |
| Shared configuration | index 16–21 | LFSR_LO, LFSR_HI, POLY_LO, POLY_HI, LFCFG (`wcfg`/`rcfg`), SIG (`rcfg` only) |
| Host registers | 8-bit address | ID, LINK, SLOTMAP, SIG, FILT, PINS, ECLR, EST0–3, PAD[0–15] (HOSTIF.md 5) |

Initialisation code does not occupy memory: the assembler emits it as injected instructions (D030). The assembler places code and tables; convention for multi-engine images: one contiguous block per engine, tables after code.

## 11. Area budget (re-baselined 2026-11-16, D051; all measured 2026-11-18)

Limit: placed standard-cell area (+ SRAM macro with its halo, SRAM build only) ≤ 60 % of the core area. Core area as reported by the GDS flow (`design__core__area`, run 43845628155): 1 208 173 µm² (die 1724.16 x 710.64 µm, core 2.88…1721.28 x 3.78…706.86), so the limit is ≈ 724.9k µm² (D063; 60 % is also the template's `PL_TARGET_DENSITY_PCT`). Cell area = what `make area LIB=<sg13g2 liberty>` reports for the synthesized netlist; placed = synthesized x 1.56, measured on the full top of the flop build, now the fallback (run 44246474825: 445.5k µm² synthesized → 697.1k final std-cell area = 57.7 %, incl. 6 627 timing-repair, 4 242 hold and 445 clock buffers; D083); the placed rows below use x 1.56. It replaces x 1.42 from the Phase 5 build (D064), which had far fewer long nets and hold buffers. Every row is measured since the full-top integration (2026-11-18). Cell counts go into the ledger but are not a criterion.

The SRAM build is the shipped build (D131): run 45553919240 placed 457.9k µm² including the macro = 37.9 %. The estimates below predate it; the flop build (D062, run 44625407065, 57.3 %) is the fallback.

| Block | Cells | Cell area µm² | Cap µm² | Source |
|---|---|---|---|---|
| `jspe_seq` | 1 243 | 18 889 | 19 500 | measured (D070) |
| `jspe_timer` | 504 | 8 192 | 8 400 | measured |
| `jspe_shift` | 453 | 6 621 | 6 800 | measured |
| `jspe_alu` (R0–R2, 12 operations, operand and result muxes, shared adder) | 1 099 | 12 374 | 12 700 | measured |
| `jspe_epins` | 294 | 5 089 | 5 250 | measured (after D081) |
| engine glue (stream data mux, signal and LFSR requests, status) | 98 | 815 | 850 | measured |
| **`jspe_engine`** | **3.69K** | **52.0k** | **53.5k** | measured 2026-11-17; cap unchanged |
| x 4 | 14.8K | 208.0k | 214.0k | |
| `jspe_link` | 1 513 | 23 814 | 24 000 | measured in the top (D079; 23 770 alone) |
| `jspe_fifo` x 8 | 1 156 | 24 929 | 28 500 | measured (4x8: 138 cells / 2 959, 4x9: 151 / 3 273) |
| `jspe_pins` | 728 | 17 625 | 22 000 | measured in the top (17 545 alone) |
| `jspe_arb` | 150 | 1 612 | 2 800 | measured |
| `jspe_lfsr` | 366 | 6 310 | 7 500 | measured |
| `jspe_sig`, top, reset | 81 | 992 | 2 200 | measured (sig 862, top 131) |
| **Logic, synthesized** | **18.7K** | **283.4k** | **301.0k** | measured, full top 2026-11-18 (after D081) |
| Logic, placed (x 1.56, D083) | | 442.1k | 469.6k | |
| SRAM macro 45.3k µm² + 35 % halo | 0 | 61.2k | 61.2k | D010 |
| **Total, SRAM build** | | **503.3k (41.7 %)** | **530.8k (43.9 %) ≤ 725k** | |
| Fallback memory instead of the macro (synth 162.1k), placed | 5 003 | 252.9k | 252.9k | measured |
| **Total, flop build (fallback, D062)** | 23.7K (synth 445.5k) | **695.0k (57.5 %)** | **722.5k (59.8 %) ≤ 725k** | `make area` full top; flow final 697.1k (57.7 %, run 44246474825) |

- Proxy = scratch RTL written in Phase 3 from sections 7–8 and ISA.md 7–8, mapped with the `make area` flow (yosys `synth`, `abc`, typical liberty, default script). Real RTL is expected at or below it. Scaled = the 2b estimate times the ratio measured on the four engine proxies (2.09); it is replaced by the first measurement of the real block. All figures are ±25 % until then.
- Flops per engine: 378 measured (sequencer 160, timer 82, shifter 46, R0–R2 48, pins 42).
- Engine caps re-split as measured on 2026-11-17 (D070); the engine cap of 53.5k and x 4 ≤ 214.0k are unchanged. The earlier split (seq 22.0k, alu 11.5k, shift 5.5k, glue 0.5k) came from proxies that placed the operand muxes and the decode differently. Phase 8 area lever, not used now: the 8:1 muxes on both ALU operands, the write-back port on R0–R2 and S and the second tap mux of the shifter (≈ 2.6k µm² per engine above the proxies); options are D051 (a) (ALU destination limited to R0–R2 and S) and decoded strobes from `jspe_seq` instead of a local decode per block (`synth -flatten` measures ≈ −0.9k per engine).
- The ISA trim of D048 measured on the ALU proxy: 938 → 935 cells, 11 113 → 10 852 µm². It simplifies the ISA more than it saves area.
- If the Phase 5 hardening exceeds the limit, in this order (D051): (a) further ISA trims (docs/history/REVIEW.md 5: ALU destination limited to R0–R2 and S, then `adc`, `halt`); (b) the memory is not an area measure: the flop fallback replaces the macro only if the macro fails precheck, and it costs 191.7k µm² placed more than the macro; (c) three engines as the last resort (−81.1k µm² placed).

## 12. Timing paths (slow corner estimates, budget 20 ns)

| Path | ns |
|---|---|
| P1 fetch: SRAM 6.3 → IR/PF/data mux 0.5 → setup 0.2 | ≈ 7 |
| P2 execute: IR 0.45 → operand mux 0.9 → 16-bit add 5.5 → result mux 0.9 → setup 0.3 | ≈ 8 |
| P3 reaction: sync or signal flag 0.45 → bind mux 1.4 → edge 0.3 → guard mux 0.9 → fire 0.6 → shift 0.5 → out tap 1.0 → pin value 0.5 → pin state register 0.2 (D081) | ≈ 5.9 (≈ 15–16 slow corner with wires, see note) |
| P3b pad: pin state register 0.45 → wire to jspe_pins → owner mux 1.5 → inv / mode 0.4 → pad register 0.2 | ≈ 2.6 + wire |
| P4 address: IR 0.45 → guard + condition 1.9 → target adder 2.5 → address mux 0.45 → slot mux 1.5 → SRAM setup 0.85 | ≈ 7.7 |
| P4b table address: register 0.45 → mux 0.9 → 9-bit add 3.0 → address mux, slot mux 1.95 → setup 0.85 | ≈ 7.2 |
| P5 auto-pull: FIFO head 0.45 → S next 0.5 → out tap 1.0 → pin value 0.5 → owner mux 1.5 → setup 0.2 | ≈ 4.2 |
| P6 reaction to address: sync 0.45 → bind mux 1.4 → edge 0.3 → guard mux 0.9 → fire 0.6 → address mux 0.45 (target adder in parallel) → slot mux 1.5 → SRAM setup 0.85 | ≈ 6.5 |
| P7 watcher cancel: sync 0.45 → bind mux 1.4 → edge 0.3 → cell select and LUT 1.9 → watcher 0.6 → request and write-enable gate 0.6 → slot mux 1.5 → SRAM setup 0.85 | ≈ 7.6 |
| P8 store completion: sync 0.45 → bind mux 1.4 → edge 0.3 → guard mux 0.9 → fire 0.6 → request 0.6 → slot mux and grant 1.5 → `st_done`, advance 0.6 → IR / PC / FP muxes and register write enables 1.4 → setup 0.3 (estimate, D070) | ≈ 8.1 |

The SRAM output feeds only IR, PF and register inputs, never the pads or the address. Measured (GDS run 44184771880, slow corner): the old P3 (pin value straight into the owner mux) arrived at 21.65 ns, WNS −1.17 ns at 504 endpoints; about 7 ns of it were three long, weakly driven nets (signal flag fanout, inside the engine, engine → jspe_pins), so the estimates above are logic only. D081 cut P3 at the engine's pin state register. By the logic estimates 50 MHz has ≥ 10 ns of slack on every path; the sign-off figure is the flow's slow corner (PROGRESS ledger); 60 MHz (16.7 ns) remains a stretch goal (D007).

## 13. Results of the 2b evaluations

- **Data path width (D022): 16 bit.** An 8-bit path saves ≈ 290 synth cells per engine (S 45, R0–R2 50, ALU and muxes 195) = 1.45K placed, 7 % of the total. It costs: two registers and extra words for every timer constant and table pointer (9-bit addresses), no 9-bit UART or 9-bit I2C word in one shift, no 16-bit SPI word, CRC-16 compare in two halves; the I2C controller grows from 39 (41 since D042/D043) to ≈ 50 words. Fmax is no argument since D018 (execute path ≈ 8 ns either way). The total fits with the 16-bit path (64 % at the caps of the 2b budget; current figures in section 11).
- **Shift-on-edge assist (D020 → D026).** The micro-loop already shifts on an edge with no fetch; the limits were the pad round trip and the word boundary. Adopted: (a) the *follow* pin state and the sample-then-drive order, so a target can launch from the sampling edge with a full-period budget; (c) *auto-transfer* at the word boundary, so a streaming micro-loop never leaves the engine. Cost ≈ 35 cells per engine, 140 in total, nothing SPI-specific: both serve any shifted stream (controller, target, timer-paced line codes). Rejected: (b) a separate autonomous shifter armed by one instruction (≈ 60–75 cells per engine, a second mechanism for what the micro-loop does, and S would change under a running instruction). Resulting SPI target rates: 4 MHz with command-dependent lookup in the default slot map, ≈ 6 MHz in solo; streaming 6.25 MHz with follow, 8 MHz class with the fused form.
- **Solo mode (D021 → D027).** Adopted as the 8-bit SLOTMAP register rather than a single bit: ≈ 30 cells, one 4:1 mux on the grant path (+0.5 ns on P4), load-time only. Solo gives request → armed in 2 clk and unguarded code at 2 clk per instruction; two-engine maps give 3 clk. The fetch bound stays static per configuration: one access 2 + (largest slot gap − 1); chained accesses as ISA.md 10 (D054).
- **Prefetch depth (D025): stays 1.** With jump folding, loop bits inside the instruction and the follow state, every MUST path has its guard armed in time (4.5). A second buffer entry (≈ 45 cells per engine) would only improve SPI target multi-byte reads at 4 MHz in the default map, which solo already covers.
