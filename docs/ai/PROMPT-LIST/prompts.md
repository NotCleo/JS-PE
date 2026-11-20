# Cleo: prompts run in Claude Code, in order

Compiled from the planning chat that produced them, so this is the **intended** sequence. The exact text you typed (with your own edits) is in `docs/ai/prompts/`; where the two differ, that folder is authoritative.

Format: **Prompt N (model, effort, mode)**. "same" means unchanged from the previous prompt. `/clear` lines mark fresh conversations. *Uncertain* marks prompts that may not have been sent exactly as written.

Slash commands such as `/phase1-distill` run the files in `.claude/commands/`; their full text is in the repo.

---

## Phase 1: requirements

**Prompt 1 (Opus 5.5, high, accept edits)**
```
/phase1-distill
```

**Prompt 2 (same)**
```
Answers to REQUIREMENTS §9. Write them into REQUIREMENTS.md (replace §9 with "Resolved"), add DECISIONS.md lines, commit.

1. Host link pins: max 4, prefer ≤3, only on ui_in/uo_out — all 8 uio stay free for protocols.
   Confirmed: the RP2350 is wired to every ASIC pin plus clk and rst_n, so host pins are lost to protocols.
   Phase 2a must compare at least a 2-pin UART-style link (TT convention ui_in[7]/uo_out[0]) against 3–4-pin SPI.
2. Host bandwidth: program load speed doesn't matter (≤1 KB). Runtime data must sustain UART 1 Mbaud full duplex
   (~200 KB/s) with no FIFO overrun. Target ≥1 MB/s, minimum 250 KB/s.
3. Memory: plan for the 1 KB SRAM macro. Phase 5 checks it passes the TT IHP flow.
   The architecture must also work with a flop-based fallback memory (≤256 B); state its cell cost.
4. UART full duplex: MUST (it also proves concurrency).
5. Pull-ups on a Pmod: allowed and documented (4.7k for I2C 400 kHz). RP2350 internal pull-ups are OK for 100 kHz demos.
6. After RTL freeze: the not-designed-in demos are WS2812 and Manchester TX/RX, with no RTL changes.
   Stretch showcase: CAN 2.0A (bit stuffing, arbitration), RP2350 as the other node.
7. Clock: timing must close at 50 MHz (CLOCK_PERIOD 20). 60 MHz is a stretch goal, only if slack allows.

Also add: the RP2350 is the default bus partner for all demos and tests on hardware (no external devices needed for v1).
```

---

## Phase 2a: architecture

`/clear`

**Prompt 3 (Fable 5.2, max, accept edits)**
```
/phase2a-arch
```

New conversation (old one not resumed):

**Prompt 4 (Fable 5.2, max, accept edits)**
```
Read PROGRESS.md, docs/spec/REQUIREMENTS.md, docs/spec/ARCH_CANDIDATES.md and docs/DECISIONS.md first.

Approve Candidate B (REFLEX) with these changes — log in DECISIONS.md and update ARCH_CANDIDATES.md, then commit:

1. Memory arbitration: replace fixed priority with round-robin/TDM fetch + a 1-instruction prefetch buffer per engine.
   Guarantee a fixed, documented worst-case fetch latency per engine (state it in clocks), so the assembler bound is static and formally provable.
2. Events: every guard event (pin edge, timer tick, host data) is latched in a sticky per-engine flag, cleared when consumed.
   A delayed instruction must never lose an edge, only respond later. Add an overrun flag when a second event arrives before consume.
3. In 2b, evaluate a minimal shift-on-edge assist (shift one bit on a selected pin edge without an instruction fetch) to raise SPI target above 4 MHz.
   Adopt only if ≤500 cells and it stays protocol-agnostic; otherwise log why rejected.
4. Confirmed as proposed: 4 engines, 512×16 SRAM macro with 128×16 flop fallback, 3-pin host link (RX, TX, ready),
   the v1 exclusions listed, 16-bit vs 8-bit datapath to be settled in 2b with numbers.
Recompute the SPI target / UART RX / I2C target timing with the new arbitration and report the changed figures in ≤5 lines.
```

**Prompt 5 (same)**
```
Both confirmed. Also add to D020's scope for 2b: evaluate a load-time "solo mode" bit that gives one active engine all fetch slots (still deterministic per configuration); adopt only if cheap. Log it and commit. No other changes.
```

---

## Phase 2b: full spec and ISA

`/clear`

**Prompt 6 (Fable 5.2, max, accept edits)**
```
/phase2b-isa B
```

**Prompt 7 (same, after the usage-limit reset)**
```
continue
```

`/clear` (case B: partial work saved first with `git commit -m "wip: phase 2b partial"`)

**Prompt 8 (Fable 5.2, max, accept edits)**
```
Continue phase 2b for Candidate B. Read PROGRESS.md, git log --oneline -10, .claude/commands/phase2b-isa.md, ARCH.md, ISA.md, isa.yaml, and docs/spec/programs/.
The last commit is a WIP: the session stopped mid-edit.
1. First check ISA.md and isa.yaml agree with each other and with ARCH.md; finish or fix any half-done edit. Commit.
2. Check each existing program in programs/ is complete and has its header line (instr count, cycles/bit, max rate at 50 MHz, engines). Finish any partial one. Commit.
3. Write the remaining programs from the command's list, then VERIF_PLAN.md, committing after each file and updating PROGRESS.md.
Do not redo finished files. Final reply ≤10 lines.
```

**Prompt 9 (same; hit the usage limit)**
```
Yes: specify UART timing as mean baud error ≤ ±1 % over a frame, plus max per-bit deviation ≤ 1 clk, plus stop-bit sample point drift within ±(BIT/2 − 2 clk). Update VERIF_PLAN.md, log it, commit. Then update PROGRESS.md and stop.
```

---

## Phase 3: adversarial spec review

New conversation

**Prompt 10 (Fable 5.2, max, accept edits)**
```
First: if VERIF_PLAN.md does not yet specify UART timing as mean baud error ≤ ±1 % over a frame, max per-bit deviation ≤ 1 clk, and stop-bit sample drift within ±(BIT/2 − 2 clk), add it, log in DECISIONS.md, commit.

Then run the procedure in .claude/commands/phase3-review.md. In addition, check these specifically:
1. i2c_ctrl.s has no arbitration-lost check: after shin, SDA released high but read low means another controller won. Add a jmis-based exit + event if it costs ≤2 words, or justify the omission against REQUIREMENTS.
2. i2c_ctrl.s changes SDA 2–3 clk after driving SCL low. Legal (tHD;DAT ≥ 0), but with a slow SCL fall time this is a robustness risk. Decide whether a configurable SDA hold (~300 ns) is warranted.
3. Area: 4 engines × (R0–R2, S, P, C, T, L) plus per-engine ALU and shifter. Recheck the ARCH.md budget per engine against the 8x4 tile limit with the flop-fallback memory.
4. 71 instructions: list any that no paper program uses and that no plausible not-designed-in protocol needs; propose removals that simplify the decoder without losing generality.
5. Nothing has been executed yet. List the 5 claims most likely to be wrong in simulation (timing bounds, .deadline paths, auto-transfer at wend, watcher abort mid-memory-access, TDM slot phase) so Phase 4 tests them first.
Commit after each spec change, and update PROGRESS.md before stopping. Final reply ≤15 lines.
```

