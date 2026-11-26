# Host interface

How the RP2350 on the Tiny Tapeout demo board talks to the chip. Normative text: [docs/spec/HOSTIF.md](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/HOSTIF.md) (framing, commands, registers, errors) and [docs/spec/PINS.md](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/PINS.md) section 2 (pins, clocking). Host code: [host/jspe.py](https://github.com/NotCleo/JS-PE/blob/main/host/jspe.py) and [host/examples/](https://github.com/NotCleo/JS-PE/tree/main/host/examples). `host/jspe.py` has not run on a board (no board results yet) and is not executed by the test suite; [test/test_uart.py](https://github.com/NotCleo/JS-PE/blob/main/test/test_uart.py) replays the same load sequence (CLK check, read-back in short `RDMEM`s) against the RTL, and the ISS's `play_bin` replays `.bin` images through the link model.

## 1. Physical layer

| Item | Value |
|---|---|
| Pins | H_RX = `ui_in[3]` (RP2350 GPIO20, UART1 TX), H_TX = `uo_out[4]` (GPIO37, UART1 RX), H_RDY = `uo_out[5]` (GPIO38, UART1 CTS, **0 = ready**) |
| Byte | 8N1, LSB first, idle high |
| Bit time | 16 clk after reset; 8 or 4 clk via register LINK. At 50 MHz: 3.125 / 6.25 / 12.5 Mbaud |
| Clocking | `clk` and the host baud rate come from one clock, so the ratio is exact. The host examples drive clk = 48 MHz because the RP2350 UART clock stays at 48 MHz under the TT firmware, giving 3 Mbaud at 16 clk per bit (PINS.md 2). Option: a PIO link at 4 clk per bit, 12 Mbaud (system clock 96 MHz, `clk` = sys/2; [design](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/pio_link_design.md)); **emulated, not run on hardware** |
| Flow control | H_RDY toward the host (a hardware CTS input stops within one byte); none toward the chip's transmitter, so the host buffers H_TX (≥ 4 KB ring recommended) |

## 2. Framing

`ESC` = 0x1B in both directions; any other byte is stream data. After `ESC` comes one operation byte and its fixed number of raw argument bytes. Host → chip data goes to the RX FIFO of the engine chosen with `SEL`; chip → host data comes from the TX FIFO of the current stream, and the chip announces every stream change.

## 3. Commands (host → chip, byte after ESC)

| Op | Name | Arguments | Effect | Reply |
|---|---|---|---|---|
| 0x1B | literal | — | data byte 0x1B | — |
| 0x00–0x03 | `SEL e` | — | select engine e for data | — |
| 0x20\|m | `STOP m` | — | stop engines in mask m | — |
| 0x30\|m | `RUN m` | — | start engines in m in the same clock | — |
| 0x40\|m | `STEP m` | — | one instruction, then stop | — |
| 0x50\|m | `RST m` | — | engine reset + flush its FIFOs | — |
| 0x60\|e | `EXEC e` | word lo, hi | inject one instruction into stopped engine e | — |
| 0x70 | `WRMEM` | addr lo, hi, n−1, n words | write 1–256 words | — |
| 0x71 | `RDMEM` | addr lo, hi, n−1 | read n words | `ESC 0xC1` + 2n bytes |
| 0x72 | `WRREG` | reg, lo, hi | write a host register | — |
| 0x73 | `RDREG` | reg | read a host register | `ESC 0xC3` lo hi |
| 0x74 | `SYNC` | — | none | `ESC 0xC4` + ID (0x4A10) |

## 4. Chip → host escapes

| Code | Meaning |
|---|---|
| 0x1B | literal data 0x1B |
| 0x00–0x03 | following data and events belong to engine e |
| 0x40\|c | event c (0–63) from `evt #c`, in stream order |
| 0x80\|e, 0x84\|e, 0x88\|e | engine e: overrun set / halted / trapped |
| 0x8C | link error (framing, break, unknown op, data with no stream, refused command, lost memory write) |
| 0xC1, 0xC3, 0xC4 | replies to `RDMEM`, `RDREG`, `SYNC` |

Scheduling: reply in progress, then notices, then engine streams (a stream is served until empty, or 4 entries when another engine has data waiting). Escape sequences are never interleaved — proven for any host behaviour in [formal/RESULTS.md](https://github.com/NotCleo/JS-PE/blob/main/formal/RESULTS.md) (link, a_esc).

## 5. Host registers

| Addr | Name | Access | Contents |
|---|---|---|---|
| 0x00 | ID | R | 0x4A10 |
| 0x01 | LINK | RW | bit time 16 / 8 / 4 clk |
| 0x02 | SLOTMAP | RW | memory slot owners, reset 0xE4; accepted only with all engines stopped |
| 0x03 | SIG | RW | signal flags G7–G0 |
| 0x04 | FILT | W | input filter enables (16 inputs) |
| 0x05 | PINS | R | synchronized input levels |
| 0x06 | ECLR | W | clear overrun / halt-trap flags |
| 0x08–0x0B | EST0–3 | R | engine status: PC, running, waiting, overrun, trap, halted, RX/TX FIFO not empty |
| 0x10–0x1F | PAD_UO0–7, PAD_UIO0–7 | W | pad configuration: logical pin, engine, invert, open-drain, enable |

Engine registers are not host registers; the host reads them by `EXEC`-ing `push R0` and reading the stream.

## 6. Load image and procedure

The assembler writes a `.bin` image: header `"JSPE"`, version 1, record count, then records the host replays in order:

| Record | Host action |
|---|---|
| 0x06 CLK | stop if the host does not drive `clk` at the image's clk_hz (D089) |
| 0x05 RST | `STOP` + `RST` the image's engines |
| 0x02 REG | `WRREG` (SLOTMAP, FILT, PAD with `en` = 0, later PAD with `en` = 1) |
| 0x01 MEM | `WRMEM` in ≤ 256-word chunks, then `RDMEM` read-back and compare |
| 0x03 EXEC | `EXEC` initialisation instructions, then `jmp entry` |
| 0x04 RUN | `RUN` mask |

Pads are enabled only after initialisation, so no pad drives a wrong level while the program is being set up. While other engines keep running, the read-back uses short `RDMEM`s (4 / 11 / 23 words at 16 / 8 / 4 clk per bit) so that their streams never wait more than 2 000 clk behind a reply (D088). Program load: 1 KB in ≈ 3.3 ms at 16 clk per bit (HOSTIF 8; the 128-word memory is 256 bytes).

## 7. Bandwidth

| Item | Value (HOSTIF 8) |
|---|---|
| Raw rate per direction at 50 MHz | 312.5 / 625 / 1250 KB/s at 16 / 8 / 4 clk per bit |
| Overhead | 1 link byte per data byte (2 for 0x1B); stream change +2; event 2 bytes |
| Host → FIFO latency | 9.5 B + 3 clk after the start bit (155 / 79 / 41 clk) |
| FIFOs | 4 bytes each way per engine; H_RDY keeps 2 entries in reserve, so no host byte is lost |
| With the PIO link (B = 4, 48 MHz) | 1.18 MB/s host → chip, 1.2 MB/s chip → host, into a 16 KB DMA ring; sustained only as fast as the consumer: on-board parsing unmeasured, pass-through to the PC over USB CDC assumed 0.8 MB/s (**unmeasured**). Emulated, not run on hardware |

**Replies hold the chip → host link.** A reply (`RDMEM`, `RDREG`, `SYNC`) is sent before any stream byte, so engines that push without waiting must not lose bytes behind it. For a program whose receiver pushes at line rate (USB LS, CAN), the host sends no command with a reply while the bus is active. USB LS measured: one 4-byte `RDREG` reply at a packet tail overflowed the 4-entry FIFO at 16 clk per bit (usb_ls_design.md §9). Line levels are read on the RP2350's own GPIOs instead: the demo board wires every ASIC pin to the RP2350, and those GPIOs stay inputs with no pulls while the chip drives the pins.

## 8. Host SDK (`host/jspe.py`)

| Call | Does |
|---|---|
| `Link(uart_id, clk_hz, B=16, **uart_kw)` | open the link UART (pass `flow=UART.CTS`, `cts=Pin(38)`, a large `rxbuf`) |
| `Link(None, clk_hz, 16, pio=dict(sm=0))` | PIO link instead (state machines sm, sm + 1, one DMA channel; GPIO base 16); per-byte H_RDY: command bytes never wait, data bytes do |
| `set_rate(B)` | `LINK` register, host baud change, `SYNC` (3 tries) |
| `connect(clk_hz, uart_kw, pio, reset)` | PIO link at B = 4, else `reset()` and the hardware UART at B = 16 |
| `forward(out, ms)` | pass-through: raw chip → host bytes to `out` (USB CDC), no decoding on the board; the PC decodes with `jspe.Parser` + host/monitor.py |
| `sync()` | `SYNC`, check ID 0x4A10 |
| `rdreg(r)`, `wrreg(r, v)` | host registers |
| `rdmem(a, n)`, `wrmem(a, words)` | memory |
| `load(path)` | replay a `.bin` (CLK check, RST, REG, MEM + read-back, EXEC, RUN) |
| `send(e, data)` | `SEL e` twice (H_RDY then shows stream e), then data with 0x1B escaped |
| `recv(e)` | stream bytes received from engine e so far |
| `poll()` | parse incoming bytes into per-engine streams, `events` (engine, code, position) and `notes` (0x100 = PIO ring overrun: resync) |

**PIO link status.** Validated without hardware only: `host/jspe.py` itself runs on a PIO model (`sw/pio_emu.py`) clocked with the ISS (sw/tests/test_pio_link.py) and one RTL case (test/test.py `test_pio_link_b4`). The model is checked against two independent references (scripts, not CI): the pico-examples `uart_tx`/`uart_rx` programs reproduce their documented timing on it, and our TX (forced bytes) equals the pico TX waveform cycle for cycle (`scripts/pio_check_pico.py`); our programs give the same waveform, instruction trace and RX FIFO on rp2040-pio-emulator, cycle for cycle (`scripts/pio_check_xemu.py`). Bench checks still open: link pins above GPIO31 (`gpio_base`) in MicroPython, USB CDC rate, on-board parse rate.

Each example in [host/examples/](https://github.com/NotCleo/JS-PE/tree/main/host/examples) enables the project with the TT SDK (`tt.shuttle.tt_um_notcleo_jspe.enable()`), drives `clk` with `tt.clock_project_PWM(48_000_000)`, holds reset while the link UART comes up, then `sync()`, `load()` and streams data. The RP2350 itself is the bus partner (PIO UART/SPI, `SoftI2C`, GPIO capture), so no external device is needed (D008).

## 9. Errors and recovery

Every error is reported, nothing hangs the link: framing errors, breaks, unknown ops, data with no stream selected and refused commands give `ESC 0x8C`; a byte for a full RX FIFO sets that engine's OVR (`ESC 0x80|e`). Recovery from any host-side confusion: a break (H_RX low ≥ 11 bit times), then `SYNC`. After reset or deselect all state is lost and the host reloads (HOSTIF 6, 9).

## 10. Cold start and scenarios

**Cold start** (board power-up to RUN; [scenario_design.md](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/scenario_design.md), D226; calls from tt-micropython-firmware @15ba5ce, [host/coldstart.py](https://github.com/NotCleo/JS-PE/blob/main/host/coldstart.py), not run on hardware):

1. config.ini `[DEFAULT] mode = ASIC_MANUAL_INPUTS` and a `[tt_um_notcleo_jspe]` section with the same mode and `start_in_reset = yes`. The firmware's default `ASIC_RP_CONTROL` turns every `ui_in` into an RP2350 output at boot (`begin_asiconboard`), which fights any Pmod that drives `ui_in`. All `ui_in` DIP switches off.
2. `tt.shuttle.tt_um_notcleo_jspe.enable()`, `tt.reset_project(True)`, `tt.pins.project_clk_driven_by_RP2(True)`, `tt.clock_project_PWM(clk_hz)`.
3. Every chip-facing pin: `.pull = None` (the firmware default is `PULL_DOWN`), `tt.uio_oe_pico.value = 0`.
4. Still in reset: only `ui_in3` (H_RX, driven high) and the scenario's `gpio.rp` pins become push-pull outputs, each after a read that must be low (high = DIP switch or Pmod contention, abort; RP2350 A2 erratum E9 may make a floating input read high, bench check pending); `gpio.pmod` pins and everything else stay inputs without pulls. Then `tt.reset_project(False)`: `uio` high-Z, `uo_out` 0, engines stopped, and no break on H_RX.
5. `SYNC`, check the ID, `load()` the `.bin` (section 6), stimulus; at the end `STOP`, `RST`, `tt.reset_project(True)`, and the `gpio.rp` pins back to inputs.

While engines run: `RDMEM` of at most 4 / 11 words at B = 16 / 8 (D088), one reply operation at a time (a new one replaces the old, D085), and none at all while a bus with a turnaround deadline is active (USB, usb_ls_design.md §4).

**Scenario runner** ([host/scenario.py](https://github.com/NotCleo/JS-PE/blob/main/host/scenario.py), [scenarios/](https://github.com/NotCleo/JS-PE/tree/main/scenarios)): one YAML file names the programs and `-D` options, engines, the pins the RP2350 or a Pmod drives, stimulus (host writes, fault arming or a seeded campaign, partner devices) and expected streams, events, pad waveforms and partner verdicts with tolerances. Before anything is loaded it rejects conflicts: an engine, pad or memory region claimed twice, a chip-driven pad also driven from outside (`:od` / `:shared` excepted), link pads, slot maps that disagree, more than one LFSR user without `lfsr_share`, `CLK_HZ` mismatch. `make scenario S=scenarios/uart_echo.yaml` runs it on the ISS, `BACKEND=rtl` on the RTL (cocotb, lockstep with the ISS over the real link), `BACKEND=board` writes a JSON plan and the `.bin` for [host/scenario_board.py](https://github.com/NotCleo/JS-PE/blob/main/host/scenario_board.py) (MicroPython, not run on hardware: host writes, faults, PWM partners and stream checks; pad and partner-model checks are reported SKIP, other partners become operator steps on pins the RP2350 leaves as inputs, and any SKIP or operator step ends the run INCOMPLETE). Output: one PASS/FAIL line per expectation, then the verdict; exit code 0 / 1, 2 = rejected.
