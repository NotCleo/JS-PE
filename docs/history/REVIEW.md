# REVIEW — Phase 3 adversarial review of the frozen spec (2026-11-15)

Scope: REQUIREMENTS, ARCH, ISA + isa.yaml, PINS, HOSTIF, VERIF_PLAN, the 8 paper programs. Nothing has run yet: every number below is a hand execution against ARCH.md 4–7, except the encoding check and the area proxies, which were machine-run.
Status: **fixed** = applied to the spec in this phase (decision number given); **call** = judgment call, listed again in section 7.

## 1. Findings, ranked

| # | Sev | Location | Scenario | Fix | Status |
|---|---|---|---|---|---|
| 1 | high | ARCH 11, REQUIREMENTS 1, VERIF 9 | Area caps are about 2x too low for the `make area` flow. Proxies of four engine blocks, mapped to sg13g2 with the same yosys/abc flow, give 2 087 cells against a budget of 1 000 (section 4). Extrapolated: engine ≈ 3.5K cells (cap 1.85K), logic ≈ 18.5K, with the fallback 23.5K synthesized ≈ 29K placed = 92 % of the 32K nominal against the 70 % target. Real cell area is ≈ 434k µm² = 43 % of the die (≈ 52 % with buffers), so it probably still hardens; the sign-off criterion "`make area` ≤ caps" cannot be met as written | Re-baseline ARCH 11 from the measured unit costs and state the limit as cell area / die area; choose reduction levers | resolved, D051 |
| 2 | high | HOSTIF 1, 9 | Break recovery injects data. After the framing error the receiver takes a new start bit in the next clock. H_RX is still low, so the tail of an 11-bit break is received as 0xFF with a good stop bit and is written to the selected engine's RX FIFO | After a stop-bit error the receiver waits for H_RX high before it accepts a start bit | fixed, D044 |
| 3 | high | ARCH 9.2, HOSTIF 3 | An injected instruction whose guard never fires (`@hrx : pull` with no data) leaves `xm` = 1. `STOP` only clears `run`; the only way out is `RST`, which wipes the configuration. `RUN`, `STEP`, `EXEC` on an engine with a pending injection were undefined | `STOP` cancels a pending injection; `RUN`/`STEP`/`EXEC` to an engine with one pending are ignored and reported | fixed, D045 |
| 4 | med | spi_tgt.s header, ARCH 4.5, VERIF ST2 | MISO setup after the command byte. The table word reaches the pad at edge t + 7; the sampling edge was at the pad during clock t − 2, so the delay is 8–9 clk + pad, not 8.5. At T = 11.25 clk the worst partner phase leaves 45 ns, not 55 ns: ST2 fails as written. 2 MHz (310 ns) and 4 MHz (70 ns) are unaffected | Claim 45 ns at T ≥ 11.25 clk, 55 ns at T ≥ 11.75 clk | fixed |
| 5 | med | i2c_tgt.s `TSU` | First bit of a read byte: SCL is released TSU + 4 = 17 clk (340 ns) after SDA changes. A released 1 needs the bus rise time first: Fm needs 300 + 100 ns = 20 clk, Sm 1 000 + 250 ns = 63 clk. With the 100 kHz internal-pull-up demo the bit is sampled while SDA is still rising | `TSU` default 20 (20 + 4 fetch = 480 ns), 60 documented for Sm | fixed, D043 |
| 6 | med | i2c_ctrl.s, i2c_tgt.s | SDA hold after SCL falls is 60–120 ns (controller) and 40–60 ns (target). UM10204 asks a transmitter for ≥ 300 ns internal hold to bridge the SCL fall; another device with a lower threshold can see a START or STOP | Controller: program option `THD`, default 12 (hold 300–360 ns), `TLOW` 80 → 72 so the period is unchanged; no RTL. Target: possible with the windowed timer sync, +3 words | fixed, D042 (controller), D049 (target) |
| 7 | med | HOSTIF 1 | `SEL` race: H_RDY shows the new stream only at the end of the `SEL` byte. A data byte sent back to back can reach a stream that was left full: byte lost, OVR. Also: a host UART with hardware CTS cannot send `ESC STOP` to a blocked stream without switching flow control off | H_RDY switches at the stop sample of the `SEL` byte; host rule: one byte time (or a second `SEL`) before data; the CTS bypass is stated | fixed, D044 |
| 8 | med | ARCH 4–9, ISA 7 | Seven corner rules two implementers would resolve differently: PC while IR is empty (watcher call, EST); watcher in the completion clock of `ld`/`st`; `STOP` with a memory request pending or in flight; a timer start in a clock with a live tick/tock; FIFO full + write + pop; LD of a following pin; live events in an abort clock | One rule each, written into ARCH 2.4, 4.3, 5, 6, 9.2 and ISA 7 | fixed, D046 |
| 9 | med | ISA 9, programs | Programs cannot be combined: fixed `.engine`/`.org`, clashing labels (`idle`, `BIT`), no way to select a variant. The concurrency image C1 needs E1/E2/E3 copies, and on the 128-word fallback i2c_tgt (144 → 16) lands on uart_rx (16) | Several source files with file-local symbols, `-D NAME=expr`, `--mem n` with an overlap check; programs use `ENG`/`ORG` symbols | fixed, D047 |
| 10 | med | uart_tx.s, uart_rx.s, VERIF U1/U9 | The programs never write `TCFG.frac`, although U9 (3 Mbaud) and the header depend on it. MUST rates pass without it (worst 0.47 % at 921 600) | `FRAC` computed in `.init` (0 memory words) | fixed |
| 11 | med | i2c_ctrl.s | `push S` / `pushh S` are unguarded: with H_TX busy (an `RDMEM` reply) the sampled byte is dropped although SCL is low and the controller can wait | `tstop`, `@htx : push`, `@htx : pushh` (+1 word) | fixed, D043 |
| 12 | low | i2c_ctrl.s | No arbitration-lost check (section 3) | Omission justified; the host compares the echoed byte | fixed (documented), D041 |
| 13 | low | i2c_ctrl.s constants | tHIGH, tSU;STA are met by 40–80 ns at the pad threshold. With a 300 ns rise the 0.7 VDD point is ≈ 180 ns later than VDD/2: tHIGH ≈ 0.50 µs. The digital pad model cannot show this | `THIGH` 40 for compliance evidence on slow buses (max rate ≈ 373 kHz) | resolved, D050: stays 32, 40 documented |
| 14 | low | ARCH 6 | Stale flags: a guard armed with no waiting clock inherits flags latched while busy. spi_tgt: SCK activity while CS is high inside the 21-clk idle path, followed by CS low before `@fall p1` has waited once, shifts a ghost bit | Rule stated in ARCH 6; programs keep ≥ 1 waiting clock (ST4/ST5 combined case added to the first-tests list) | fixed (documented) |
| 15 | low | ARCH 12 | Two paths missing from the list: pin edge → fire → redirect → memory address, and pin edge → watcher → cancel of a granted store → write enable. ≈ 6.5 and ≈ 7.6 ns with the spec's own unit delays | Added as P6, P7 for the STA review | fixed |
| 16 | low | programs | No stuck-bus recovery: I2C controller with SDA held low (recovery = one step `byte, 0xFF, ninth = 1`: nine clocks), I2C target when the controller dies mid-read (both condition cells are in use; host `RST`) | Documented in the headers | fixed (documented) |
| 17 | low | ARCH 4.2 | `x: jmp x` folds forever: the fetcher spins and uses every slot of the engine. Use `@never : nop` | Documented | fixed (documented) |
| 18 | low | PINS 1, ARCH 9.1 | Pad output-enable flops are undefined until the first clocks under reset; `uo` outputs are 0 until enabled (UART TXD shows a break, SPI CS floats during load) | Accepted; partner flushes; pull-up on CS if a real target is attached | noted |