**Prompt 11 (same)**
```
Decisions (REVIEW.md 7):
1. Area: restate the limit as cell area. SRAM macro is the primary path. If Phase 5 numbers exceed budget, apply in this order: (a) ISA trims, (b) flop fallback only if the macro fails precheck, (c) 3 engines as last resort. Log the order.
2. I2C target SDA hold: add it, default on (+3 words).
3. THIGH stays 32: the @c0 wait already absorbs slow SCL rise. Document THIGH=40 as an option for slow buses.
4. Remove stgl, wcfg SIG, sbc, not, swap. Keep adc, halt, rcfg SIG. Freed encodings → trap. Update ISA.md, isa.yaml, ARCH.md, affected programs, budgets.
5. THD = 12 stays default.
Apply to spec, log in DECISIONS.md, commit each change, update PROGRESS.md (Next: /phase4-iss). Final reply ≤8 lines.
```

**Prompt 12 (same)**
```
Yes: update that CLAUDE.md line to "Area: placed cell area + macro ≤ 60 % of die (D051); measure with make area, track in the PROGRESS.md ledger." Commit, then stop.
```

---

## Phase 4: assembler and golden ISS

`/clear`

**Prompt 13 (Opus 5.5, high, auto)**
```
Run the procedure in .claude/commands/phase4-iss.md.
Before the general tests, write the "test first" cases from docs/spec/REVIEW.md: all nine .deadline paths with zero slack (run every program in all 4 TDM slot phases), auto-transfer at wend including apull, a watcher firing during ld/st, and pad-referred timing numbers. Report each as pass/fail with the measured value.
Commit per file, update PROGRESS.md. Final reply ≤12 lines.
```

`/clear` (after the usage limit; WIP committed)

**Prompt 14 (Opus 5.5, high, auto)**
```
Resume phase 4 (.claude/commands/phase4-iss.md). The last session hit the usage limit mid-work; PROGRESS.md may be stale.
First: git log --oneline -10, list sw/ and sw/tests/, run make swtest via the test-runner subagent. Then state in ≤5 lines what is done and what remains.
Then continue: finish the remaining parts, with the REVIEW.md "test first" cases before general tests. Commit after each file and update PROGRESS.md after each commit.
Final reply ≤12 lines with pass/fail and measured values for the test-first cases.
```

**Prompt 15 (same)**
```
Decisions:
1. Approve the slot-gap bound fix: update ISA.md 10 and the assembler. Add a test asserting assembler bound ≥ ISS-measured arming time for every .deadline path on all 4 slot maps × 16 phases.
2. spi_tgt: take the 2-word fix (timer one-shot forcing one waiting clock). A spacing rule is unacceptable because shared SPI buses toggle SCK while our CS is high. Also add a general rule to ISA.md: how a program discards stale latched edges before a new wait, with this as the idiom. Check the other programs for the same hazard.
3. i2c_ctrl.s header: state the measured 0.89 MHz with 120 ns rise instead of 1 MHz.
4. U9: add .equ options to the UART programs for inverted lines (SHCFG inv), MSB-first (lsb=0) and low baud via the timer prescaler; test 300 baud. H3: close it or state in ≤2 lines why it needs a decision.
Log in DECISIONS.md, commit each, run make swtest via test-runner. Phase 4 is done when all tests pass with 0 failures. Update PROGRESS.md (Next: /phase5-skeleton). Final reply ≤8 lines.
```

**Prompt 16 (same)**
```
Two small fixes, then stop:
1. VERIF_PLAN.md: replace the 3 Mbaud drift estimate 3.8 with the measured 4.55 clk (limit 6.33).
2. Mark the 300-baud tests (and any test >20 s) with @pytest.mark.slow. Make `make swtest` skip them (-m "not slow") and add `make swtest-all` that runs everything. Update CLAUDE.md commands line.
Commit each, update PROGRESS.md. Reply ≤3 lines.
```

---

## Phase 5: walking skeleton through the TT GDS flow

`/clear`

**Prompt 17 (Opus 5.5, high, manual)**
```
/phase5-skeleton
Notes: info.yaml still has the template (top_module tt_um_example, source_files project.v) — the last GDS run built the example adder, not ours. Replace it: top module tt_um_notcleo_jspe, 8x4 tiles, our source files, clock_hz 50000000, pinout names from PINS.md; update test/tb.v and test/Makefile to match. Use the 512x16 SRAM macro path per D051; find how the TT IHP template expects macros (config.json, info.yaml, macro placement) and log it. Make sure the host-link registers and memory read path drive real outputs so synthesis can't prune them. Keep the stub engine trivial — this phase only proves the flow.
Write files as you go, commit after each. When local make test/lint/area pass, stop and tell me to push. Reply ≤10 lines.
```

**Prompt 18 (same; after GDS run 2 failed. Uncertain: an earlier version of this prompt for run 2 was not sent)**
```
Pushed. Watch run 3 (if gh can't access the run, tell me and I'll paste the results).
Macro rule: run 3 is the last attempt on the SRAM macro. If it fails on anything (LVS, DRC, precheck, timing), switch to the flop-fallback memory per D051 contingency (b), log it, rerun locally, commit, and tell me to push.
Area: correct the limit using the core/placeable area reported by the GDS run (not nominal 1.225 mm²). Update ARCH 11 and D051, log it.
IR drop: add a Phase 8 task to VERIF_PLAN.md: estimate IR drop manually (stripe resistance × peak current from the power report) since the flow check is disabled, and state this limitation in the write-up.
When green: record cells (incl. flip-flop count), utilisation, worst setup/hold slack, LVS, DRC and precheck in PROGRESS.md (area ledger). Note how much Metal4 the 32 extra stripes block, as a routing risk for the full design. Commit, push. Reply ≤8 lines.
```

