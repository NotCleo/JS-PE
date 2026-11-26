# Scenario runner + cold start (BACKLOG rank 5, D225)

One YAML file per run; `host/scenario.py` checks it, then plays it on `iss` (pytest), `rtl` (cocotb) or `board` (RP2350). Same file, same verdict.

## Format
```yaml
name: can_uart
clk_hz: 50000000
programs:                        # assembled together; MEMMAP .reserve applies
  - {file: can.s, engines: [0, 1]}
  - {file: uart_tx.s, engines: [2], D: {ENG: 2}}
lfsr_share: [[0, 1]]             # D211
gpio: {drive: {ui_in[7]: partner}}   # every other pad: RP2350 input, no pull
stimulus:                        # at = clk after RUN, or after an event
  - {at: 600, send: {e: 2, bytes: [0x55]}}
  - {at: 0, fault: {e: 0, word: 0x8003, seed: 1}}   # MEMMAP FAULT_W
  - {after: {e: 1, evt: 1}, partner: {model: can_node, frame: [0x0F8, [255]]}}
expect:
  - {stream: {e: 1}, data: [0x123, 0x08], within: 32000}
  - {pad: uo_out[0], uart: {baud: 1000000, tol: 0.02}, data: [0x55]}
  - {no: ovr}                    # no OVR / LATE / 0x8C notice
timeout: 100000                  # clk; hit = FAIL
```
`partner` = a model in sw/tests/models.py (ISS), its cocotb twin (RTL), or a documented Pmod/PIO action (board). Report: one line per expect (measured vs. tolerance), then `PASS`/`FAIL` and exit code.

## Conflict check (before load; any hit rejects)
- Engines: each in one program; an engine without a slot is not run.
- Pads: one owner per pad (programs' `.pad` + `gpio.drive`); host never drives a chip- or Pmod-driven pad or any `uo_out`; link pads `ui_in[3]`, `uo_out[4,5]` never claimed.
- Memory: image overlap, `.reserve`, code below `MEM_DATA`.
- Slot map: all `.slots` agree; default-map timing claims refused under another map.
- LFSR: a user writes `LFSR_*`/`LFCFG` or sets the TAPCFG step bit; ≥ 2 users must form one `lfsr_share` group.
- Clock: every `CLK_HZ` = `clk_hz`.

## Cold start (board)
1. RP2350 boot: every chip GPIO input, pulls off (reset default pull-down).
2. Start `clk` at `clk_hz`, `rst_n` low, select the design on the TT mux.
3. Release `rst_n`: `uio` high-Z, `uo_out` 0, engines stopped (ARCH 9.1).
4. Drive `ui_in[3]` only; `SYNC`, check ID.
5. Drive the `gpio.drive` pins; all others stay inputs.
6. Replay the `.bin` (HOSTIF 7).
7. Stimulus and expects; then `STOP`, `RST`, all GPIOs inputs.

While engines run: `RDMEM` ≤ 4 / 11 words at B = 16 / 8 (D088); one reply op at a time, a new one replaces the old (D085); poll `RDREG ESTe` only between stream reads.