Checked and found consistent: isa.yaml against ISA.md 2, 8, 11 (71 instructions, no mask/value collision, every bit of every word covered by mask or field, 3 088 undefined words = the classes listed in ISA.md); host constants against HOSTIF.md; bind sources and pad lines of all programs against PINS.md; RP2350 function names. Timing paths P1–P5 leave ≥ 10 ns at 50 MHz by the spec's own unit delays.

## 2. Hand execution

Notation: pad edge during clock t − 2 → guard fires in clock t (ARCH 4.1). Engine slot phase arbitrary; g = grant clock.

**uart_rx (8N1, BIT = 50).** Start edge fires `tsync` in k. Tocks at k + 25 + 50n: start check at k + 25 (pad time: 25 clk after the edge = mid bit), data at k + 75 … k + 425. Eighth `shin` in t0 = k + 425 ends the loop; `push` fires t0 + 1; `stop` is granted in g ≤ t0 + 4 and armed in g + 2; `jmp rx` arrives g + 5, is folded in g + 6; `rx` arrives at the end of g + 9 ≤ t0 + 13. Stop sample at t0 + 50, start guard armed 1 clk later. Bound "BIT ≥ 13" confirmed with zero slack; with parity the same count gives BIT ≥ 17 (header says 18). No wrong cycle.