**Prompt 19 (same)**
```
Pushed. Watch run 4 and fill in the PROGRESS ledger when green.
Also log a deferred task in PROGRESS.md and VERIF_PLAN Phase 8: "SRAM macro retry (one attempt, after all protocols pass on the flop build): fix the LVS supply-name mismatch (VDD!/VSS!/VDDARRAY! vs netlist names) via netgen equates or the macro's powered netlist, starting from commit 6896dd8. Adopt only if gds + LVS + precheck + gl_test are all green; otherwise stay on flops." Note the program-memory limit (128 words shared by 4 engines) in REQUIREMENTS as a known constraint, and list which program combinations fit concurrently. Commit, push. Reply ≤6 lines.
```

**Prompt 20 (same)**
```
Log for Phase 8 in PROGRESS.md and VERIF_PLAN §9: run 4 inserted 2417 dlygate4sd3 hold-delay cells (more than the 2275 flip-flops); they likely explain part of the 1.42× placed/synth ratio. Investigate cause (flop-memory read path, clock skew, PL/GRT hold slack margins) and estimate area recoverable. No changes now. Commit, push. Reply 1 line.
```

**Prompt 21 (same; uncertain whether sent as written)**
```
Edit .github/workflows/gds.yaml (and test.yaml/docs.yaml if they also run on every push):
1. Run gds only when src/**, info.yaml, test/** or the workflow file change (on.push.paths), and keep workflow_dispatch so I can trigger it manually.
2. Add concurrency: group gds-${{ github.ref }}, cancel-in-progress: true, so a newer push cancels an older run.
Don't change any job steps. Commit, push. Reply 2 lines.
```

---

## Phase 6: RTL blocks

`/clear`

**Prompt 22 (Fable 5.2, high, plan, then auto after approval)**
```
/rtl-block jspe_engine
Scope: sequencer, TDM fetch + prefetch, guard evaluation, latched event flags + OVR, watchers, loop control — per ARCH.md 4–6 and ISA.md. Must match sw/jspe_iss.py cycle-exact; reuse its test vectors. Memory is the 128x16 flop build by default (JSPE_MEM_SRAM selects the macro); keep the memory interface identical for both.
Area is tight (D064: ~69k µm² margin): report synthesized area vs its ARCH 11 budget before committing.
Write files as you go, commit every ~20 min. Final reply ≤10 lines.
```
Then: `go ahead`

**Prompt 23 (Fable 5.2, high, auto; after the usage-limit reset)**
```
Limit is reset. Continue: first git status + commit any uncommitted engine work. Then rerun the reviewer subagent on src/jspe_engine.v against ARCH.md 4–6 / ISA.md, fix high and medium findings, rerun make unit B=jspe_engine and the ISS lockstep tests via test-runner, report area vs ARCH 11 budget, commit, update PROGRESS.md. Final reply ≤10 lines.
```

**Prompt 24 (same)**
```
Answers: 1) Yes, correct ARCH 4.2 to match RTL/ISS (jmp behind ld executes); confirm the assembler bound model uses the same rule. 2) Yes, add the gnt→st_done row to ARCH 12. 3) Yes, re-split ARCH 11 engine caps as measured, total ×4 ≤ 214k; log the ALU/shifter 8:1 operand-mux structure as a Phase 8 area lever.
Log as D070, commit, push. Reply 2 lines.
```

Blocks `jspe_arb`, `jspe_fifo`, `jspe_sig` and `jspe_lfsr` were then continued on Fable 5.2 via `/rtl-block <name>` until the usage limit hit.

New conversation (different day)

**Prompt 25 (Opus 5.5, high, auto)**
```
/resume
Check which of jspe_arb, jspe_fifo, jspe_sig, jspe_lfsr are done (git log, PROGRESS.md, make unit B=<each> via test-runner). Finish any incomplete one per .claude/commands/rtl-block.md with area vs ARCH 11 budget. Commit each, push, update PROGRESS.md. Reply ≤8 lines.
```

**Prompt 26 (same)**
```
FIFO: no — keep rdata registered per ARCH 2.4/P5. Log the alternative coding (−2.1k µm²) as a Phase 8 area lever. Commit, push.
Then continue: /rtl-block jspe_pins, then /rtl-block jspe_link (the real host link replacing the Phase 5 skeleton), each per .claude/commands/rtl-block.md with area vs ARCH 11 budget. Commit and push each, update PROGRESS.md. Stop after jspe_link. Reply ≤8 lines.
```

**Prompt 27 (same)**
```
Link area: apply the three counter/register merges (target ~23.4k), then raise the jspe_link cap to the measured value (≤24k µm², ≤1.4K cells) in ARCH 11 and D016; log as D079. Rerun jspe_link unit tests, top test and engine lockstep via test-runner.
Also retry the ARCH 2.5 H_TX/H_RDY wording edit so the spec matches the RTL/ISS (two flops to the pad); I'll approve the permission prompt.
Commit, push, update PROGRESS.md (Next: top-level integration). Reply ≤5 lines.
```

**Prompt 28 (same)**
```
Accept 1539 cells: area is the criterion. Update D016 to drop the cell-count cap (area cap only). Commit, push. Reply 1 line.
```

---

## Integration and timing closure

`/clear`

**Prompt 29 (Opus 5.5, high, plan, then auto)**
```
Integrate jspe_engine ×4, jspe_arb, jspe_fifo, jspe_sig, jspe_lfsr, jspe_pins and jspe_link into tt_um_notcleo_jspe per ARCH.md (block diagram, pad mux, SLOTMAP). Replace the Phase 5 stub engine.
Tests: make test must load a real assembled program over the host link and run it (start with uart_tx.s looped to uart_rx.s on another engine, compared against the ISS).
Re-sum ARCH 11 totals with measured block areas; make area for the full design vs the 60 % core limit. Lint, commit, push so GDS runs. Final reply ≤10 lines with area and test results.
```
Then: `go ahead`

**Prompt 30 (Opus 5.5, high, auto; GDS logs copied to build/gds_run first)**
```
GDS run 44184771880 failed at step 68-checker-setupviolations (everything up to LVS passed). The run artifacts are in build/gds_run/runs/wokwi/.
Read only: error.log, 55-openroad-stapostpnr/summary.rpt, and for the failing corner(s) wns.max.rpt, tns.max.rpt and the top 3 paths of max.rpt. Don't read .def/.odb/.spef/.gds.
Report in ≤8 lines: failing corner(s), WNS/TNS, the worst path (start → end, module chain, logic depth), and the cause.
Then propose the minimal RTL fix (pipeline register / retime / split the mux), with its cycle impact on the ISS/ISA. Don't apply it until I approve, unless it adds no cycle of latency and keeps ISS lockstep unchanged.

Also: H_RDY fix — approved. Keep H_RDY high (not ready) during the escape state; update ISS, jspe_link and HOSTIF §1, add a test for the 4-clk/bit escaped-0x1B-into-one-free-entry case, remove the test's data-ordering workaround. Log as a decision.
```

