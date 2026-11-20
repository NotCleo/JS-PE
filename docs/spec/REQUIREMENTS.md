# REQUIREMENTS — Cleo programmable protocol emulator

Sources: Jane Street brief (2026-11-09), TT IHP template, checklist. MUST = v1 blocker; SHOULD = v1 goal; STRETCH = later.

## 1. Hard constraints

| Item | Constraint |
|---|---|
| Process | IHP SG13 CMOS5L, TT IHP Verilog template (main), LibreLane via `TinyTapeout/tt-gds-action@ttihp27a`. |
| Area | 8x4 tiles ≈ 1.0 mm² (tile ≈ 202×155 µm). Limit (D051): placed standard-cell area + SRAM macro with halo ≤ 60 % of the core area (1 208 173 µm² from the GDS flow → ≈ 725k µm², D063); cell area from `make area LIB=…`, x 1.2 for clock-tree and timing buffers until Phase 5 measures it; cell counts are recorded, not judged. Program memory: one 1 KB IHP SRAM macro is the primary build (D003); the ≤ 256 B flop fallback must work and meet the same limit, and is built only if the macro fails the flow or precheck (it did: D062, the flop build is current). |
| Program memory (known constraint, D062) | 128 x 16 words in the flop build, shared by all four engines (code + `.word` tables); 9-bit addresses alias modulo 128. Sizes: uart_tx 8, uart_rx 8, spi_ctrl 18, spi_tgt 20, i2c_ctrl 41, i2c_tgt 30, ws2812 9, manchester 23 (TX + RX, 2 engines; 20 with `CVCHK = 0`, D099), can 128 (TX + RX, 2 engines, D102–D104). CAN uses 128/128 words and runs alone on the flop build (relocate with `-D ORG_TX=0 -D ORG_RX=62`). Every combination of these programs that fits on the four engines fits in memory: largest is i2c_ctrl + i2c_tgt + spi_tgt + spi_ctrl = 109 words (19 spare); manchester + any two others ≤ 94; the largest pad-compatible four-program image assembled with `--mem 128` is spi_ctrl + spi_tgt + i2c_ctrl + ws2812 = 88. Default `ORG`s above 127 (i2c_tgt 144, ws2812 176, manchester 192/208) must be relocated with `-D file.s:ORG=…` (D047). A new program must fit next to the others it runs with; the 512-word macro would lift this (deferred retry, VERIF_PLAN 9). |
| Top / names | `tt_um_notcleo_jspe`; every other module `jspe_*`. |
| Clock | Single `clk` from RP2350. Timing MUST close post-PnR at 50 MHz (`CLOCK_PERIOD=20`); 60 MHz only if slack allows (D007). |
| Pad timing | ≈ 10 ns pad+mux round trip; max output toggle ≈ 66 MHz (estimates, no silicon yet). |
| Reset | `rst_n` from button/RP2350; flops power up random → every control flop reset. `ena` always 1 when powered (ignore). |
| Deselect | Deselected design is powered down and loses all state, including program memory. Host must be able to reload the full program after any reset/select. |
| Flow | Must pass GDS build, precheck, `RUN_LINTER=1`, GL test (`test/` cocotb, `GATES=yes`) within GitHub's 6 h limit. `info.yaml`: tiles, top_module, all sources, pinout, `clock_hz`. `docs/info.md` = datasheet. Top-level tests use top ports only (no hierarchical probes) so they run at GL. |

### Pins (24 digital, 3.3 V I/O on demo board; no analog pads, no on-chip pull-ups)

| Group | Count | Capability | Usable for |
|---|---|---|---|
| `ui_in[7:0]` | 8 | input only | RX, MISO, target-side SCLK/MOSI/CS, monitor taps, host link (≤ 4 pins total across ui/uo) |
| `uo_out[7:0]` | 8 | push-pull, always driven | TX, controller-side SCLK/MOSI/CS, host link |
| `uio[7:0]` | 8 | bidir; `uio_oe=1` drives | protocols only, never host link: open-drain, tri-state MISO/SWDIO/MDIO, I2C, PS/2 |
| `clk`, `rst_n` | 2 | dedicated | — |

- Open-drain/bidir lines (I2C, PS/2, SWD, MDIO, 3-wire SPI) MUST use `uio`.
- Every input passes a 2-FF synchronizer (+2–3 clk, 40–60 ns); minimum reliable external pulse ≈ 2 clk. Pad outputs registered.
- Demo board (confirmed): RP2350 is wired to every ASIC pin plus `clk`, `rst_n`; host-link pins are lost to protocols.

### Host
RP2350 (MicroPython) generates `clk`, drives `rst_n`, loads programs, exchanges data. Assembler on PC.
- Host link: ≤ 4 pins (prefer ≤ 3), `ui_in`/`uo_out` only (D001). Phase 2a compares a 2-pin UART-style link (TT convention `ui_in[7]`/`uo_out[0]`) vs 3–4-pin SPI.
- Program load ≤ 1 KB, speed unconstrained. Runtime data ≥ 250 KB/s minimum, target ≥ 1 MB/s; MUST sustain UART 1 Mbaud full duplex (≈ 200 KB/s) with no FIFO overrun (D002).
- RP2350 is the default bus partner for all hardware demos/tests; v1 needs no external devices (D008).
- Pmod pull-ups allowed and documented: 4.7 kΩ for I2C 400 kHz; RP2350 internal pull-ups OK for 100 kHz demos only — tr may exceed 1 µs, so not compliance evidence (D005).

