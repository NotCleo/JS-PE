# Scenario runner + cold start (BACKLOG rank 5, D225)

One YAML file per run; `host/scenario.py` checks it, then plays it on `iss` (pytest), `rtl` (cocotb) or `board` (RP2350). Same file, same verdict.

## Format
```yaml
name: can_uart
clk_hz: 50000000
backends: [iss, rtl, board]       # the runner refuses any other backend
programs:                        # assembled together; MEMMAP .reserve applies
  - {file: can.s, engines: [0, 1], D: {FAULTS: 1}}
  - {file: uart_tx.s, engines: [2], D: {ENG: 2}}
lfsr_share: [[0, 1]]             # D211
gpio: {rp: ["ui_in[7]"], pmod: ["uio[4]:od"]}   # ui_in[3] always rp; rp pins push-pull only; all else input, no pull
stimulus:                        # at = clk after RUN, or after an event
  - {at: 600, send: {e: 2, data: [0x55]}}
  - {at: 0, fault: {e: 0, word: 0x9001}}          # armed, TYPE 1, to FAULT_W + 0 (must be .reserve'd)
  - {at: 0, partner: {model: can_node, pin: "uio[4]:od", bit: 100, delay: 15000, frame: [0x0F8, [255]]}}
expect:
  - {stream: {e: 1}, data: [0, 0xF8, 0x01, 0xFF], mask: {0: 7}, within: 32000}
  - {pad: "uo_out[0]", uart: {bit: 50, tol: 0.02}, data: [0x55]}
  - {clean: [ovr, late, trap]}   # (not `no:`: YAML reads it as false)
timeout: 100000                  # clk; hit = FAIL
```
`partner` = a model in sw/tests/models.py (ISS), its cocotb twin (RTL), or a documented Pmod/PIO action (board). Report: one line per expect (measured vs. tolerance), then `PASS`/`FAIL` and exit code.

## Conflict check (before load; any hit rejects)
- Engines: each in one program; an engine without a slot is not run.
- Pads: one driver per pad (programs' `.pad`, `gpio.rp`, `gpio.pmod`); `gpio.rp` only `ui_in` / chip-released `uio`, never `uo_out`; link pads `ui_in[3]`, `uo_out[4,5]` never claimed.
- Memory: image overlap, `.reserve`, code below `MEM_DATA`.
- Slot map: all `.slots` agree; default-map timing claims refused under another map.
- LFSR: a user writes `LFSR_*`/`LFCFG` or sets the TAPCFG step bit; ≥ 2 users must form one `lfsr_share` group.
- Clock: every `CLK_HZ` = `clk_hz`.

## Cold start (board)
TT firmware (tt-micropython-firmware @15ba5ce): modes `RPMode.SAFE`, `ASIC_RP_CONTROL`, `ASIC_MANUAL_INPUTS` (src/ttboard/mode.py). RP_CONTROL turns every `ui_in` into an RP output (pins.py `begin_asiconboard`); MANUAL_INPUTS leaves `ui_in`, `clk`, `rst_n` inputs (`begin_asic_manual_inputs`). Boot builds `DemoBoard.get()` in the config.ini `[DEFAULT] mode` (main.py); default pull is `PULL_DOWN` (gpio_map_dbv3.py `default_pull`), despite "no pulls" in the SAFE docstring.
1. config.ini: `[DEFAULT] mode = ASIC_MANUAL_INPUTS`; section `[tt_um_notcleo_jspe]` `mode = ASIC_MANUAL_INPUTS`, `start_in_reset = yes`. All `ui_in` DIP switches off.
2. `tt.shuttle.tt_um_notcleo_jspe.enable()`; `tt.reset_project(True)`; `tt.pins.project_clk_driven_by_RP2(True)`; `tt.clock_project_PWM(clk_hz)` (what demoboard.py does for MANUAL_INPUTS + `clock_frequency`).
3. Every `ui_in*`, `uio*`, `uo_out*`: `.pull = None` (StandardPin re-inits with it); `tt.uio_oe_pico.value = 0`.
4. Still in reset: for `ui_in3` and each `gpio.rp` pin read it (high = DIP or Pmod contention → abort, as `begin_asiconboard`; RP2350 A2 erratum E9 may make a floating input read high, bench check pending), set its level (H_RX high), `.mode = Pin.OUT`. `gpio.pmod` pins and partners without an on-board model stay inputs.
5. `tt.reset_project(False)`: `uio` high-Z, `uo_out` 0, engines stopped (ARCH 9.1); H_RX is already idle high, so no break.
6. `SYNC`, check ID, clear notices; replay the `.bin` (HOSTIF 7).
7. Stimulus and expects; then `STOP`, `RST`, `tt.reset_project(True)`, every `gpio.rp` pin back to `Pin.IN`. Any SKIP or operator step ends the board run INCOMPLETE, never PASS.

While engines run: `RDMEM` ≤ 4 / 11 words at B = 16 / 8 (D088); one reply op at a time, a new one replaces the old (D085); poll `RDREG ESTe` only between stream reads.

## Vocabulary (step 3, host/scenario.py)
Pins: `:od` (pulled-up wired AND), `:shared` (half duplex the protocol arbitrates, USB). Partners: `uart`, `can_node` (verdict: frames, acked), `square` (board: PWM), `wire` (jumper from a chip pad), `usb_host` (needs `link: wire`: real host link, RP2350 stand-in writes the buffer); `idle` = levels before start. Stimulus: `send` (`data` or `can`), `fault`, `campaign` (host/fault.py seed → fault + send per character), `partner`. Expects: `stream`/`events`/`fifo` (`mask`, `prefix`, `within`, `from_campaign` template per fault type), `decode` (host decoder `module.fn`, `field`, `skip`, `tol`), `pad` + `uart`, `spi`, `partner`, `clean` (ovr, late, trap, halt, undef, notes; notes only with a link: wire mode and RTL). `at`/`within` count clk from RUN (ISS) or from the end of the load (RTL, which includes the RUN record). A run that hits `timeout` before all expects hold adds a FAIL line. Backends: `make scenario S=… [BACKEND=rtl|board]`; board = JSON + `.bin` for host/scenario_board.py (send/fault/PWM, stream-type expects; the rest SKIP or operator steps).