**spi_tgt (mode 0).** Eighth sampling edge fires in t. `ld S,[R1+S]!` fires t + 1, granted g ≤ t + 4, S loaded at the end of g + 1 ≤ t + 5, follow pin loads the pad register at the end of t + 6: edge t + 7 (finding 4 for the setup figure). Loop refetched: armed g + 6 ≤ t + 10. Later bytes: `ld`, then `jmp next` executes in IR (g + 6), target armed g + 10 ≤ t + 14. During the command MISO shows 0x00 (S starts 0, MSB tap). Bounds 6 / 10 / 14 confirmed, zero slack. CS abort releases MISO ≤ 8 clk + pad. One wrong number: the 55 ns setup.

**i2c_tgt (400 kHz).** START in s: `start` armed ≤ s + 5, loop armed ≤ s + 13. Eighth rise in t: `shr` t + 1, `cmp` ≤ t + 6, `jnz` ≤ t + 10, ACK guard armed ≤ t + 14 (SCL falls ≥ t + 30). ACK and SCL hold at the pad edge after the fall is seen (2–3 clk + pad). Read: bit 7 by `p1 <- out` (N: 0 → 7), seven loop shifts end the word on bit 0: bit count correct. Own SDA changes only while SCL reads low, so neither watcher is self-triggered. One wrong constant: `TSU` (finding 5).

Also executed: uart_tx (stop tick → start instruction ≤ 10 clk, back to back exact), spi_ctrl (first tock after `tsync` precedes the first tick and is discarded by a waiting clock; target window (SMP − 2) clk − pad confirmed), i2c_ctrl (tLOW 91–94 clk, tHD;STA 33 clk), ws2812 (6 / 14 clk), manchester (10 / 10 / 14 clk). No wrong cycle found.

## 3. I2C controller: arbitration-lost check (requested item 1)

Not added. A `jmis` after `shin` costs 1–2 words but is wrong: MIS = "SDA released by us, read low" is the normal state in the ACK slot of every write and on every 0 bit of a read, so the check would abort every transfer. A correct check must exclude the ninth bit and read bytes: a per-step flag kept in CY (`cmp R0, #2`) plus `jnc` / `jnw` / `jmis` in the bit loop and a handler = 5–6 words and +12 clk in the SCL-high path (breaks the 1 MHz setting). Both condition cells are taken (SCL level, deadline), so a watcher form is not available either.
Against REQUIREMENTS: multi-controller arbitration is out of scope for v1 (section 7) and listed as a stretch primitive (section 6); the primitive itself (MIS, `jmis`, cell select 14) exists. Detection without back-off is already there at no cost: the controller returns every written byte as sampled on the bus, so the host sees a lost arbitration as "echo ≠ data". Logged as D041; the program header says so.

## 4. Area recheck (requested item 3)

Scratch proxies written from ARCH 7–8 and ISA 7–8 (not project RTL), mapped with the `make area` flow (yosys `synth`, `dfflibmap`, `abc` on `sg13g2_stdcell_typ_1p20V_25C.lib`):

| Block | Budget / cap (ARCH 11) | Measured cells | Flops | Cell area µm² |
|---|---|---|---|---|
| `jspe_timer` | 240 / 260 | 490 | 82 | 8 054 |
| `jspe_shift` | 190 / 210 | 372 | 44 | 5 183 |
| `jspe_alu` with R0–R2 | 420 / 450 | 938 | 50 | 11 113 |
| `jspe_epins` | 150 / 170 | 287 | 42 | 5 057 |
| `jspe_fifo` 4x8 / 4x9 | 88 / 100 each | 125 / 132 | 41 / 45 | 3 221 / 3 505 |
| fallback memory 128x16 | 7.7K | 4 993 | 2 062 | 162 060 |
| 16-bit register, enable (+ sync reset) | — | 32 (48) | 16 | 1 045 (1 190) |

