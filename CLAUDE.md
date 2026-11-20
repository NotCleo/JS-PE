# Cleo — Programmable Protocol Emulator ASIC (Jane Street competition, Tiny Tapeout IHP)

Goal: a reprogrammable protocol-emulator chip. Primary judging axis = running protocols NOT designed in.
v1 scope: UART, SPI, I2C fully implemented + verified. Everything else is later.

## Hard constraints (edit the <FILL> fields once, then never again)
- Process IHP SG13 CMOS5L via TT IHP template + LibreLane. Must pass TT precheck.
- Tiles: 8x4. Area: placed cell area + macro ≤ 60 % of die (D051); measure with make area, track in the PROGRESS.md ledger.
- Top module: tt_um_notcleo_jspe. Every other module is prefixed `jspe_` (TT shuttle shares one namespace).
- Pins: ui_in[7:0] input-only, uo_out[7:0] output-only, uio[7:0] bidir (uio_oe=1 drives). clk, rst_n separate.
  Open-drain = out 0 + toggle uio_oe. Only uio pins can be open-drain/bidir.
- Clock from RP2350 host, target 50 MHz (config.json CLOCK_PERIOD=20), stretch 60 MHz. ~10 ns pad round trip.
- Design loses state when deselected → program must be reloadable from host at any time.
- Area freeze: from Phase 7 on, RTL changes are bug fixes only. Every RTL commit reports synthesized area delta (make area); any increase >2k µm² synthesized needs my approval first. Prefer program/ISS fixes over RTL.
- Host = RP2350 on TT demo board (MicroPython). Assembler runs on PC. External parts go on a Pmod, documented.

## Source of truth
docs/spec/REQUIREMENTS.md → ARCH.md → ISA.md (+ isa.yaml, machine-readable) → PINS.md → HOSTIF.md → VERIF_PLAN.md.
Spec changes come BEFORE code changes. Every non-trivial choice → one line in docs/DECISIONS.md.
If the spec is ambiguous or wrong: stop, state the problem + proposed fix in ≤10 lines, wait.

## Layout
- src/        RTL, flat (TT requires it). Every new file → add to info.yaml source_files AND test/Makefile PROJECT_SOURCES.
- test/       top-level cocotb (template tb.v/test.py). test/unit/<block>/test_<block>.py per-block cocotb.
- sw/         jspe_asm.py (assembler), jspe_iss.py (golden ISS), gen_isa.py (isa.yaml → src/jspe_isa.vh), sw/tests (pytest).
- formal/     SymbiYosys .sby + property files.
- host/       MicroPython for RP2350 (loader + demos).
- docs/spec/  specs; docs/spec/programs/*.s example programs. docs/info.md = TT datasheet page.
- docs/inputs/ raw inputs (gitignored).

## Commands (always use these; they print terse output)
- `make unit B=<module>`  block test   | `make test` top-level test | `make swtest` pytest for sw/ (skips slow) | `make swtest-all` incl. slow (>20 s)
- `make lint`             verilator -Wall | `make area [B=<module>]` yosys cell count per module
- For running tests prefer the `test-runner` subagent. Never paste full sim logs. Never open VCD/FST files.
- Never download full GDS artifacts (300+ MB). Use gh run view --log/--log-failed, or HTTP range requests for single files.

## RTL rules
- Verilog-2005 + `default_nettype none` (must pass iverilog -g2012, verilator --lint-only -Wall, yosys).
- Single clock domain `clk`. No generated/gated clocks, no latches, no combinational loops; use clock enables.
- Every external input enters through a 2-FF synchronizer (one shared pin-sync block). Edge detect after sync.
- Reset: rst_n synchronized once at top; internal synchronous active-low reset. Reset every control flop; datapath flops may skip reset.
- Registered outputs to pads (uo_out, uio_out, uio_oe). All outputs assigned; unused inputs folded into `_unused`.
- One module per file, filename = module name. Named port connections only. localparam for widths/encodings; encodings come from src/jspe_isa.vh (generated — never hand-edit).
- FSMs: localparam states, one sequential block + one combinational next-state block with defaults at top.
- No `initial` in synthesizable code. No `#delays`. No `casex`; `casez` only for decode with explicit default.

## Output and token rules
- Replies ≤10 lines unless asked. No restating the plan, no summaries of files you just wrote.
- Edit files directly; never print whole files. Show diffs only when asked.
- Code comments: one short line, only where intent isn't obvious. No docstrings, no banners, no ASCII art.
- Python: plain functions, type hints optional, no frameworks. Light file types only (.v .vh .py .yaml .sby .md .s .txt).
- Don't create files outside the layout above without saying why.
- Prefer Grep/targeted reads (line ranges) over reading whole large files.

## Workflow rules
- Per task: tests first → implementation → lint → tests pass → area check → commit → update PROGRESS.md.
- After EVERY completed subtask: `git add -A && git commit -m "<scope>: <what>"` and update PROGRESS.md (≤15 lines).
- If context is past ~60% or usage is running low: finish the current edit, commit, update PROGRESS.md "Next", stop.
- No new instruction / HW block / protocol without a DECISIONS.md line: which protocols need it, est. cells, alternative rejected.
- Don't implement beyond the task's "Done =" criteria. Don't refactor unrelated code.
- Before calling a protocol done: run the `reviewer` subagent and fix its findings.
- Never reason through a large design in one response. For any task with design + implementation, first write a short design note to a file and commit it, then implement in steps of ≤1 file or ≤1 feature per response.