**Prompt 31 (same)**
```
Apply the +1 clk fix: feed the jspe_pins owner mux from the engines' registered pin state (val/oe), not next-state. Log as D081 with the reasoning (504 failing endpoints, WNS −1.17 ns slow corner, robustness over a marginal flow-side fix).
Update in this order: ISS (pad_outputs from previous-clock val/oe) → RTL → ARCH 4.1/4.5/12 (reaction floor 3–4 clk + pad, P3 new estimate) → ISA.md wherever pad timing is stated → every program header and .deadline/timing claim (spi_tgt t+8, i2c_tgt SCL hold 3–4 clk, others) → VERIF_PLAN numbers.
Also fix the 17 max-slew violations flow-side (e.g. MAX_TRANSITION_CONSTRAINT / design-repair settings in src/config.json), without touching the template's "do not change" section unless required; log it.
Re-run: make swtest-all, make unit for every block, make test, lint, via test-runner. All must pass. Check that every REQUIREMENTS MUST still holds with the new latency (SPI target MHz, I2C 400 kHz, UART 1 Mbaud full duplex); list any that changed.
Commit each step, push (including 4050e25), update PROGRESS.md. Reply ≤10 lines with the new ISS-measured limits.
```

**Prompt 32 (same)**
```
GDS run 44246474825 is green: utilisation 52.545 %, 42044 cells (4387 dfrbpq, 12445 buf, 4247 dlygate, 4240 mux), precheck all ✅, gl_test passed.
Use gh (gh run view / gh run download with only the needed files, not the whole artifact) to get slow/typ/fast setup WNS, hold WNS and the max-slew/max-cap violation counts. If gh isn't authenticated, tell me and I'll paste them.
Record everything in the PROGRESS ledger. Add to the Phase 8 area list: 12445 buffers (~30 % of cells, likely from the long-wire buffering in D082) and 4247 hold-delay cells — investigate both. Commit, push. Reply ≤5 lines.
```

**Prompt 33 (same)**
```
Area is now the binding constraint (57.7 % placed, ~27.8k µm² left; placed/synth = 1.56×). Add to CLAUDE.md under hard constraints:
"Area freeze: from Phase 7 on, RTL changes are bug fixes only. Every RTL commit reports synthesized area delta (make area); any increase >2k µm² synthesized needs my approval first. Prefer program/ISS fixes over RTL."
Update ARCH 11 to use 1.56× as the placement factor. Commit, push. Reply 2 lines.
```

---

## Full-RTL review

`/clear`

**Prompt 34 (Opus 5.5, max, auto)**
```
You did not write this RTL: review all of src/*.v against docs/spec (ARCH, ISA, HOSTIF, PINS). Focus: cross-block integration (engine↔arb↔mem, link↔fifo↔engines, pins ownership/OE/open-drain, sig shared-flag resolution, reset and host reload mid-run, STOP/EXEC races), X sources, and test gaps vs VERIF_PLAN.
Write findings to docs/spec/RTL_REVIEW.md as you go (ranked: severity, file:line, failure scenario, fix), commit it.
Fix high/medium findings with a regression test each, respecting the CLAUDE.md area freeze (report area delta per fix). Run make test / make swtest / unit tests via test-runner. Commit each fix; batch the push to the end so only one GDS run starts. List low findings and judgement calls for me. Update PROGRESS.md. Final reply ≤10 lines.
```

**Prompt 35 (same)**
```
Decisions: J1–J4 — accept your recommendations; rule: current RTL+ISS behaviour defines these same-clock cases, the spec text follows (no RTL change under the area freeze). J5 — yes, align the ISS reply handling with the RTL so lockstep holds even when the host breaks the one-reply rule; add a test.
Approve spec edits F2 and F6–F11. Document both one-clock STOP races in HOSTIF as defined behaviour.
Log as decisions, commit each, push once at the end. Update PROGRESS.md (Next: /protocol uart). Reply ≤4 lines.
```

---

## Phase 7: protocol bring-up

`/clear`

**Prompt 36 (Opus 5.5, high, auto)**
```
/protocol uart
Area freeze applies (CLAUDE.md): prefer program/ISS fixes over RTL. Batch pushes: push once at the end of this protocol.
Also: in .github/workflows/gds.yaml, narrow on.push.paths to src/**, info.yaml and the workflow file (gl_test still runs inside the gds workflow when RTL changes). Commit with the rest.
```

**Prompt 37 (same)**
```
Reload-while-streaming: yes, add the HOSTIF 8 rule. Also make host/jspe.py handle it automatically: when any engine is running, read back in chunks of ≤5 words at B=16 (or use B=4 if the board supports it); C2 must pass using the default loader. Add the C2 test.
Demo clock: host/examples must assemble programs with -D CLK_HZ matching the actual clock (48 MHz); add an assert that the loader's clock and the image's CLK_HZ agree.
PINS.md GPIO map: verify the RP2350↔TT pin mapping against the TinyTapeout tt-micropython-firmware sources/docs (web), correct PINS.md and the demo, cite the source.
Log decisions, commit, push once. Update PROGRESS.md (Next: /protocol spi). Reply ≤5 lines.
```

`/clear`

**Prompt 38 (Opus 5.5, high, auto)**
```
/protocol spi
Area freeze applies (CLAUDE.md): prefer program/ISS fixes over RTL. Batch pushes: push once at the end of this protocol. Keep the default test run ≤5 min (full sweep behind an env flag, as for UART).
Cover: controller modes 0–3 × MSB/LSB × word sizes, SPI target modes 0–3 at the ISS-measured max SCLK, CS setup/hold, back-to-back transfers, SCK activity while our CS is high (shared bus, D055), and controller+target on two engines talking to each other.
```

**Prompt 39 (same)**
```
SC2: yes, reword VERIF_PLAN SC2 to the measured CS-hold range (11–16 clk + CSDLY), per D091.
ST2: accepted, but as an explicit limit: state a minimum CS-high (inactive) time for spi_tgt (≈25 clk + MISO setup) in the spi_tgt.s header, PINS/REQUIREMENTS and ST2, and add a test at the boundary (min passes, min−1 shows the documented late MISO). Log it.
Commit, push once. Reply ≤3 lines.
```

`/clear`

**Prompt 40 (Opus 5.5, high, auto)**
```
/protocol i2c
Area freeze applies (CLAUDE.md): prefer program/ISS fixes over RTL. Batch pushes: push once at the end of this protocol. Keep the default test run ≤5 min (full sweep behind an env flag).
Cover: controller and target at 100 and 400 kHz, open-drain wired-AND bus model with pull-up rise time, ACK/NACK, repeated START, clock stretching incl. the timeout watcher, SDA hold timing, and controller+target on two engines on one bus. Test memory must be pre-filled with random words (as for SPI).
Before the final push, run the full make test once (UART + SPI + I2C) via test-runner.
```