- The four engine blocks measure 2.09x their budget. Flops per engine by the state lists of ARCH 3.3, 7, 8 and ISA 5: ≈ 377, not 330. Scaling the sequencer and the shared blocks by the same factor: engine ≈ 3.5K cells / 49k µm²; logic ≈ 18.5K cells / 272k µm².
- With the fallback memory: 23.5K synthesized, ≈ 29K placed (x1.25) = **92 % of the 32K nominal, limit 70 %**. In area: 434k µm² of cells on a 1.00 mm² die = 43 %, ≈ 52 % with 20 % buffers.
- With the SRAM macro: 18.5K synthesized, ≈ 23K placed = 72 % of nominal; 33–40 % of the die including the macro and a halo.
- The fallback memory is smaller than planned (2.4 cells per bit, not 3.7) but area-heavy: 5.2 tiles of cell area on its own.
- Limits of this check: the proxies are quick RTL and `abc` runs its default script; careful RTL and the LibreLane area strategy typically save 15–25 %. The sequencer and the link are extrapolated, not measured. The direction is firm: the per-block caps cannot be met, and the cell-count rule and the area rule disagree by a factor that only Phase 5 (real hardening) settles.
- Measured lever: ALU without `swap`, `adc`, `sbc` and with the destination operand limited to R0–R2, S: 865 cells (−73 per engine).

## 5. Instruction set: unused instructions (requested item 4)

The paper programs use 35 of the 71 instructions. Unused: 17 flag jumps, `ovclr`, `halt`, `sset`, `sclr`, `stgl`, 12 ALU operations (`add`, `adc`, `sub`, `sbc`, `and`, `or`, `xor`, `tst`, `shl`, `rrc`, `swap`, `not`), `st`, `rcfg`.
The 71 names are about 21 decode classes (all 25 flag jumps are one 13:1 condition mux with an invert bit; the 15 ALU operations are one class), so removing names barely changes the decoder. The cost sits in the datapath.

| Candidate | Why it can go | Replacement | Saving (estimate) |
|---|---|---|---|
| `swap`, `adc`, `sbc`; ALU destination limited to R0–R2, S | byte order is covered by `push`/`pushh`, `pull`/`pullh`, `lih`; no protocol needs arithmetic wider than 16 bits; P, C, L stay reachable with `mov` | — | 73 cells per engine, measured |
| `stgl`, configuration register SIG (`wcfg`/`rcfg SIG`) | `sset`/`sclr` and pin binding of the flags cover signalling; a byte between engines goes through a memory word (`st`/`ld`) | `sset` + `sclr`; mailbox | ≈ 25 per engine + 25 shared; `jspe_sig` becomes `(G | S) & ~C` |
| `halt` | same effect as `evt #c` + `@never : nop`, or a deliberate undefined word (trap) | 2 words | ≈ 10 per engine + 15 in the link (flag, notice code, ECLR/EST bits) |
| `not` | `xor` with a constant | 1–3 words | ≈ 16 per engine |

Keep, although unused: the flag jumps (`jrun` for bit stuffing, `jmis` for arbitration, cell and FIFO tests), `ovclr`, `sset`/`sclr`, the plain ALU operations and both shift directions, `st` (capture buffers, register-device writes, mailboxes), `rcfg` (CRC read-out). Encoding space: 3 088 undefined words, all in reserved sub-ranges; nothing collides.

## 6. Test first in Phase 4 (requested item 5)