## 2. Deliverables (brief, due 2027-01-18)
1. Public repo: RTL, tests, passing GDS + precheck from the TT flow.
2. Architecture documentation.
3. Instruction set documentation.
4. Assembler/toolchain + example programs for every supported protocol.
5. Verification write-up (tools and methodology; open-source runnable tests preferred).
6. Description of AI-tool use.
7. Documentation of any external hardware (Pmod).
8. Optional: video/demo. GL simulation strongly recommended.

## 3. Judging criteria → evidence we must produce

| Criterion (no fixed weights) | Evidence |
|---|---|
| **Flexibility (primary):** new protocols after fab | WS2812 and Manchester TX/RX (no protocol-specific RTL) implemented after RTL freeze with program-only commits (git history shows zero RTL diff); runs in ISS, RTL sim and GL sim. Checklist coverage table: protocol → primitives used → status. |
| Protocols demonstrated | Per-protocol test matrix (modes, rates, widths, errors) passing against independent bus models; decoded waveforms. |
| Unique capabilities | Concurrency, monitor, error injection, cycle-exact timing — each backed by tests. |
| Design + verification | Spec-first flow, golden ISS + RTL lockstep, cocotb bus models, constrained-random, SBY formal, GL sim, coverage, area/timing reports. |
| Write-up quality | Readable docs with diagrams, worked example programs, AI-use disclosure. |

## 4. v1 MUST

Numbers assume 50 MHz; all timing cycle-programmable, not preset tables.

### UART
- TX and RX, LSB-first, idle-high, 8N1 default.
- Baud: any rate 1 200–1 000 000 incl. 1200…115200, 230400, 460800, 921600, 1 M; mean baud error ≤ ±1 % over a frame and every bit within 1 clk of nominal (D040).
- RX tolerates incoming baud error ±2 % (8N1), samples near bit centre: stop-bit sample point drift within ±(BIT/2 − 2 clk) (D040).
- Data bits 5, 6, 7, 8, 9. Parity none/even/odd. Stop bits 1, 1.5, 2.
- Detect and report framing error and parity error per character.
- Continuous back-to-back frames (no forced inter-frame gap) on TX and RX.
- Full duplex: TX and RX simultaneously and independently at up to 1 Mbaud, data streamed to/from host without overrun (D004).

### SPI controller
- Modes 0–3 (CPOL, CPHA), MSB-first and LSB-first, 8-bit words; transfers of N bytes with CS held.
- SCLK 100 kHz – 6.25 MHz (≥ 8 clk per bit), programmable divisor; correct MISO sampling given pad + synchronizer latency.
- Active-low CS; CS setup, hold and inactive time programmable in clk cycles.

### SPI target
- Modes 0–3, external SCLK ≤ 2 MHz, MSB-first, 8-bit words.
- MISO on a `uio` pin, high-Z while CS inactive.
- Timing rule for the controller (D101): CS high ≥ 25 clk (500 ns) between transfers, first sampling SCK edge ≥ 7 clk (140 ns) after CS falls, SCK ≤ 2 MHz (MUST; 4 MHz with the default slot map, about 6 MHz solo). Under it MISO is driven ≤ 4 clk + pad after CS asserts and every byte is correct.
- CS deassert mid-word aborts cleanly and resynchronizes on next CS assert.
- Register-device emulation: response bytes after a 1-byte command depend on that command, starting at the next byte boundary.

### I2C controller (UM10204 Sm/Fm)
- 100 kHz and 400 kHz; meet tLOW ≥ 4.7/1.3 µs, tHIGH ≥ 4.0/0.6 µs, tHD;STA ≥ 4.0/0.6 µs, tSU;STA ≥ 4.7/0.6 µs, tSU;STO ≥ 4.0/0.6 µs, tBUF ≥ 4.7/1.3 µs, tSU;DAT ≥ 250/100 ns, tHD;DAT ≤ 3.45/0.9 µs, with tr ≤ 1000/300 ns from Pmod pull-ups.
- Open-drain SDA and SCL on `uio`.
- START, repeated START, STOP; 7-bit address + R/W; multi-byte write and read.
- Detect ACK/NACK after each written byte; generate ACK, and NACK on last read byte.
- Clock stretching: after releasing SCL wait until SCL reads high (programmable timeout).

### I2C target
- Programmable 7-bit address; ACK own address, ignore others.
- Write (receive N bytes, ACK each) and read (transmit N bytes until controller NACK).
- Detect START, repeated START, STOP at any bit position.
- Stretch SCL while data is not ready; 100 and 400 kHz controllers.