**Prompt 41 (same)**
```
Approve both spec fixes: (1) IT5 text → "TSU + 2..5 clk (≥ 440 ns with TSU 20)", IT5 asserts ≥ TSU+2; (2) IC6/i2c_ctrl.s/D056 → 60 clk/bit (0.83 MHz), noting D081 as the cause. Log both, plus D094 for E1's uio[2:1] move.
ACK setup: don't rely on SCL rise time. In i2c_tgt.s, after placing ACK on SDA, keep SCL stretched until ACK has been stable ≥ tSU;DAT + margin for the configured mode (≥ 300 ns at 100 kHz, ≥ 150 ns at 400 kHz) — program/.equ change only, no RTL. Add a test asserting ACK-to-SCL-release ≥ that value with zero rise time, at both rates.
Then continue: reviewer, host demo, full make test via test-runner, commit, push once. Reply ≤8 lines.
```

**Prompt 42 (same)**
```
Run JSPE_I2C_FULL=1 make test (I2C only) in the background via test-runner; report pass/fail when done. If it fails, stop and report; don't fix yet.
```

**Prompt 43 (same)**
```
Yes: update PROGRESS.md (I2C full sweep 11/11, 5.9 min), commit, push. Reply 1 line.
```

---

## Protocols not designed in

`/clear`

**Prompt 44 (Opus 5.5, high, auto)**
```
Bring up the not-designed-in programs (docs/spec/programs/ws2812.s, manchester.s) per .claude/commands/protocol.md steps 1–4, using only the assembler and existing RTL — no RTL changes. If RTL changes would be needed, stop and list them as a write-up finding.
WS2812: test bit timings (T0H/T1H/TL ±150 ns), reset ≥280 µs, ≥64 LEDs streamed from the host without underrun. Manchester: TX+RX on two engines at ≥1 Mbit/s, the 0x55 preamble case, and an error case (missing transition).
Area freeze applies. Default test run ≤5 min. Pre-fill memory with random words. Full make test once via test-runner before the single push.
```

**Prompt 45 (same)**
```
Yes: create an annotated tag rtl-freeze-v1 on 42457db ("RTL used for all protocol bring-up; WS2812 and Manchester added after with no RTL change") and push the tag. From now on, any src/ change must be listed against this tag in PROGRESS.md.
Manchester: add an optional code-violation check to manchester.s (.equ CVCHK, default 1 if it fits ≤4 words; else default 0) that ends the frame on a missing mid-bit transition; test it. Program change only.
Then do protocol step 5 for both: host/examples/ws2812.py and manchester.py, minimal. Commit, push once. Reply ≤5 lines.
```

**Prompt 46 (same)**
```
The static bound under-estimating (6 vs measured 7) is a correctness bug in the guarantee, not a test issue. Fix the assembler: where the "guard waited ≥ 4 clk" assumption cannot be shown from the program (e.g. a guard whose event can arrive immediately), use the not-prefetched bound for that step, or require a .assume directive that states it. Revert the ISS check to assert bound ≥ measured.
Then add a sweep test: for every program and every guarded instruction (not only .deadline paths), across all 4 slot maps × phases and event spacings down to 0, assert assembler bound ≥ ISS-measured arming time. Log the decision, update ISA.md 10. Commit, push once. Reply ≤5 lines.
```

**Prompt 47 (same)**
```
spi_tgt timing: consolidate D093 and the new limits into one stated rule per PINS/REQUIREMENTS/ST2/spi_tgt.s header (e.g. "min CS-high X clk, first SCK ≥ Y clk after CS falls; max SCLK Z"), so no reader has to combine three. Check REQUIREMENTS MUSTs still hold. Commit, push. Reply 2 lines.
```

### CAN 2.0A

`/clear`

**Prompt 48 (Opus 5.5, high, auto; failed: exceeded the 128k output-token cap, nothing written)**
```
Write a new program docs/spec/programs/can.s for CAN 2.0A (11-bit ID) as a protocol NOT designed in: use only ISA.md and the assembler; no RTL or ISS changes (rtl-freeze-v1). Open-drain-style bus on a uio pin (dominant = drive 0), RP2350 or a second engine as the other node.
Features: SOF, 11-bit ID, RTR, DLC 0–8, data, CRC-15 via the shared LFSR, bit stuffing (stuff after 5 equal bits, both TX and RX via ALT/run), stuff-error and CRC-error detection, ACK slot, EOF, and bitwise arbitration loss detection with backoff (MIS). Bit rate 125–500 kbit/s with configurable sample point.
Tests (test/test_can.py, ≤5 min default): two nodes exchanging frames, arbitration between two transmitters with different IDs, stuff-heavy IDs/data, injected stuff error and CRC error, missing ACK. Compare against a Python CAN reference model; lockstep against the ISS. Run the assembler-bound ≥ ISS sweep on can.s too.
If something truly cannot be done without RTL, stop and document it as a write-up finding. Full make test once via test-runner, commit, push once. Reply ≤10 lines with the measured bit rate and word count.
```

`/exit`, restart, new conversation

**Prompt 49 (Opus 5.5, high, auto)**
```
Add to CLAUDE.md workflow rules: "Never reason through a large design in one response. For any task with design + implementation, first write a short design note to a file and commit it, then implement in steps of ≤1 file or ≤1 feature per response." Commit. Reply 1 line.
```

**Prompt 50 (same)**
```
CAN 2.0A program (not designed in; no RTL/ISS changes, rtl-freeze-v1). Work in small steps, writing to disk and committing after each; never design everything in one pass.

Step 1 (stop after it): write docs/spec/programs/can_design.md, ≤800 words: engine split (TX/RX on 1 or 2 engines), bit timing + sample point via timer, stuffing via ALT/RUN, CRC-15 via shared LFSR, arbitration loss via MIS, ACK slot, EOF, error handling, word budget vs 128-word memory, and anything that may need RTL (flag it, don't solve it). Commit. Reply ≤6 lines.
```

**Prompt 51 (same)**
```
Design approved. Document the three limits in can.s header and the write-up findings list (resync = hard re-time on every recessive→dominant edge, i.e. SJW unlimited; ≤~700 kbit/s; fault confinement on host). Add a test later that shows the glitch behaviour honestly.
Step 2: write can.s per can_design.md (RX path first, then TX + arbitration). Assemble it, run it on the ISS with a minimal smoke test (one frame, one node TX → other RX). Commit. Reply ≤6 lines with word count.
```

**Prompt 52 (same)**
```
Step 2b: TX path + arbitration per can_design.md. Assemble (default and --mem 128), report total words and whether can.s + uart_tx + uart_rx still fit in 128. Commit. Reply ≤5 lines.
```

