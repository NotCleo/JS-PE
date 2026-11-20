# ISA — Cleo REFLEX instruction set (Phase 2b, 2026-11-15)

Machine-readable twin: `isa.yaml` (assembler, ISS and `src/jspe_isa.vh` are generated from it; if the two disagree, `isa.yaml` wins and this file is fixed).
Timing, slots, prefetch and the per-clock rule list: ARCH.md 4–6. Notation: `<-` is an update at the end of the clock in which the instruction fires; `sext`/`zext` extend to 16 bits; addresses are 9 bits and wrap.

## 1. Execution model in one paragraph

Each engine holds one armed instruction. An instruction is `guard : operation`: it waits until its guard is true, performs the whole operation in that clock, and the next instruction is armed 1 clk later if it was prefetched (else 2–5 clk). Formats without a guard field act as `always`. Two watchers per engine can preempt any wait. Pin edges and timer events that occur while the engine is busy are latched and answered late (ARCH.md 6).

## 2. Formats

```
bit      15 14 13 12 11 10  9  8  7  6  5  4  3  2  1  0
E         0 [ pa ] [  aa  ] pb [ ab ]  s [ rr ] [  guard  ]   pin/shift instant
JP        1  0 lv [ pin] [       rel7       ] [  guard  ]   jump on pin level
JF        1  1  0  0 [    cond     ] [       rel7       ]   jump on flag
GM        1  1  0  1 [sub ] [       x6       ] [  guard  ]   move, stream, timer, control, event
IM        1  1  1  0 [ op ] [ rd ] [         imm8         ]   immediate
RX        1  1  1  1 [               x12                 ]   ALU, memory, configuration, far jump
```
The guard is always bits [3:0]. JF, IM and RX never wait.

| RX sub-format | Bits [11:0] |
|---|---|
| ALU | `00 op[3:0] rd[2:0] rs[2:0]` |
| `jmp` absolute | `010 addr[8:0]` |
| `call` | `011 addr[8:0]` |
| `ld` / `st` | `100 w reg[2:0] rb[1:0] mode[1:0] 0` (w = 1 for `st`) |
| `wcfg` / `rcfg` | `101 r creg[4:0] reg[2:0]` (r = 1 for `rcfg`) |
| undefined | `110 …`, `111 …` |

## 3. Registers and flags

| Code | Name | W | Meaning |
|---|---|---|---|
| 0–2 | R0, R1, R2 | 16 | general |
| 3 | S | 16 | shifter (section 7) |
| 4 | P | 16 | timer period / reload |
| 5 | C | 16 | timer compare (tock) |
| 6 | T | 16 | timer count, read-only (a write is ignored) |
| 7 | L | 9 | link register, reads zero-extended |

IM and memory-base fields are 2 bits: 0–3 = R0, R1, R2, S.

| Flag | Set by | Meaning |
|---|---|---|
| Z, CY | ALU and IM `add`/`cmp` | zero, carry (section 8.5) |
| B | every tapped bit | last bit shifted or sampled |
| PAR | shifts, `smp` | parity of the current word plus any `smp` bits, starts at `podd` |
| RUN | every tapped bit | run-length condition |
| N | shifts | bits left in the current word; condition `w` = N ≠ 0 |
| OVR | ARCH.md 6 | sticky overrun |
| MIS | live | input-pin level ≠ last data bit driven by this engine |

## 4. Guards

| Code | Syntax | True when |
|---|---|---|
| 0 | (none) or `@always` | every clock |
| 1 | `@tick` | timer reached 0 (latched event) |
| 2 | `@tock` | timer equals C (latched event) |
| 3 | `@hrx` | host → engine FIFO not empty (level) |
| 4 | `@htx` | engine → host FIFO not full (level) |
| 5, 6 | `@c0`, `@c1` | condition cell output is 1 (level, not latched) |
| 7 | `@never` | never; only a watcher or the host ends the wait |
| 8–11 | `@rise p0` … `@rise p3` | rising edge of the logical pin (latched event) |
| 12–15 | `@fall p0` … `@fall p3` | falling edge (latched event) |

