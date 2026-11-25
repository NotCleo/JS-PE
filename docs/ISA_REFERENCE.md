# ISA reference

The reader's version of the instruction set. Normative text: [docs/spec/ISA.md](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/ISA.md). Machine-readable source for the assembler, the ISS and the RTL decoder: [docs/spec/isa.yaml](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/isa.yaml) (67 instruction entries, 6 formats, 15 configuration registers). [sw/gen_isa.py](https://github.com/NotCleo/JS-PE/blob/main/sw/gen_isa.py) generates [sw/isa_def.py](https://github.com/NotCleo/JS-PE/blob/main/sw/isa_def.py) and [src/jspe_isa.vh](https://github.com/NotCleo/JS-PE/blob/main/src/jspe_isa.vh) from it, and the formal decoder check (`formal/fv_seq_dec.v`, k=1) proves the RTL decoder matches it.

## 1. Execution model

Each engine holds one armed instruction `guard : operation`. It waits until the guard is true, does the whole operation in that clock, and the next instruction is armed 1 clk later if it was prefetched (else 2–5 clk with the default slot map). Formats without a guard field act as `always`. Two watchers can preempt any wait. Pin edges and timer events that occur while the engine is busy are latched and answered late; a second one while the first is pending sets the sticky OVR flag.

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

| Format | Waits on a guard | Instructions | Clocks fire → done |
|---|---|---|---|
| E | yes | `pin` actions on up to two logical pins, optional `shin`, loop field | 1 |
| JP | yes | `jhi`, `jlo` (jump on a pin level, ±64 words) | 1 |
| JF | no | `jmp` and 24 conditional jumps on flags | 1 |
| GM | yes | `mov`, stream (`push`, `pushh`, `pull`, `pullh`), timer (`tsync`, `tset`, `tstop`), `smp`, `wrst`, `ovclr`, `ret`, `halt`, `sset`, `sclr`, `watch`, `evt` | 1 |
| IM | no | `li`, `lih`, `add #`, `cmp #` | 1 |
| RX | no | 12 ALU ops, `jmp` absolute, `call`, `ld`, `st`, `wcfg`, `rcfg` | 1; `ld` 2–5, `st` 1–4 |

Every encoding not listed is undefined and traps the engine (halts it with the trap flag and a notice to the host).

## 3. Registers and flags

| Code | Register | Bits | Use |
|---|---|---|---|
| 0–2 | R0, R1, R2 | 16 | general |
| 3 | S | 16 | shifter word |
| 4 | P | 16 | timer period |
| 5 | C | 16 | timer compare (tock) |
| 6 | T | 16 | timer count, read-only |
| 7 | L | 9 | link register |

Flags: Z, CY (ALU); B (last bit), PAR (word parity), RUN (run-length condition), N ≠ 0 (`w`, word incomplete), OVR (sticky overrun), MIS (input level ≠ last bit this engine drove: arbitration and collision detect).

## 4. Guards

| Code | Guard | True when |
|---|---|---|
| 0 | (none) / `@always` | every clock |
| 1, 2 | `@tick`, `@tock` | timer reached 0 / equals C (latched) |
| 3, 4 | `@hrx`, `@htx` | RX FIFO not empty / TX FIFO not full (level) |
| 5, 6 | `@c0`, `@c1` | condition cell output 1 (level) |
| 7 | `@never` | never; only a watcher or the host ends the wait |
| 8–11 | `@rise p0`…`p3` | rising edge of a logical pin (latched) |
| 12–15 | `@fall p0`…`p3` | falling edge (latched) |

A latched event that arrived before the guard was armed fires it at once ("stale event", D055); ISA.md 4 gives the idiom that forces one waiting clock where that matters.

## 5. Instructions by group

| Group | Instructions | What they do |
|---|---|---|
| Pin and shift (E) | `pK <- 0 \| 1 \| z \| d \| out \| q \| f`, `pK <- t` (p0, p1), `shin`, `nop`, `loop { … }` | drive 0/1, release (`z`), drive the data bit (`d`), drive and shift (`out`), drive an alternate tap (`q`: parity, inverted bit, stuff bit), follow the shifter (`f`), toggle (`t`); sample a bit into S (`shin`); repeat or pair-loop until a word completes |
| Pin level jump (JP) | `jhi pK, l`, `jlo pK, l` | branch on a live pin level, optionally after a guard |
| Flag jump (JF) | `jmp`, `jz/jnz`, `jc/jnc`, `jb/jnb`, `jpar/jnpar`, `jrun/jnrun`, `jw/jnw`, `jrx/jnrx`, `jtx/jntx`, `jc0/jnc0`, `jc1/jnc1`, `jovr/jnovr`, `jmis/jnmis` | relative branch on a flag; unconditional `jmp` is folded by the prefetcher (no execute clock) |
| Move | `mov rd, rs` | register copy; `mov rd, T` captures the timer |
| Stream | `push`, `pushh`, `pull`, `pullh`, `evt #c` | byte to/from the host FIFOs (low or high half); `evt` sends a 6-bit event code in stream order |
| Timer | `tsync`, `tset rs`, `tstop` | periodic start from P, one-shot start from a register, stop |
| Shifter control | `smp`, `wrst` | sample a bit without shifting it in (parity, stuff, ACK bits); restart the word |
| Control | `ret`, `halt`, `watch #m`, `ovclr` | return from `call` or a call-mode watcher; stop with a notice; enable watchers; clear OVR |
| Signals | `sset #n`, `sclr #n` | set/clear inter-engine flag G n |
| Immediate | `li`, `lih`, `add rd, #s8`, `cmp rd, #u8` | load low byte (zero-extend) / high byte, add, compare |
| ALU | `add`, `adc`, `sub`, `cmp`, `and`, `or`, `xor`, `tst`, `shl`, `shr`, `rlc`, `rrc` | 16-bit register–register; shifts by one through CY |
| Far jump | `jmp` (absolute, or `ljmp`), `call` | 9-bit absolute target |
| Memory | `ld rd, [rb]`, `[rb+1]!`, `[rb+S]`, `[rb+S]!`; `st` the same | base, pre-increment with write-back, indexed by S, indexed with write-back (`rb` ∈ R0–R2, S) |
| Configuration | `wcfg creg, rs`, `rcfg rd, creg` | write a configuration register; read LFSR_LO/HI or SIG |