**Prompt 53 (same)**
```
No cuts now: correctness first. Document "CAN uses 126/128 words; runs alone on the flop build" in can.s header and REQUIREMENTS §1 combos, and note in PROGRESS that the SRAM retry (512 words) would allow CAN+UART concurrently. Trimming options go in the write-up findings, not applied.
Step 2c: ISS smoke test — chip TX → Python CAN node RX, and Python TX → chip RX, one frame each at 500 kbit/s; also chip TX → chip RX on two engines. Fix bugs in can.s only. Commit. Reply ≤5 lines.
```

**Prompt 54 (same)**
```
Step 3: extend sw/tests/test_can.py with: arbitration between two transmitters with different IDs (incl. IDs differing only in the last bit and in RTR), stuff-heavy IDs/data (runs of 5), injected stuff error and CRC error, missing ACK, error flag behaviour, 125 and 500 kbit/s, ±1 % clock offset between nodes, and the glitch-behaviour test (document the result honestly). Run the assembler-bound ≥ ISS sweep on can.s. Fix bugs in can.s only. Then run make swtest via test-runner. Commit. Reply ≤6 lines.
```

**Prompt 55 (same)**
```
Step 4: test/test_can.py (cocotb, lockstep vs ISS, ≤5 min default, random-filled memory), reviewer subagent, fix findings in can.s only (it is at 128/128 words: any fix must trim elsewhere; list the trade-off), full make test via test-runner, push once. Reply ≤10 lines with measured bit rate, word count, and write-up findings.
```

**Prompt 56 (same)**
```
Yes: fix can_design.md §4 and §11 to 625 kbit/s. Also fix .claude/agents/test-runner.md: run make targets in the foreground (or wait on the PID), never poll with pgrep -f on the command text (it matched itself and hung 1h25m). Commit both, push. Reply 1 line.
```

---

## Phase 8: formal verification

`/clear`

**Prompt 57 (Opus 5.5, max, auto)**
```
/phase8-signoff formal
Work per CLAUDE.md small-step rule: first write formal/PLAN.md (properties per block, ≤600 words) and commit, then one block per response. Priorities: TDM fetch-latency bound, event latching/OVR, pin ownership + open-drain/OE safety, FIFO invariants, host-link framing, no-X after reset. Report property → result → depth.
```

**Prompt 58 (same)**
```
Plan approved. Use prove (k-induction) wherever possible, falling back to bmc only with a stated depth and a reason; add a cover for each property so vacuous passes are caught. Go: arb.
```

**Prompt 59 (same)**
```
Next block: seq, split into sub-steps, one per response, committing each: (1) fetch latency ≤ G+1 + prefetch credit, (2) event latching/OVR, (3) watchers + trap/halt, (4) decoder vs isa.yaml. If induction fails, add helper invariants rather than falling back to bmc; if a property needs bmc, state the depth and why. Go: sub-step 1.
```

**Prompt 60 (same)**
```
Good. Track the D107 assumptions as obligations: in the link step, prove the link cannot issue EXEC to a running engine / with an injection pending, RUN/STEP while pending, or two pulse kinds per clock. If the link CAN (host-dependent), say so: then it's a documented host rule (HOSTIF) and the write-up states the bound is conditional on it — report before changing RTL.
Next sub-step: events/OVR.
```

**Prompt 61 (same)**
```
Next sub-step: watchers + trap/halt.
```

**Prompt 62 (same)**
```
Decision: (b). Reset FP to 1 (PC=0, FP=PC+1) in RTL, ISS and ARCH 9.1; it's a correctness bug, zero area. Log as a decision and in PROGRESS's list of src/ changes since rtl-freeze-v1 (bug fix, unrelated to protocol bring-up). Add a regression test (RUN straight after reset/RST with no EXEC runs word 0 exactly once) at ISS and cocotb level, and a formal assertion in seq. Rerun make test + make swtest via test-runner and the seq formal suite. Commit; don't push yet — batch the push with any other RTL fixes found during formal, so only one GDS run starts.
Then sub-step 4: decoder vs isa.yaml.
```

**Prompt 63 (same)**
```
The test-runner keeps leaving `until grep … ; do sleep` waiter shells running after results are in (4 right now). Fix .claude/agents/test-runner.md: run each make target in the foreground with a timeout (e.g. `timeout 1800 make …`), read the summary from its own output, never start polling/until loops or background shells. Commit. Reply 1 line.
```

`/clear`

**Prompt 64 (Opus 5.5, max, auto)**
```
/resume
Continue /phase8-signoff formal from formal/PLAN.md, formal/RESULTS.md and PROGRESS.md: next block is pins + epins (ownership, open-drain/OE safety, en=0 pads drive nothing). Keep the D107 link obligations in mind. RTL bug fixes: fix + regression test + log against rtl-freeze-v1, commit, don't push (batch at end of formal).
```

**Prompt 65 (same)**
```
Yes: move mut.py to scripts/mutate.py, add `make mutate B=<block>` (prints mutants tried / caught / survivors with reason), and record each block's mutant files or generator settings so the counts in formal/RESULTS.md are reproducible. Commit.
Then the link block: prove the D107 obligations (no EXEC to a running engine or with injection pending, no RUN/STEP while pending, one pulse kind per clock) plus link framing, escape handling and H_RDY (D080). If an obligation depends on the host, stop and report before changing RTL. Commit, don't push.
```

**Prompt 66 (same)**
```
SLOTMAP: yes, change HOSTIF 5/9 and ARCH 2.2 to "accepted only while all engines are stopped and none has an injected instruction pending"; log it. No RTL change.
Next block: top — from an arbitrary state, after reset all pad outputs and OEs take their reset values and stay safe (no pad driven) until the host configures pads; no X on outputs after reset. Then timer, shift, sig, lfsr, one per response. Commit each, don't push.
```

**Prompts 67–70 (same)**, sent once each for timer, shift, sig and lfsr:
```
Next block.
```

The formal push was done from the terminal: `git push`.

---

## Phase 8: timing and area

`/clear`

**Prompt 71 (Opus 5.5, high, manual)**
```
/phase8-signoff timing
Context: GDS green with D109 (or paste failures). Placed area is 57.7 % of core (limit 60 %), 12445 buffers and 4247 hold-delay cells logged in PROGRESS as Phase 8 items.
Small steps, one per response: (1) diagnose buffer and hold-cell causes from the latest run artifacts (fetch only needed reports via gh) and propose ranked fixes with estimated µm² saved and timing risk; stop for my approval. Don't change RTL without approval (area freeze, rtl-freeze-v1 log).
```