| # | Claim | Why it is the likeliest to be wrong | First test |
|---|---|---|---|
| 1 | Arming arithmetic of ARCH 4.2 and the nine `.deadline` values (uart_rx 1 at BIT 13; uart_tx 10; spi_tgt 10, 14 and the t + 6 load; ws2812 6, 14; manchester 10, 10, 14) | every value is the bound itself: zero slack. One clock in "request raised in the clock that causes it", in the IR/PF capture rule or in jump folding moves all nine and every maximum rate in the headers | T1, T3, T5 in the four slot phases before any protocol test |
| 2 | Auto-transfer at `wend` (ISA 7): push of the completed word and pull of the next in one clock, loop continues, follow pin shows the new word one clock later | FIFO levels are registered; only manchester RX uses `apush`, no program uses `apull`, so the streaming rates of ARCH 13 have nothing behind them | S2 plus a gapless SPI stream program (`apull` + `apush` + follow) in T2 |
| 3 | Watcher during `ld`/`st`: cancelled up to and including the completion clock, a store granted in that clock is not written, call mode repeats the access after `ret` | three rules met in one clock (D046), and the write-enable gate is a new timing path (P7). spi_tgt takes this path whenever CS rises at a byte boundary | S5, T4; ST3 with CS rising in each of the 6 clocks after the eighth edge |
| 4 | Slot phase independence: bounds hold for any `RUN` phase, any partner phase, other engines saturating their slots, the host using free slots; maps 0x00, 0x44, 0x24 give 2 + (largest gap − 1) | the data access of `ld` competes with the prefetch inside one engine; request timing differs when the slot is every clock (solo) | T1 with all maps, T6, C2; ST1 in 4 x 4 phases |
| 5 | Pad-referred numbers: spi_tgt setup (finding 4), spi_ctrl answer window (SMP − 2) clk − 10 ns with the first tock arriving before the first tick, timer events at k + V + 1 and k + V − C + 1 with `frac` and a start in the tick clock (D038), UART drift 10.3 / 12.3 / 3.8 / 4.6 clk | all derived from the 2-clk synchronizer convention of ARCH 4.1 and a timer-level model, never executed | S3, then ST2, SC3, U4/U5, M2 with the 0x55 preamble |

Also early: the stale-flag case of finding 14 (ST4 + ST5 combined) and `STOP`/`RUN` in every state of the memory FSM (D046).

## 7. Judgment calls for the user

1. **Area (finding 1).** Options: (a) restate the limit as cell area ≤ 60 % of the die and re-baseline the caps from measured numbers (design stays as is, ≈ 52 % with the fallback); (b) make the SRAM macro the only 4-engine build and give the fallback 3 engines (−3.5K cells); (c) trim the ISA per section 5 (≈ −0.5K in total); (d) 8-bit data path (D022, now worth ≈ 2K cells). Recommendation: (a) now, decide (b)/(c) with the Phase 5 hardening numbers.
2. **I2C target SDA hold (finding 6).** Add the windowed-sync form (+3 words, data edges 300 ns after SCL falls, SCL hold stays fused) or keep 40–60 ns and document "single-partner buses". Recommendation: add as an option, off by default.
3. **`THIGH` 32 → 40 (finding 13)** for compliance on buses with a 300 ns rise; costs rate (≤ 373 kHz).
4. **Instruction removals (section 5).** The ISA is frozen; each removal edits isa.yaml and ISA.md. Recommendation: drop `stgl` + SIG register and `halt` now (no program uses them), decide the ALU trim with the area numbers.
5. **i2c_ctrl defaults.** `THD` = 12 is on by default (41 words instead of 39, same bit period). Say so if the 39-word form should stay the default.

## 8. Resolutions (user, 2026-11-16)

1. Area: the limit is cell area, placed cells + macro ≤ 60 % of the die. The SRAM macro is the primary build. If Phase 5 exceeds the limit: further ISA trims first, the flop fallback only if the macro fails precheck, three engines last (D051). ARCH 11 is re-baselined from the proxies: 38.7 % for the SRAM build, 52.0 % with the fallback.
2. I2C target SDA hold added and on by default: +3 words (30), `THD` 13 = 300–320 ns (D049).
3. `THIGH` stays 32; 40 is documented as the slow-bus option (D050).
4. Removed: `stgl`, `wcfg SIG`, `sbc`, `not`, `swap`. Kept: `adc`, `halt`, `rcfg SIG`. Freed encodings trap; 67 instructions, 3 416 undefined words (D048). Re-measured ALU proxy: 938 → 935 cells, so the per-engine savings estimated in section 5 for `swap` and `not` were too high.
5. `THD` = 12 stays the default of i2c_ctrl.s (D050).

Sections 1–7 stand as written on 2026-11-15; instruction counts, word counts and area figures there predate these decisions.
