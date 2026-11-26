![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg)

# Cleo: a programmable protocol emulator ASIC

Cleo is my entry to the [Jane Street protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/): a Tiny Tapeout chip on IHP 130 nm that runs serial protocols as small programs instead of fixed hardware.

- Four small engines each hold one instruction, `guard : operation`, and fire it in the clock its event arrives, so a pin edge becomes a pin change in 3–4 clocks.
- Nothing protocol-specific is built in. UART, SPI and I2C were the brief; WS2812, Manchester and CAN 2.0A were added later with no RTL change.
- Everything is verified in simulation, at RTL and at gate level. Nothing has run on silicon or a board yet.

**Contents:** [At a glance](#1-at-a-glance) · [The challenge](#2-the-challenge) · [How it works](#3-how-it-works) · [Quick start](#4-quick-start) · [Protocols](#5-protocols) · [On the demo board](#6-on-the-demo-board) · [Pins and wiring](#7-pins-and-wiring) · [Verification and sign-off](#8-verification-and-sign-off) · [Limits](#9-limits) · [How it was built](#10-how-it-was-built) · [Repository map](#11-repository-map) · [Future work](#12-future-work) · [Acknowledgements and licence](#13-acknowledgements-and-licence)

----

## 1. At a glance

| Item | Value |
|---|---|
| Process | IHP SG13 130 nm through the Tiny Tapeout IHP template, 8x4 tiles |
| Architecture | 4 engines, 512 x 16 shared program memory (SRAM macro), 67 instructions, 3-pin host link |
| Protocols | UART, SPI controller and target, I2C controller and target; not designed in: WS2812, Manchester, CAN 2.0A, USB low speed (simulation) |
| Clock | 50 MHz (about 57 MHz possible, estimate) |
| Area | 37.9 % of the core including the SRAM macro (my limit: 60 %) |
| Sign-off | Tiny Tapeout precheck, DRC, LVS and antenna pass; setup and hold met in all corners |
| Tests | 45/45 system tests at RTL and at gate level; 149 formal properties proven |
| Hardware | not tested on a board or silicon yet |

----

## 2. The challenge

Jane Street asked for an open-source, general-purpose protocol emulator, "reprogrammable enough to support new protocols *after* fabrication". UART, SPI and I2C come first; low-speed USB and 10 Mbit Ethernet are stretch goals. The post points at RP2040 PIO and TI PRU and asks what you would do differently; Cleo is not modelled on either.

The constraints: 8x4 Tiny Tapeout tiles, 24 I/O pins (only the 8 bidirectional ones can be open-drain), about 10 ns pad round trip, a clock from the RP2350 on the demo board, and a chip that loses its program when deselected, so the host must be able to reload it at any time.

I optimised for one thing: running protocols that were not designed in.

----

## 3. How it works

![](https://github.com/NotCleo/JS-PE/blob/main/docs/images/floorplan.jpeg?raw=true)

*Author's drawing, simplified.*

| Part | What it does |
|---|---|
| Engines (4) | each waits on one instruction and acts in the clock its event (timer, pin edge, host FIFO) arrives; each has a 16-bit timer, a shifter with taps for parity and CRC, three registers, an ALU and four logical pins that can be bound to any input |
| Program memory | 512 x 16 words (one IHP SRAM macro) shared by time slots: each engine owns a fixed slot, so fetch time never depends on the other engines and timing can be checked before the program runs |
| Pads | every output pad has exactly one owner and only the host can enable it, so engines cannot fight over a pin |
| Host link | 3 pins to the RP2350: loads programs, starts and stops engines, and streams bytes and events both ways |
| Toolchain | assembler with a static timing check, cycle-accurate simulator, MicroPython host library; one ISA file generates the tables for all of them and for the RTL |

Instructions are 16 bits wide: 67 of them in 6 formats, covering pin actions, branches, arithmetic, host streams, and timer and shifter control.

Code identifiers use the prefix jspe_ (the project's working name); the chip is Cleo.

Why this shape:

- A fixed hardware lane per protocol family only serves the protocols I anticipated, and a single CPU runs out of reaction time once two protocols run together.
- Events are latched, so a busy engine answers late instead of silently missing an edge.
- Line coding, bit stuffing and CRC come from generic shifter taps, not dedicated blocks. CAN was built from them.
- The assembler refuses to build a program that can miss a timing deadline it was given.

More: [architecture](https://github.com/NotCleo/JS-PE/blob/main/docs/ARCHITECTURE.md), [instruction set](https://github.com/NotCleo/JS-PE/blob/main/docs/ISA_REFERENCE.md), [design rationale](https://github.com/NotCleo/JS-PE/blob/main/docs/DESIGN_RATIONALE.md).

----

## 4. Quick start

Needs Python 3.12, Icarus Verilog, Verilator and Yosys; formal also needs the OSS CAD Suite.

```sh
git clone https://github.com/NotCleo/JS-PE && cd JS-PE
python3 -m venv .venv && . .venv/bin/activate
pip install -r test/requirements.txt
make lint        # Verilator lint
make swtest      # assembler and simulator tests
make test        # system tests, RTL against the simulator
make formal B=arb
```

Run a scenario (programs, pins, stimulus and expected traffic in one YAML file; conflicts are rejected before load):

```sh
make scenario S=scenarios/uart_echo.yaml               # ISS: PASS/FAIL per expectation
make scenario S=scenarios/uart_echo.yaml BACKEND=rtl   # same file on the RTL (cocotb, lockstep with the ISS)
make scenario S=scenarios/can_uart.yaml BACKEND=board  # build/can_uart.json + .bin for host/scenario_board.py
```

Seven examples in [scenarios/](https://github.com/NotCleo/JS-PE/tree/main/scenarios): UART echo, UART → SPI bridge, seeded fault campaign, UART monitor, pulse meter, USB enumeration, CAN + UART on all four engines. The board backend and the cold start ([HOST_INTERFACE 10](https://github.com/NotCleo/JS-PE/blob/main/docs/HOST_INTERFACE.md)) are written but not run on hardware.

Assemble a program:

```sh
python sw/jspe_asm.py -D CLK_HZ=48000000 \
    docs/spec/programs/uart_tx.s docs/spec/programs/uart_rx.s -o uart
```

Other targets: `make unit B=<module>` (block tests), `make area` (synthesis area), `make mutate B=<block>`, `make gl-all` (gate-level tests). `MEM=flop` on `make lint`, `make test` or `make area` builds the 128 x 16 flip-flop fallback instead of the SRAM macro (assemble with `--mem 128`).

----

## 5. Protocols

All rates are from simulation.

| Protocol | What it does | Fastest rate |
|---|---|---|
| UART | TX and RX; 5–9 data bits, parity, 1–2 stop bits, RTS/CTS | 1 Mbaud tested (TX up to 4.5 Mbaud by analysis) |
| SPI controller | modes 0–3, MSB or LSB first, 1–16-bit words | 6.25 MHz |
| SPI target | register device, modes 0–3 | 4.44 MHz |
| I2C controller | START, repeated START, STOP, ACK/NACK, clock stretching | 100 and 400 kHz to the standard's timing; 0.83 MHz (Fm+) |
| I2C target | 7-bit address, read and write, clock stretching | 400 kHz |
| WS2812 \* | LED strip driver | the LED's own rate (794 kbit/s) |
| Manchester \* | TX, and RX with clock recovery | 2.27 Mbit/s TX, 2.78 Mbit/s RX |
| CAN 2.0A \* | 11-bit IDs, arbitration, bit stuffing, CRC-15, ACK, error flags | 500 kbit/s |

\* Not designed in: run on the frozen RTL with no RTL change. WS2812 and Manchester were sketched while the ISA was being written, so they may have shaped it; CAN was written entirely afterwards. The one RTL change since the freeze is a reset bug fix found by formal verification.

Unique capabilities:

- Fault injection: `-D FAULTS=1` builds of the UART TX, I2C target, SPI target and CAN programs inject one armed fault each (wrong parity, low stop bit, break; NACK, extra stretch, ignored address, stuck SDA; flipped bits, short/long SPI words; a bad CAN CRC) at a chosen position, armed by one memory write from the host; seeded random campaigns are reproducible, and every fault is shown to reach the partner's receiver (test EI1). The fault builds trade speed: UART TX ≤ 1.08 Mbaud, SPI target ≤ 1 MHz.

Programs: [docs/spec/programs/](https://github.com/NotCleo/JS-PE/tree/main/docs/spec/programs). Details per protocol: [capability](https://github.com/NotCleo/JS-PE/blob/main/docs/CAPABILITY.md), [programs](https://github.com/NotCleo/JS-PE/blob/main/docs/PROGRAMS.md).

----

## 6. On the demo board

None of this has run on a board yet; the same sequence runs against the RTL in the tests.

1. Select the project, clock it from the RP2350 at 48 MHz and reset it (`tt.shuttle.tt_um_notcleo_jspe.enable()`, `tt.clock_project_PWM(48_000_000)`). Put `mode = ASIC_MANUAL_INPUTS` in the firmware's config.ini so the RP2350 drives only the `ui_in` pins a scenario names, not all eight (cold start: [HOST_INTERFACE 10](https://github.com/NotCleo/JS-PE/blob/main/docs/HOST_INTERFACE.md), [host/coldstart.py](https://github.com/NotCleo/JS-PE/blob/main/host/coldstart.py)).
2. Connect with the host library: `sync()` checks the chip ID.
3. `load("x.bin")` checks the image was built for the same clock, writes the program, reads it back, sets up the pads and starts the engines.
4. Exchange data with `send` and `recv`; program events and errors arrive through `poll`.
5. To switch protocol, load another image; engines you don't replace keep running. After a deselect or reset, load again.

```sh
mpremote cp uart.bin host/jspe.py :
mpremote run host/examples/uart.py
```

Demos for UART, SPI, I2C, WS2812 and Manchester are in [host/examples/](https://github.com/NotCleo/JS-PE/tree/main/host/examples). The RP2350 acts as the bus partner, so no external device is needed. Details: [host interface](https://github.com/NotCleo/JS-PE/blob/main/docs/HOST_INTERFACE.md).

Optional faster link: `host/jspe.py` can run the host link on the RP2350's PIO at 4 clk per bit (12 Mbaud, about 1.2 MB/s each way, 4x the UART), with a DMA ring and a pass-through mode that forwards raw bytes to the PC over USB. **Emulated, not run on hardware:** the host code runs on a PIO model with the ISS and the RTL, and the model is cross-checked against the pico-examples UART programs and an independent PIO emulator ([design](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/pio_link_design.md)).

----

## 7. Pins and wiring

Only the three host-link pins are fixed; every other pad is assigned by the program. Defaults of the shipped programs:

| Pins | Use |
|---|---|
| `ui_in[3]`, `uo_out[4]`, `uo_out[5]` | host link RX, TX, ready (fixed) |
| `uo_out[0]`, `ui_in[7]` (+ `uo_out[1]`, `ui_in[2]`) | UART TX, RX (+ RTS, CTS) |
| `uio[0]`–`uio[3]` | SPI CS, MOSI, MISO, SCK |
| `uio[6]`, `uio[5]` | I2C SCL, SDA (open-drain) |
| `uio[4]` | CAN (open-drain) |
| `uo_out[2]` | WS2812 data |
| `uo_out[3]`, `ui_in[4]` | Manchester TX, RX |

All I/O is 3.3 V with no on-chip pull-ups, and only `uio` pads can be open-drain or tri-state.

| Device | Extra parts |
|---|---|
| UART, SPI, Manchester at 3.3 V | none |
| I2C | two 4.7 kΩ pull-ups to 3.3 V |
| WS2812 strip at 5 V | 74AHCT1G125 level shifter, 330 Ω resistor, 5 V supply with ≥ 470 µF |
| CAN bench (wired-AND, no transceiver) | one pull-up on `uio[4]` |
| RS-232, RS-485, CAN with a transceiver, 5 V logic | a transceiver or level shifter (feasible, not tested) |

Full pin table and wiring diagrams: [PINS.md](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/PINS.md), [interfacing](https://github.com/NotCleo/JS-PE/blob/main/docs/INTERFACING.md).

----

## 8. Verification and sign-off

The simulator was written from the spec before the RTL. Every program was proven on it against independent protocol models, and the RTL was then compared with it on every output pad in every clock.

| Method | Result |
|---|---|
| Assembler and simulator tests | 350 pass |
| RTL block tests | 56 pass |
| System tests, RTL and simulator in lockstep | 45/45 |
| Random programs on the full chip against the simulator | 3 M clocks clean |
| The same 45 tests on the post-layout netlist | 45/45 |
| Formal proofs (SymbiYosys, 11 blocks) | 149 properties proven |
| Mutation testing of the proofs | 211 of 213 mutants caught, 2 equivalent |

Sign-off of the shipped SRAM build ([GDS run 45591139824](https://github.com/NotCleo/JS-PE/actions/runs/45591139824) on main, all jobs green; same layout and figures as the sram-retry run 45553919240):

| Check | Result |
|---|---|
| Tiny Tapeout precheck | pass |
| DRC (KLayout, route), LVS, antenna | clean |
| Setup slack at 50 MHz, slow / typ / fast | 2.68 / 9.08 / 12.66 ns |
| Hold slack, slow / typ / fast | 0.602 / 0.284 / 0.105 ns |
| Max slew / max cap | 0 / 1 (SRAM output pin, see limits) |
| Placed area | 37.9 % of the core, macro included |
| Power (typ) | 10.0 mW |
| IR drop | PSM 0.31 mV; hand estimate 1.9 % worst per net ([IR_DROP](https://github.com/NotCleo/JS-PE/blob/main/docs/IR_DROP.md)) |
| Gate-level tests | 45/45 on this run's netlist |

The 128 x 16 flip-flop fallback (`-DJSPE_MEM_FLOP`) was signed off before on [run 44625407065](https://github.com/NotCleo/JS-PE/actions/runs/44625407065): 57.3 % placed, setup slack 1.18 ns slow, GL 45/45.

Details: [verification](https://github.com/NotCleo/JS-PE/blob/main/docs/VERIFICATION.md), [formal results](https://github.com/NotCleo/JS-PE/blob/main/formal/RESULTS.md).

----

## 9. Limits

- No hardware results: every rate is from simulation, and the host code has never run on an RP2350.
- 512-word program memory shared by all four engines: CAN (128 words) fits next to UART TX + RX, but every image still has to fit in 512 words.
- Known issue: one max-capacitance violation on the SRAM output pin `A_DOUT[13]` (0.081 pF against the macro's 0.064 pF limit, all corners; precheck and timing still pass). Its net runs about 400 µm to a buffer, and the router's estimate before detailed routing is half the final value, so post-route repair misses it; no single flow setting fixes it (D131).
- I2C has no multi-controller arbitration. CAN leaves fault confinement to the host and flags 29-bit frames as errors.
- No formal proofs for the ALU and the memory, and no code coverage measured.
- The simulator and the RTL come from the same spec, so a misreading could be shared; the independent protocol models, formal proofs and reviews are the check against that.
- USB low speed runs only in simulation (ISS and RTL lockstep, 48 MHz, [usb_ls.s](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/usb_ls.s)); the RP2350 handles descriptors and token keys. Ethernet is not supported: it needs faster reactions than 3–4 clocks at 50 MHz.

![](https://github.com/NotCleo/JS-PE/blob/main/docs/images/ethernet_concept.jpeg?raw=true)

*Concept only, not implemented: RMII needs 2 bits every clock at 50 MHz.*

----

## 10. How it was built

I built Cleo with Claude Code in seven days (2026-11-14 to 2026-11-20). The working rules, the phase commands and the plan were drafted with Claude in a chat before the repo existed. The spec came before the code; when something was unclear, Claude stopped and asked, and every decision went into [docs/DECISIONS.md](https://github.com/NotCleo/JS-PE/blob/main/docs/DECISIONS.md). Sessions were short, so all state lived in files and commits.

Before writing the spec I reviewed existing programmable-I/O approaches (RP2040 PIO, TI PRU) and an earlier public entry in this competition. The architecture candidates were drafted first, without that review, and compared against it afterwards. Cleo differs deliberately: `guard : operation` instructions, latched events, time-division fetch with proven latency bounds, four engines sharing one program memory, and formal sign-off.

| Stage | Model | Mode | Output tokens | Result |
|---|---|---|---|---|
| 0 plan | Claude (chat) | — | not recorded | rules, phase commands, plan |
| 1 requirements | Opus 5.5 | accept edits | 63k | requirements and scope |
| 2 architecture + spec | Fable 5.2 | accept edits | 1.0M | three candidate designs, one chosen; full spec |
| 3 spec review | Fable 5.2 | accept edits | 470k | adversarial review, ISA trimmed |
| 4 assembler + simulator | Opus 5.5 | auto | 529k | assembler, simulator, protocol models |
| 5 skeleton | Opus 5.5 | auto | 167k | first GDS runs |
| 6 RTL | Opus 5.5, Fable 5.2 | plan, auto | 854k | all blocks, integrated |
| 7 protocols | Opus 5.5 | auto | 1.7M | 9 programs running |
| 8 sign-off | Opus 5.5 | auto | 815k | formal proofs, timing closure, gate-level tests |
| 9 write-up | Opus 5.5 | auto | 106k | docs |
| **Total** | | | **≈ 5.7M** | |

| Work | Me | Claude |
|---|---|---|
| Scope and architecture | answered the open questions, chose the architecture and changed it | proposed the questions, wrote three candidates and the spec |
| Decisions | about 40 of the ~120 logged decisions | proposed the rest |
| Code, tests, proofs, programs | reviewed through test results and review reports | wrote them |
| Flow and docs | ran and watched the GDS runs, set the rules for the write-up | changed configs, diagnosed failures, wrote the text |
| Images | drew the two images in this README | — |

More: [AI usage](https://github.com/NotCleo/JS-PE/blob/main/docs/AI_USAGE.md).

----

## 11. Repository map

| Path | What |
|---|---|
| [src/](https://github.com/NotCleo/JS-PE/tree/main/src) | RTL (Verilog-2005) |
| [test/](https://github.com/NotCleo/JS-PE/tree/main/test) | cocotb system tests and block tests |
| [sw/](https://github.com/NotCleo/JS-PE/tree/main/sw) | assembler, simulator, ISA generator and their tests |
| [formal/](https://github.com/NotCleo/JS-PE/tree/main/formal) | formal proofs and results |
| [host/](https://github.com/NotCleo/JS-PE/tree/main/host) | MicroPython host library and demos, scenario runner (PC) |
| [scenarios/](https://github.com/NotCleo/JS-PE/tree/main/scenarios) | scenario files for host/scenario.py (ISS, RTL, board) |
| [docs/spec/](https://github.com/NotCleo/JS-PE/tree/main/docs/spec) | the spec and the example programs |
| [docs/](https://github.com/NotCleo/JS-PE/tree/main/docs) | reader docs and the decision log |
| [docs/history/](https://github.com/NotCleo/JS-PE/tree/main/docs/history), [docs/ai/](https://github.com/NotCleo/JS-PE/tree/main/docs/ai) | design-process records and the prompt list |
| [.claude/](https://github.com/NotCleo/JS-PE/tree/main/.claude), [CLAUDE.md](https://github.com/NotCleo/JS-PE/blob/main/CLAUDE.md) | Claude Code rules, phase commands and subagents |

----

## 12. Future work

- One CRC unit per engine, so two CRC protocols can run at once.
- Deeper FIFOs, so running streams tolerate a busy host link for longer.
- Capture and replay of pin traffic, for protocols nobody has written a program for yet.
- USB low speed on hardware (the HID demo in host/examples/usb_hid.py has not run on a board).
- 32-bit shifts for long fields.

----

## 13. Acknowledgements and licence

- Jane Street for the competition and the shuttle; Tiny Tapeout and IHP for the template, the flow and the process; the open-source EDA tools (LibreLane/OpenROAD, Yosys, SymbiYosys, Icarus Verilog, Verilator, cocotb); Ken Pettit's IHP SRAM macro walkthrough; Anthropic's Claude.
- Licence: [Apache-2.0](https://github.com/NotCleo/JS-PE/blob/main/LICENSE).