**Prompt 72 (same)**
```
Approve T2 (SYNTH_STRATEGY AREA 0) + T3 (MAX_FANOUT 16). Reject T1: hold margin stays (unmeasured silicon, no OCV derate; hold failures are unrecoverable post-silicon, setup isn't). Reject T4.
Rule for the run: if slow-corner setup goes negative, drop T2 and keep T3 — don't touch clock or hold margins. Log as a decision.
Also fix the failing docs workflow ("Build docs") — likely docs/info.md format; check the TT docs template rules. Commit both, push once. When the GDS run finishes, report placed area %, cells by category, setup/hold WNS per corner, slew/cap counts, precheck, gl_test. Reply ≤8 lines now.
```

**Prompt 73 (same)**
```
Stop the artifact download — not needed. Get the slow-corner setup WNS/TNS for run 44563418098 from the job text log (gh run view --log-failed, grep the STA summary), or, if needed, fetch only that report via HTTP range requests as before. Then log D120 with the number, commit and push the fallback (fanout 16 only) to start run 7. Reply ≤4 lines.
```

**Prompt 74 (same)**
```
Add to CLAUDE.md: "Never download full GDS artifacts (300+ MB). Use gh run view --log/--log-failed, or HTTP range requests for single files." Commit with the push above.
```

**Prompt 75 (same)**
```
Yes, merge the two D120 lines into one (keep your numbers), commit, don't push (src unchanged, no new GDS).
Rule for run 7: if it fails setup, revert MAX_FANOUT_CONSTRAINT to the run-5 value (back to the known-green config), log D121, push, and accept 57.7 % area. Report run 7 when done: placed area %, cells by category, setup/hold WNS per corner, slew/cap, precheck, gl_test. Reply 2 lines now.
```

---

## Phase 8: gate-level simulation and IR drop

Second terminal, read-only, while GDS ran

**Prompt 76 (Opus 5.5, high, manual)**
```
/phase8-signoff gls
Plan only, read-only (another session is waiting on a GDS run; don't commit or push): which existing cocotb tests (uart/spi/i2c/ws2812/manchester/can, random system test) can run gate-level (GATES=yes) on the latest green GDS netlist, runtime estimate each, and how to fetch only the netlist via gh. Reply ≤8 lines.
```

`/exit`, restart, new conversation

**Prompt 77 (Opus 5.5, high, auto)**
```
/phase8-signoff gls
Step 1: install the IHP ihp-sg13cmos5l PDK locally with ciel at the version ttihp27a uses (no sudo; under ~/.ciel). Get the netlist of green run 44625407065 without downloading the full artifact (tt_submission artifact ~20 MB is OK; never the 300+ MB one). Place it as test/gate_level_netlist.v (gitignored). Run one GL test (test_load_readback) to prove the setup. Add make gl-local T=... . Commit tooling only, don't push. Reply ≤5 lines.
```

**Prompt 78 (same)**
```
Fix scripts/test_summary.py: report PASS/FAIL/SKIP separately ("PASS a, FAIL b, SKIP c of n"); a run with only skips must not print PASS. Check every earlier reported pass count in PROGRESS.md that might have included skips and correct any that did. Commit.
Then Step 2: remove skip=GL from every test that can run at gate level; run the full GL set locally via test-runner (foreground, with timeout). Triage any RTL-vs-GL mismatch (X-propagation, reset/init, timing-sensitive test assumptions) and report before changing RTL. Commit, don't push until all pass. Reply ≤8 lines with pass/fail/skip counts and total runtime.
```

The push was done from the terminal: `git push`.

`/clear`

**Prompt 79 (Opus 5.5, medium, auto)**
```
IR-drop hand estimate per VERIF_PLAN §9 (the flow's IR check is disabled): use run 44625407065's power report (19.5 mW typ; fetch only text logs/reports, never full artifacts) and the PDN geometry from src/config.json + pdn script (stripe width/pitch, metal sheet resistance from the PDK). Estimate worst-case static IR drop to the center of the die, state assumptions, and compare to a 5–10 % budget at 1.2 V. Write docs/IR_DROP.md (≤400 words), commit, push. Reply ≤4 lines.
```

**Prompt 80 (same)**
```
Add to docs/IR_DROP.md: the combined VDD+VSS worst case (64.6 mV, 5.4 %) vs VERIF_PLAN's 5 % target, why it is pessimistic (2× power, single-edge feed, lumped), the realistic combined figure, and that the slow-corner power wasn't used (only in the full artifact). Update VERIF_PLAN §9 status accordingly. Commit, push. Reply 1 line.
```

---

## Cleanup and write-up

`/clear`

**Prompt 81 (Opus 5.5, medium, manual)**
```
Cleanup for submission. Step 1 only: list candidates for removal (untracked build outputs, scratch scripts, stale logs, sim_build, __pycache__, temporary artifacts, duplicate/obsolete docs, the kit's leftover files) with one line each: path, size, why removable, and anything that references it. Do NOT delete. Keep: src/, test/, sw/, formal/, host/, scripts/, docs/ (spec, programs, decisions, IR_DROP, info.md), .github/, info.yaml, Makefile, CLAUDE.md, PROGRESS.md, .claude/ (needed for the AI-use write-up). Also check .gitignore covers build/ and test outputs. Reply as a table.
```

**Prompt 82 (same)**
```
Approved:
- Delete (git rm): test/tb.gtkw, .github/workflows/fpga.yaml, .devcontainer/, .vscode/. Remove any reference to tb.gtkw.
- Move (git mv) docs/spec/ARCH_CANDIDATES.md, REVIEW.md, RTL_REVIEW.md → docs/history/; update every reference (DECISIONS, ARCH, sw/tests/test_first.py, .claude/commands, PROGRESS). Add docs/history/README.md (3 lines: what these are).
- Rewrite test/README.md (≤10 lines): how to run make test, swtest, unit, gl-fetch/gl-local/gl-all, formal, mutate.
- Keep README.md (rewritten at writeup), can_design.md, formal/PLAN.md.
- Local delete (untracked): build/gds_run, build/gl_*, build/formal, build/asm, build/ivl, build/area.json, build/test_first_results.txt, test/sim_build, test/unit/sim_build, test/tb.fst, test/gate_level_netlist.v, test/last*.log, test/unit/last*.log, test results*.xml, all __pycache__ and .pytest_cache. Keep .venv and docs/inputs.
- .gitignore: dedupe the PE block, add .pytest_cache/.
Then run make lint, make test, make swtest via test-runner (foreground). Commit, push. Reply ≤4 lines.
```

`/clear`