**Stale events (D055).** Edge and timer events are latched while the engine is busy and cleared only by a waiting clock (ARCH.md 6). A guard that is true in its first armed clock therefore fires on an event that happened before it was armed, including one the program did not mean to answer (an SCK edge while CS was high, a tick of a timer left running). Rule: when a program starts a new wait whose earlier events are meaningless, it first gives the engine one waiting clock after the moment from which events count. Idiom (+2 words, R = a register holding V):
```
        @fall p1 : p2 <- f      ; the moment from which events count
        tset  R                 ; one-shot: tick V + 1 clk later
        .deadline V             ; @tick armed <= V clk after tset executes: >= 1 waiting clock
        @tick : nop
        loop { @rise p0 : shin }
```
V ≥ G + 1 of the slot map (5 default, section 10): `tset` is unguarded, so its successor is counted as not prefetched and is armed ≤ G + 1 clk after it; the `.deadline V` after `tset` proves the `@tick` guard is armed in time (D100). An event that must count (the first data edge) has to arrive after the tick. Alternatives: a watcher abort at that moment (clears all events, costs a redirect), or a guard that is known to wait (an edge that cannot occur yet).

## 5. Configuration registers

Written with `wcfg creg, rs` (16 bits from a register), unused bits ignored. Reset values 0 unless noted.

| Idx | Name | Fields |
|---|---|---|
| 0 | SHCFG | [3:0] `wl` = word length − 1; [4] `lsb` (1 = LSB first); [6:5] `inpin` (logical pin sampled by `shin`/`smp`); [7] `inv` (invert received and transmitted data bits); [8] `diff`; [9] `apush`; [10] `apull`. Reset 0x0007. A write also performs `wrst`. |
| 1 | TAPCFG | [1:0] `alt` (source of the `q` action: 0 PAR, 1 inverted data bit, 2 stuff bit, 3 B); [2] `podd` (parity start value); [3] `lfen` (shifted bits step the shared LFSR); [6:4] `rl` (run limit, 0 = off); [7] `r1` (count ones only) |
| 2 | TCFG | [7:0] `frac`; [9:8] `pre` (0: /1, 1: /16, 2: /256, 3: as 0); [11:10] `sedge` (0 none, 1 rise, 2 fall, 3 both); [13:12] `spin`; [14] `swin`. Timer: ARCH.md 7 |
| 3 | BIND01 | [4:0] p0 source, [5] p0 invert, [12:8] p1 source, [13] p1 invert |
| 4 | BIND23 | same for p2, p3 |
| 5, 6 | CC0, CC1 | [3:0] `selA`, [7:4] `selB`, [11:8] `lut`, [13:12] `mode` (0 off, 1 abort, 2 call, 3 as 0) |
| 7, 8 | WV0, WV1 | [8:0] watcher vector |
| 16, 17 | LFSR_LO, LFSR_HI | shared LFSR state halves (readable with `rcfg`) |
| 18, 19 | POLY_LO, POLY_HI | shared polynomial halves |
| 20 | LFCFG | [0] `right` (1 = right-shifting, reflected form) |
| 21 | SIG | [7:0] signal flags G7…G0, read-only (`rcfg`). `wcfg SIG` is undefined (trap); the flags are changed with `sset`/`sclr` (D048) |

Indices 9–15 and 22–31: writes ignored, reads 0. `rcfg` returns 0 for write-only registers (0–8, 18–20).

**Pin binding.** Logical pin level = source ^ invert. Sources: 0–7 `ui_in[0..7]`, 8–15 `uio_in[0..7]`, 16–23 signal flags G0–G7, 24 constant 0, 25 constant 1, 26–31 read 0. Each logical pin has a previous-level flop: `rise = level & ~prev`, `fall = ~level & prev`. No edge is reported in the first clock after a write to the pin's BIND register.