### Not-designed-in demos (post-RTL-freeze, program-only, D006)
- WS2812: T0H 0.4 µs, T1H 0.8 µs, T0L 0.85 µs, T1L 0.45 µs, each ±150 ns; reset low ≥ 280 µs; ≥ 64 LEDs streamed from host.
- Manchester TX and RX (IEEE 802.3 convention, polarity selectable), proposed ≥ 1 Mbit/s; RX recovers timing from edges.
- Zero RTL diff between freeze tag and these programs; run in ISS, RTL and GL sim.

### Common
- Status/data visible to host; program reloadable anytime; every MUST verified in RTL and GL sim.

## 5. v1 SHOULD
- **Concurrency beyond UART full duplex:** two different protocols at once, e.g. I2C target + UART logger.
- **Monitor mode:** passive decode of external UART (≤ 1 Mbaud), SPI (≤ 2 MHz), I2C (400 kHz) without driving any pin; decoded bytes/events to host.
- **Error injection:** UART parity/framing error and break; I2C forced NACK, extra stretch; SPI short/long word, CS glitch; bit flips at chosen positions.
- UART extras: break gen/detect, RTS/CTS, MSB-first, TX/RX inversion, mark/space parity, 9-bit address detect, 300 baud, 3 Mbaud.
- SPI extras: 1–32-bit words, ≥ 2 CS lines, gapless streaming, controller SCLK 12.5 MHz, target SCLK 4 MHz, 3-wire half duplex.
- I2C extras: 10-bit addressing, general call, bus-busy detect, 50 ns spike filter (tSP), Fm+ 1 MHz controller.

## 6. STRETCH (grouped by hardware primitive needed)

Showcase: CAN 2.0A (11-bit ID, bit stuffing, CRC-15, arbitration), RP2350 as the other node; proposed 125–500 kbit/s over open-drain `uio` + pull-up (no transceiver on the bench).

| Primitive | Protocols / features |
|---|---|
| GPIO + cycle timer + shift only | JTAG controller/TAP, SWD host (runtime OE turnaround), MDIO Cl.22/45 (≤ 2.5 MHz), PS/2 host/device (dual open-drain), Microwire, APA102, custom one-wire, PWM/PPM, NEC/SIRC IR TX |
| Cycle-exact output (≤ ±150 ns jitter) | WS2812/SK6812, IR carrier generation |
| Multi-bit parallel shift | Dual/Quad SPI (Octal exceeds pin budget) |
| Edge timestamp capture | IR RX (NEC/RC5/SIRC), autobaud, PWM/PPM capture, logic-analyser monitor |
| CRC/LFSR engine | Modbus RTU CRC-16, SMBus PEC CRC-8, CAN CRC-15, USB CRC-5/16, Ethernet CRC-32 |
| Bit (un)stuffing | CAN, USB LS |
| NRZI / Manchester line coding | USB LS, RC5, SWO Manchester, 10BASE-T |
| Clock recovery / resync | CAN (SJW), USB LS RX, 10BASE-T RX |
| Drive-and-compare arbitration | CAN arbitration, I2C multi-controller arbitration |
| Large buffer / lookup table | EEPROM emulator, SWD DP/AP emulator, MDIO PHY register map, USB descriptors, Ethernet frames |
| UART-framed upper layers | LIN, DMX512, Modbus RTU, ISO 7816, IrDA SIR, BACnet MS/TP |

## 7. Out of scope v1
USB LS, 10BASE-T, CAN FD (CAN 2.0A is the stretch showcase); I2C Hs/UFm; multi-controller arbitration; Octal SPI; MII (pin count); XON/XOFF; autobaud; on-chip analog; application protocols (ITCH, OUCH, FIX, MoldUDP64, SoupBinTCP).

## 8. Operating roles → primitives

| Role | Primitives |
|---|---|
| Controller | Timer-paced output toggles, output/OE control, deterministic timing, input readback (stretch, ACK) |
| Target | Wait-on-edge with bounded latency, fast edge→response, pin-condition abort (CS/STOP), SCL hold |
| Transmitter | Shift-out, bit timer, data supply from host/program |
| Receiver | Edge wait, mid-bit sample timer, shift-in, error flags, data sink to host |
| Monitor | Input-only multi-pin sampling, edge detect, optional timestamps, buffering, guaranteed no-drive |
| Pattern gen / tester | Host-fed stimulus, compare vs expected, bit/timing override, counters |

## 9. Resolved (2026-11-14)
1. Host link ≤ 4 pins (prefer ≤ 3), `ui_in`/`uo_out` only; all `uio` free for protocols — D001.
2. Runtime host data ≥ 250 KB/s (target 1 MB/s), sustains UART 1 Mbaud full duplex; load speed irrelevant — D002.
3. 1 KB SRAM macro planned (Phase 5 proves flow); ≤ 256 B flop fallback must work — D003.
4. UART full duplex is MUST — D004.
5. Pmod pull-ups allowed (4.7 kΩ for 400 kHz); RP2350 internal pull-ups for 100 kHz demos — D005.
6. Post-freeze demos: WS2812, Manchester TX/RX; stretch showcase CAN 2.0A vs RP2350 — D006.
7. Close at 50 MHz; 60 MHz stretch — D007. RP2350 is the default bus partner — D008.