Removed in the trim of D048 (now trap): `stgl`, `sbc`, `swap`, `not`, `wcfg SIG`.

## 6. Configuration registers

| Index | Register | Fields (isa.yaml) | Scope |
|---|---|---|---|
| 0 | SHCFG | word length 1–16, LSB first, input pin, invert, differential, auto-push, auto-pull (reset 0x0007: 8 bit, MSB first, p0) | engine |
| 1 | TAPCFG | alternate tap select, odd parity, LFSR enable, run-length limit and mode | engine |
| 2 | TCFG | fraction (1/256), prescaler ÷1/16/256, sync edge, sync pin, window mode | engine |
| 3, 4 | BIND01, BIND23 | per logical pin: source 0–31 (pads 0–15, signal flags 16–23, constant 0/1) and invert | engine |
| 5, 6 | CC0, CC1 | condition cell: two selects, 4-bit LUT, mode (off / abort watcher / call watcher) | engine |
| 7, 8 | WV0, WV1 | watcher vector (9-bit address) | engine |
| 16–19 | LFSR_LO/HI, POLY_LO/HI | shared 32-bit LFSR state and polynomial | shared |
| 20 | LFCFG | LFSR form (left / right) | shared |
| 21 | SIG | signal flags, read-only for engines | shared |

## 7. Assembler

[sw/jspe_asm.py](https://github.com/NotCleo/JS-PE/blob/main/sw/jspe_asm.py). One statement per line: `[label:] [@guard :] operation [; comment]`. Expressions with the usual integer operators, `$`, `lo()`, `hi()`.

| Directive | Meaning |
|---|---|
| `.equ`, `.org`, `.word`, `.space` | constants, location, data |
| `.engine n` | following `.init`, `.entry`, `.pad` and code belong to engine n |
| `.init` … `.code` | initialisation emitted as host-injected instructions (no memory used) |
| `.entry label` | injected `jmp label` after `.init` |
| `.pad PIN, [eN.]pK {, od} {, inv}` | pad configuration, enabled after initialisation |
| `.slots a, b, c, d` | SLOTMAP |
| `.filter PIN …` | input filter enables |
| `.if/.else/.endif`, `.assert expr, "text"` | conditional assembly, assembly-time checks |
| `.deadline n [, label]` | after a guarded instruction G: the next guard on that path must be armed ≤ n clk after G fires, else assembly fails |
| `.assume armed m` | after G: G fires ≥ m clk after it is armed — a stated environment precondition that credits the prefetch; checked by ISS test T6 |

Command line: `jspe_asm.py [-D NAME=expr] [--mem n] file.s {file.s}` (`--mem` default 512; 128 for the flop fallback) assembles several files into one image (per-file labels; `-D` selects variants, engines, load addresses). Outputs: `.hex`, `.bin` (load image for the host, [HOST_INTERFACE.md](https://github.com/NotCleo/JS-PE/blob/main/docs/HOST_INTERFACE.md)), `.lst` (address, word, source, arming bound). `--mem 128` is the shipped build.

## 8. Static arming bound

For every guarded instruction G and every path from it, the assembler computes the clocks from G firing to the next guard being armed (ISA.md 10, D054, D100). With G = largest gap between the engine's slots (4 default, 1 solo), K = chained-access step, j = memory accesses on the path and x = accesses still missing when G fires:

- x = 0: 1 + (G + 1) + (j − 1)·K (1 for j = 0); x ≥ 1: (G + 1) + (x − 1 + j)·K. Default map: 4j + 2 and 4(x + j) + 1.
- Prefetch of G's successor is credited only if the program states `.assume armed m`; without it the wait is 0.
- `.deadline n` turns a path's bound into an error when it exceeds n. The paper programs give the bound itself as n, so an event spaced ≥ n + 1 clk after the previous one finds its guard armed.

The ISS checks the model: T5 measures every `.deadline` path; T6 sweeps every step of every program in slot maps 0xE4, 0x00, 0x44, 0x24 x 4 phases with event spacing down to 0 — 0 violations ([sw/tests/test_timing.py](https://github.com/NotCleo/JS-PE/blob/main/sw/tests/test_timing.py), [sw/tests/test_bounds.py](https://github.com/NotCleo/JS-PE/blob/main/sw/tests/test_bounds.py), PROGRESS "Write-up findings").

## 9. Encoding summary

The bit-level table is ISA.md 11; the decode masks and values are the `instructions:` list of isa.yaml. Example program fragment (SPI controller bit loop, [docs/spec/programs/spi_ctrl.s](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/spi_ctrl.s)): one `loop { head; tail }` pair toggles SCK and shifts MOSI/MISO for a whole word without a fetch.