## 6. Condition cells and watchers

Cell k output: `cc_k = lut[{b, a}]` where a, b are the signals selected by `selA`, `selB`:

| Sel | Signal | Sel | Signal |
|---|---|---|---|
| 0–3 | level p0–p3 | 12 | tick (live pulse) |
| 4–7 | rise p0–p3 (live pulse) | 13 | tock (live pulse) |
| 8–11 | fall p0–p3 (live pulse) | 14 | MIS |
| | | 15 | hrx |

`lut` examples: `0xA` = a, `0x8` = a & b, `0xE` = a | b, `0x2` = a & ~b, `0x1` = ~a & ~b.

A cell is usable as guard `@c0/@c1` and as jump condition whatever its mode. As a watcher (mode abort or call, enabled by `watch`), it fires in the first clock `cc_k` is 1 after a clock in which it was 0, while the engine is running:
- **abort**: `PC <- WV_k`; armed instruction, micro-loop, prefetch and a memory instruction in progress are dropped; event flags are cleared.
- **call**: `L <- PC`; then as abort but the event flags are kept. `ret` re-executes the preempted instruction (its guard waits again). One link register: a call-mode watcher must not be enabled while a `call` return is pending unless the program saves L.
- Watcher 0 wins if both fire in one clock. The handler is armed 2–5 clk later.

## 7. Shifter, taps and pin state

Let `w = wl`, `WL = w + 1`, `mask = 2^WL − 1`.
- `tap_out(S) = lsb ? S[0] : S[w]`; data bit `D = tap_out(S) ^ inv`.
- `shift(S, f) = lsb ? ((S & mask) >> 1) | (f << w) : ((S << 1) | f) & mask`.
- Counter: `N <- (N == 0) ? w : N − 1` on every shift. `wend = shift & (N == 1 | (N == 0 & w == 0))`.
- Sample: `lv = level(inpin)`; `bin = lv ^ (diff ? LV : 0) ^ inv`; `LV <- lv`.
- Tap update for a tapped bit t: `RUN <- r1 ? (t ? min(RUN+1, 7) : 0) : ((t == B) ? min(RUN+1, 7) : 1)`; `B <- t`. Condition `run` = `rl != 0 & RUN == rl`.
- Shifted bits additionally: `PAR <- ((N == 0) ? podd : PAR) ^ t` (N before the shift: parity restarts with each word); if `lfen`, one step of the shared LFSR with t. `smp` adds its bit to PAR without restarting it.
- `ALT` = `alt`: 0 → PAR, 1 → ~D, 2 → ~B (and the tap update is applied with t = ~B: the stuff bit), 3 → B.
- `MIS = level(inpin) ^ LD`, LD = last value driven by a `d`, `out`, `q` or `f` action. A following pin does not update LD after the `f` action itself, so MIS is not meaningful while a pin follows.
- Auto-transfer at `wend`, with Sx = S after the shift: `ok = (~apush | htx) & (~apull | hrx)`. If `(apush | apull) & ok`: push `Sx[7:0]` if `apush`; `S <- zext(host byte)` if `apull`. Otherwise nothing is transferred. `loop_end = wend & ~((apush | apull) & ok)`.

Logical pin state `oe`, `val`, `fol` (reset 0). In every clock a pin with `fol = 1` and no action on it gets `val <- D` (D from the current S). Pad function and modes: ARCH.md 2.1.

## 8. Instructions

Clocks = from fire to completion (ARCH.md 4.3). "Next" = time until the successor is armed: +1 if prefetched, else per ARCH.md 4.2.

### 8.1 E — pin/shift instant
Syntax: `[@guard :] action {, action}` with actions `pK <- 0|1|z|d|out|q|f`, `pK <- t` (p0, p1 only), `shin`, or `nop`. Loops: `loop { one or two E instructions }`.