**Prompt 83 (Opus 5.5, high → ran as xhigh, accept edits)**
```
Write the submission README.md and its supporting docs. Small steps: one section group per response, commit after each, reply ≤5 lines per step. Never print files.

Rules:
- Keep everything in README.md up to and including the line starting "![](../../workflows/gds/badge.svg)" byte-for-byte untouched; all new content goes after it.
- Style: like https://github.com/NotCleo/GDS-to-RTL README — long single page, first-person, plain and candid, tables carry most content, numbered sections with anchors, contents as grouped tables (number, linked section, "what it covers"), every file reference a repo link (blob/main/... or tree/main/...). No marketing language, no badges added.
- Every number must come from a source in the repo (test, formal/RESULTS.md, PROGRESS.md ledger, GDS run ID, DECISIONS ID). If unsure, mark "(estimate)" or leave TODO(Jojo) — never invent.
- Details live in docs/*.md; README summarises and links. Create/refresh: docs/ARCHITECTURE.md, docs/ISA_REFERENCE.md, docs/HOST_INTERFACE.md, docs/VERIFICATION.md, docs/DESIGN_RATIONALE.md, docs/CAPABILITY.md, docs/PROGRAMS.md, docs/AI_USAGE.md, docs/INTERFACING.md. Design CAPABILITY.md and PROGRAMS.md so future protocol batches only append rows.

README sections, in order:
Top: 1 title + 3-line pitch; 2 results-at-a-glance table; 3 contents (grouped tables: Start here / The chip / Using it / How it was built / Reference).
Start here: 4 the challenge (link Jane Street post, constraints); 5 timeline (from git log dates per phase); 6 how I started (inputs, not copying PIO, files-as-memory); 7 what I built + docs/images/floorplan.png captioned "author's drawing, simplified"; 8 quick start (copy-paste commands).
The chip: 9 sign-off table (GDS run, precheck, DRC, LVS, antenna, STA 3 corners setup/hold, slew/cap, IR estimate, GLS 45/45, area %); 10 pinout (host pins + per-demo pads, open-drain capable pins, from PINS.md); 11 architecture overview; 12 module table (module, purpose, area µm², formal status, unit tests); 13 ISA (formats table + grouped instruction summary, link isa.yaml); 14 design rationale (8–10 key decisions with rejected alternatives, D-IDs).
Using it: 15 software (assembler incl. .deadline/.assume + static bounds, ISS, gen_isa, host SDK host/jspe.py, make targets table); 16 protocols table (protocol, roles/modes, tested configs, measured max rate, words, engines, links to .s and test; mark items added after rtl-freeze-v1); 17 program catalogue (link PROGRAMS.md); 18 complete picture: power-on → RP2350 boot (MicroPython/TT SDK) → project select + clock → reset → image load over host link + CLK check → pad config → RUN → FIFO streaming → events/errors → reload another protocol; 19 interfacing external devices (table: device class, protocol/modes, pins/Pmod, extra parts like pull-ups/transceivers/level shifters, status supported/feasible/not supported; details in INTERFACING.md with one text/mermaid diagram per protocol family; include docs/images/ethernet_concept.png captioned "concept only — not implemented: RMII needs 2 bits every clock at 50 MHz, beyond the 3–4 clk reaction floor"); 20 verification (methods table: method, tool, scope, proves, result — ISS tests, RTL unit, lockstep, random full-chip, bus models, bound-vs-ISS sweep, formal k-induction, mutation, GLS, STA, IR; then protocol × method matrix); 21 limits and known issues stated plainly.
How it was built: 22 AI-assisted workflow (method summary; stage table: stage, model, effort, mode, prompt short form + link to the full prompt text saved under docs/ai/prompts/, tokens, outcome, human decision). For tokens: write scripts/ai_usage.py that sums input/output/cache tokens per Claude Code session from ~/.claude/projects/<this repo>/*.jsonl and maps sessions to stages by date/commit range; mark totals approximate and explain method; leave TODO(Jojo) for stages whose logs are missing. Also state the two README images are author-made (TODO(Jojo): confirm whether AI-assisted). 23 what went wrong; 24 human vs AI split (TODO(Jojo) to confirm).
Reference: 25 repo map; 26 reproducing every result (claim → command); 27 future work (SRAM macro + area freed, per-engine CRC, deeper FIFOs, capture/replay, USB LS, wide-shift); 28 acknowledgements + licence.

Step 1 now: the plan only — list each doc to write and any facts you can't source (as TODO(Jojo)). Wait for my OK.
```

**Prompt 84 (same)**
```
Go: run all steps 1–11 in order without waiting for my OK between them. Commit after each step, update PROGRESS.md "Next" after each. Never print files; reply ≤3 lines per step. If context passes ~60%, finish the current step, commit, update PROGRESS.md and stop.

Answers to your TODOs:
- Images: I'll add docs/images/floorplan.png and ethernet_concept.png; reference them now. They were <made by me by hand / made with AI help — say which>.
- Narrative: <2–3 lines on your motivation/background>.
- Hardware: no board results yet; state clearly that all rates are from simulation (ISS / RTL / gate-level) and label each rate's source in the protocols table.
- 60 MHz: write "≈53 MHz slow corner (estimate from 1.183 ns slack)".
- Pre-11-14 AI use: the Claude Code kit (CLAUDE.md, agents, commands, scripts) and the overall plan were drafted with Claude in a claude.ai chat before the repo existed; no other directories were used.
- Prompts: yes, publish my typed prompts verbatim in docs/ai/prompts/ with personal details removed.
- Human vs AI, per-stage decisions, acknowledgements: draft them and mark TODO(Jojo) for me to confirm.
- ARCH.md memory size: yes, fix it to 128x16 (flop build) in its own spec commit.
- Leave line 1 untouched including the broken fpga badge.
```
(The `< >` choices were left unfilled when this was sent.)

**Prompt 85 (same; latest)**
```
Fill/remove the remaining TODO(Jojo) markers (ignore docs/ai/prompts/ and PROGRESS.md). Keep the tone neutral and factual — no personal narrative anywhere:
1. README:129 motivation: delete the line and anything else that asks for personal narrative; "How I started" stays factual (inputs, approach).
2. Images (README:527, AI_USAGE:78): "<Diagrams drawn by the author; simplified.>" OR "<Diagrams generated with an AI image tool from the author's description; simplified.>"
3. Stage-0 tokens (README:510, AI_USAGE:9, :23): "not recorded (claude.ai chat, no usage export)".
4. Human vs AI split (README:553, AI_USAGE:80) and AI_USAGE:39: keep the drafts as factual statements; remove the TODOs.
5. Acknowledgements (README:622): keep as drafted, add "Anthropic's Claude (see AI usage)."
6. PROGRAMS:33 CAN host example: "planned".
Then `git grep "TODO(Jojo)"` outside docs/ai/prompts and PROGRESS.md must print nothing. Commit and push. Reply ≤3 lines.
```