| Field | Values |
|---|---|
| `pa`, `aa` | pin 0–3 and action A: 0 none, 1 `0`, 2 `1`, 3 `z`, 4 `d`, 5 `out`, 6 `q`, 7 `f` |
| `pb`, `ab` | pin 0–1 and action B: 0 none, 1 `0`, 2 `1`, 3 `t` |
| `s` | 1 = `shin` |
| `rr` | 0 none, 1 `rep`, 2 `head`, 3 `tail` (ARCH.md 4.4) |

Operation, in this order within the fire clock:
```
1  if s:   Sx = shift(S, bin); t = bin; tapped, shifted            (sample)
   if s & wend: auto-transfer (section 7) may replace Sx by the host byte
2  D = tap_out(Sx) ^ inv                                           (Sx = S if s = 0)
   A:  0   : val <- 0;  oe <- 1; fol <- 0
       1   : val <- 1;  oe <- 1; fol <- 0
       z   : oe <- 0;   fol <- 0
       d   : val <- diff ? val ^ D : D;  oe <- 1; fol <- 0; LD <- D
       out : as d, then (if s = 0) Sx = shift(S, 1); t = D; tapped, shifted; auto-transfer at wend
       q   : val <- ALT; oe <- 1; fol <- 0; LD <- ALT
       f   : val <- D;  oe <- 1; fol <- 1; LD <- D
   B:  0 / 1 as A;  t : val <- ~val; oe <- 1; fol <- 0
3  S <- Sx; taps, N, PAR, LFSR updated if tapped/shifted
4  loop control from rr and loop_end
```
- Clocks: 1. The pin state changes at the end of the fire clock; the pad register loads it one clock later, so the pad changes at the end of the clock after the fire clock (D081).
- Affects: pin state, S, N, B, PAR, RUN, LV, LD, LFSR (if `lfen`), FIFOs (auto-transfer). Not Z, CY.
- `nop` = all fields 0 except the guard; `0x0000` is `nop`.
- Defined corner cases (the assembler rejects them): A and B on the same pin → B wins for that pin; `out` together with `shin` → one shift (the sampling one), `out` acts as `d`; `t` on a following pin toggles `val` and ends follow; `z`/`od` on a `uo` pad has no electrical effect.

### 8.2 JP — jump on pin level
Syntax: `[@guard :] jhi pK, label` / `jlo pK, label`. Encoding: `lv` = 1 for `jhi`, `pin` = K, `rel7` = label − (PC + 1), range −64…+63.
```
if level(pK) == lv:  PC <- PC + 1 + sext(rel7)   (redirect)   else advance
```
Clocks 1. The level is read in the clock the guard fires. Affects nothing else.

### 8.3 JF — jump on flag
Syntax: `jmp label`, `jz`, `jnz`, `jc`, `jnc`, `jb`, `jnb`, `jpar`, `jnpar`, `jrun`, `jnrun`, `jw`, `jnw`, `jrx`, `jnrx`, `jtx`, `jntx`, `jc0`, `jnc0`, `jc1`, `jnc1`, `jovr`, `jnovr`, `jmis`, `jnmis`.
`cond[4:1]` = source, `cond[0]` = invert:

| Source | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Condition | true | Z | CY | B | PAR | run | w (N ≠ 0) | hrx | htx | cc0 | cc1 | OVR | MIS |

```
if cond:  PC <- PC + 1 + sext(rel7)   else advance
```
Clocks 1, never waits. `jmp` (cond 0) is unconditional and is folded by the prefetcher (ARCH.md 4.2). Sources 13–15 and cond = 1 ("never") are undefined → trap.

### 8.4 GM — guarded move, stream, timer, control, event
Syntax: `[@guard :] op`. All take 1 clock. `sub` and `x6 = {k[2:0], n[2:0]}`:

| sub | k | Syntax | Operation | Affects / notes |
|---|---|---|---|---|
| 0 | rd | `mov rd, rs` (n = rs) | `rd <- rs` | no flags. `mov rd, T` is the timer capture. rd = T: no write. L is 9 bits |
| 1 | 0 | `push rs` | TX FIFO `<- {0, rs[7:0]}` | full FIFO: byte dropped, `OVR <- 1`; use `@htx` to block |
| 1 | 1 | `pushh rs` | TX FIFO `<- {0, rs[15:8]}` | as `push` |
| 1 | 2 | `pull rd` | `rd <- zext(byte)`, pop RX FIFO | empty FIFO: rd unchanged, `OVR <- 1`; use `@hrx` to block |
| 1 | 3 | `pullh rd` | `rd[15:8] <- byte`, pop | as `pull`; rd[7:0] unchanged |
| 2 | 0 | `tsync` (n = 0) | timer start, periodic, `T <- P` | clears tick/tock flags |
| 2 | 0 | `tstop` (n = 1) | timer stops, T holds | an automatic sync in the same clock restarts it (ARCH.md 7) |
| 2 | 0 | `smp` (n = 2) | `t = bin` (section 7); tap update; `PAR <- PAR ^ t` | B, RUN, PAR, LV; not S, N, LFSR. Samples a bit that must not enter the word (parity bit, stuff bit, ACK) |
| 2 | 0 | `wrst` (n = 3) | `N <- 0; PAR <- podd; RUN <- 0` | next shift starts a word |
| 2 | 0 | `ovclr` (n = 4) | `OVR <- 0` | stays 1 if its own guard consumes a doubled flag (ARCH.md 6) |
| 2 | 0 | `ret` (n = 5) | `PC <- L` (redirect) | |
| 2 | 0 | `halt` (n = 6) | engine stops, `halted <- 1`, notice to host | `RUN` continues with the next instruction |
| 2 | 1 | `tset rs` (n = rs) | timer start, one-shot, `T <- rs` | tick after rs + 1 clk; clears tick/tock flags |
| 2 | 2, 3 | `sset #n`, `sclr #n` | signal flag G n set / cleared | several engines in one clock: clear wins (ARCH.md 2.6) |
| 2 | 5 | `watch #m` (n = m, 0–3) | `wen <- m[1:0]` (bit k enables watcher k) | n[2] must be 0 |
| 3 | — | `evt #c` (x6 = c, 0–63) | TX FIFO `<- {1, c}` | reported to the host in stream order; full FIFO as `push` |

Undefined → trap: sub 1 with k ≥ 4; sub 2 with k = 0, n = 7; k = 4; k = 5, n ≥ 4; k = 6, 7.

### 8.5 IM — immediates
Syntax and operation (rd ∈ R0, R1, R2, S), 1 clock, no wait:

| op | Syntax | Operation | Flags |
|---|---|---|---|
| 0 | `li rd, #imm8` | `rd <- zext(imm8)` | — |
| 1 | `lih rd, #imm8` | `rd[15:8] <- imm8` | — |
| 2 | `add rd, #simm8` | `rd <- rd + sext(imm8)` | Z, CY = carry out |
| 3 | `cmp rd, #imm8` | `rd − zext(imm8)`, result dropped | Z, CY = 1 if rd ≥ imm (no borrow) |

Assembler: `sub rd, #n` is `add rd, #−n`; `li rd, #v` with v > 255 expands to `li` + `lih`.

### 8.6 RX — ALU
Syntax `op rd, rs` (any registers; rd = T: result dropped, flags still set), 1 clock:

| op | Syntax | Operation | CY |
|---|---|---|---|
| 0 | `add rd, rs` | `rd <- rd + rs` | carry out |
| 1 | `adc rd, rs` | `rd <- rd + rs + CY` | carry out |
| 2 | `sub rd, rs` | `rd <- rd − rs` | 1 if no borrow |
| 4 | `cmp rd, rs` | `rd − rs`, result dropped | 1 if rd ≥ rs (unsigned) |
| 5, 6, 7 | `and`, `or`, `xor rd, rs` | bitwise | unchanged |
| 8 | `tst rd, rs` | `rd & rs`, result dropped | unchanged |
| 9 | `shl rd, rs` | `rd <- rs << 1` | rs[15] |
| 10 | `shr rd, rs` | `rd <- rs >> 1` (logical) | rs[0] |
| 11 | `rlc rd, rs` | `rd <- (rs << 1) | CY` | rs[15] |
| 12 | `rrc rd, rs` | `rd <- (CY << 15) | (rs >> 1)` | rs[0] |
| 3, 13, 14, 15 | undefined → trap (3, 13, 14 were `sbc`, `swap`, `not`: removed, D048) | | |

Z = (16-bit result == 0) for every ALU operation. L takes the low 9 bits.

### 8.7 RX — far jump, call
| Syntax | Operation | Notes |
|---|---|---|
| `jmp label` (absolute form) | `PC <- addr` | chosen by the assembler when rel7 does not reach, or forced with `ljmp`; folded like the relative form |
| `call label` | `L <- PC + 1; PC <- addr` | not folded; return with `ret` |

1 clock, then a redirect (2–5 clk to the target being armed).

### 8.8 RX — memory
Syntax: `ld rd, [rb]`, `ld rd, [rb+1]!`, `ld rd, [rb+S]`, `ld rd, [rb+S]!`, and `st rs, …` with the same operands. `rb` ∈ R0, R1, R2, S.

| mode | Address | Write-back |
|---|---|---|
| 0 | `rb` | — |
| 1 | `rb + 1` | `rb <- rb + 1` |
| 2 | `rb + S` | — |
| 3 | `rb + S` | `rb <- rb + S` |

```
ld:  at completion:  rd <- mem[addr[8:0]];  write-back      (rd == rb: the loaded value wins)
st:  at completion:  mem[addr[8:0]] <- rs;  write-back
```
- Clocks: `ld` 2–5, `st` 1–4 (one memory slot; ARCH.md 4.2). The engine is busy meanwhile; events are latched.
- No flags. rd = T: value dropped. Bit 0 of the encoding must be 0, else trap.
- `st` reads rs in its completion clock (the clock of the write), not when it fires: `st T` stores the count of that clock (D066). The address and the write-back value cannot change in between.
- Memory is shared and unprotected; a store into code that is already prefetched or armed does not change the copy in the engine.

### 8.9 RX — configuration
| Syntax | Operation |
|---|---|
| `wcfg creg, rs` | configuration register `creg` `<- rs` (section 5) |
| `rcfg rd, creg` | `rd <- creg` (LFSR_LO, LFSR_HI, SIG; others 0) |

1 clock; Z, CY unchanged. Side effects of writes: SHCFG → `wrst`; BIND → edges of both pins suppressed for one clock. LFSR/POLY/LFCFG are shared: of several writes in one clock the lowest engine wins, and any write blocks the LFSR steps of that clock (ARCH.md 2.7). `wcfg SIG` is undefined (trap): engines change the signal flags with `sset`/`sclr` only and read all eight with `rcfg`.

### 8.10 Undefined encodings
Every encoding not listed above is undefined. One undefined group lies inside a listed instruction (`wcfg` with index 21); `isa.yaml` carries it in its `undefined:` list so that the decoder predicate is generated completely. When it is armed the engine stops with `trap = 1`, PC stays on it and the host is notified; an undefined word injected by `EXEC` also ends the injection (ARCH.md 9.2). Nothing else changes.

## 9. Assembler

One statement per line: `[label:] [@guard :] operation [; comment]`. Mnemonics, registers and guard names are case-insensitive; labels are case-sensitive. A guard on a JF, IM or RX instruction is an error.

Numbers: decimal, `0x`, `0b`, `'c'`. Expressions: `+ - * / % << >> & | ^ ~ ( )`, comparisons `== != < <= > >=` (result 1 or 0), symbols, `$` (current address), `lo(x)`, `hi(x)`. Arithmetic is integer; `/` truncates. Immediates are written `#expr`.

| Directive | Meaning |
|---|---|
| `.equ NAME, expr` | constant |
| `.org expr` | location counter, 0–511 |
| `.word expr {, expr}` | literal words |
| `.space n` | n zero words |
| `.engine expr` | following `.init`, `.entry`, `.pad` and code belong to engine n (0–3) |
| `.init` … `.code` | instructions between them are not stored: they are emitted as injected (EXEC) instructions for the current engine, in order. Allowed: anything except relative jumps, `call`, `loop` |
| `.entry label` | injected `jmp` (absolute) to `label`, emitted after the engine's `.init` list |
| `.pad PIN, [eN.]pK {, od} {, inv}` | pad configuration; PIN = `uo0`…`uo7`, `uio0`…`uio7`; without `eN.` the current engine owns the pad; enabled after initialisation |
| `.slots a, b, c, d` | SLOTMAP (engine owning slot 0…3) |
| `.filter PIN {, PIN}` | input filter enables; PIN = `ui0`…`ui7`, `uio0`…`uio7` |
| `.if expr` / `.else` / `.endif` | conditional assembly (true = non-zero) |
| `.assert expr, "text"` | assembly-time check |
| `.deadline n [, label]` | written after a guarded instruction (or a loop) G, or after an unguarded instruction (wait 0): the next guarded instruction on one path from it must be armed ≤ n clk after it fires (section 10). Without `label`: the straight-line path (conditional jumps not taken, unconditional jumps followed). With `label`: the path that takes the first conditional jump to `label` (earlier conditional jumps not taken) and is straight-line from there. Several `.deadline` lines may follow one G |
| `.assume armed m` | written after a guarded instruction (or a loop) G: G fires ≥ m clk after it is armed (a loop: its last pass). Credits the prefetch of G's successor in the bound (section 10); a stated precondition, checked by VERIF T6 |

`loop { … }` holds one E instruction (`rep`) or two (`head`, `tail`); the assembler sets `rr`. The braces may stand on their own lines. `jmp` is relaxed to the relative or the absolute form.

Command line (D047): `jspe_asm.py [options] file.s {file.s}`. Several source files are assembled into one image; labels and `.equ` names are local to their file. `-D NAME=expr` replaces the value of `.equ NAME` in every file that defines it, `-D file.s:NAME=expr` in one file: this selects a variant, an engine (`ENG`) or a load address (`ORG`) without editing the source. `--mem n` (512 by default, 128 for the fallback build) makes a word at an address ≥ n an error. Two words at one address, two owners for one pad and two `.slots` lines are errors in every build.

Outputs: `.hex` (512 lines for `$readmemh`), `.bin` (load image, HOSTIF.md 7), `.lst` (address, word, source, arming bound).

## 10. Static arming bound

A *guarded* instruction is one whose guard is not `always`. For every guarded instruction or loop G and every path from it, the assembler bounds the clocks from G firing (a loop: its last pass) to the next guarded instruction being armed (D100). Slot map terms (D054): G = the largest gap between the engine's slots (4 default, 1 solo); K = max over the engine's slots s of (first own slot ≥ s + 2) − s, the step of a chained access (uniform maps K = max(G, 2): 0x00 G 1, K 2; 0x44 2, 2; maps with adjacent own slots K > G: 0x24 and 0x90 G 3, K 4).
- **First step.** G's successor is in the prefetch buffer when G fires only if G was armed ≥ G clk before it fired (its read waits ≤ G − 1 for a slot and arrives 1 clk after the grant); a successor that is an unconditional `jmp` has its target there only after ≥ G + 1 + K clk; a taken guarded jump's target is never prefetched. x = the accesses still missing when G fires: 0, 1 (the successor, or the folded jump's target) or 2 (a `jmp` successor and its target).
- **The wait is credited only when the program states it.** `.assume armed m` after G: G (a loop: its last pass) fires ≥ m clk after it is armed. It is a precondition on the environment, written as protocol timing (`BIT - 6`, `THIRD - 6`), with a one-line reason in the source; VERIF T6 checks every one in the in-contract worst cases. Without it the wait is 0: a level guard can be true at once, an edge or timer event can already be latched, an unguarded instruction executes when armed.
- **Accesses on the path:** one per instruction (its successor), one per taken jump target, `call`, `ret`, two per `ld`/`st`; a `jmp` directly behind G is folded and counted in x.
- **Bound** with j accesses on the path: x = 0: 1 + (G + 1) + (j − 1)·K (1 for j = 0); x ≥ 1: (G + 1) + (x − 1 + j)·K. Default map: 4j + 2 and 4(x + j) + 1.

`.deadline n [, label]` after G, or after an unguarded instruction (the path then starts when it executes, wait 0), turns the bound of one path into an error when it exceeds n; the message gives the bound the path would have if the guard waited long enough, as a pointer to a missing `.assume`. The paper programs give the bound itself as n; an event spaced at least n + 1 clk after the previous one therefore finds its guard armed. The bound is per program and independent of the other engines. `.lst` prints each G's straight-line bound and every `.assume`; a path through a `ret` whose `call` is not on it has no static bound ("?"). The ISS confirms the model: T5 for every `.deadline` path, T6 for every step of every paper program in slot maps 0xE4, 0x00, 0x44, 0x24 x 4 slot phases with event spacing down to 0, and for every `.assume` in the in-contract runs; at run time `OVR` reports a missed event.

## 11. Encoding summary

| Word | Instruction |
|---|---|
| `0ppa aabb bsrr gggg` | E: `pp` pa, `aaa` aa, then pb, ab[1:0], s, rr, guard |
| `10vp prrr rrrr gggg` | JP: v level, pp pin, rel7, guard |
| `1100 cccc crrr rrrr` | JF: cond5, rel7 |
| `1101 00dd dsss gggg` | `mov rd, rs` |
| `1101 0100 0sss gggg` | `push rs` |
| `1101 0100 1sss gggg` | `pushh rs` |
| `1101 0101 0ddd gggg` | `pull rd` |
| `1101 0101 1ddd gggg` | `pullh rd` |
| `1101 1000 0nnn gggg` | n: 0 `tsync`, 1 `tstop`, 2 `smp`, 3 `wrst`, 4 `ovclr`, 5 `ret`, 6 `halt` |
| `1101 1000 1sss gggg` | `tset rs` |
| `1101 1001 0nnn gggg` | `sset #n` |
| `1101 1001 1nnn gggg` | `sclr #n` |
| `1101 1010 10mm gggg` | `watch #m` |
| `1101 11cc cccc gggg` | `evt #c` |
| `1110 00dd iiii iiii` | `li rd, #i` |
| `1110 01dd iiii iiii` | `lih rd, #i` |
| `1110 10dd iiii iiii` | `add rd, #i` |
| `1110 11dd iiii iiii` | `cmp rd, #i` |
| `1111 00oo oodd dsss` | ALU op, rd, rs |
| `1111 010a aaaa aaaa` | `jmp` absolute |
| `1111 011a aaaa aaaa` | `call` |
| `1111 1000 dddb bmm0` | `ld rd, [rb mode]` |
| `1111 1001 sssb bmm0` | `st rs, [rb mode]` |
| `1111 1010 cccc csss` | `wcfg creg, rs` |
| `1111 1011 cccc cddd` | `rcfg rd, creg` |
