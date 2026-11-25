# Capability

What the chip does today, one row per protocol (role). New protocol batches add rows at the end of the table in section 2 and a line in section 4; nothing above them changes.

## 1. How to read the rates

All figures are from simulation: there are no board or silicon results yet. Each rate carries its source:

| Tag | Source |
|---|---|
| R | measured on the RTL in a cocotb lockstep test (the ISS agrees clock for clock) |
| G | the same test passed on the post-layout netlist: shipped SRAM build GDS run 45553919240 (CI `gl_test`, 45/45) and the flop fallback run 44625407065 (`make gl-all`); most tests shorten their configuration list at gate level (e.g. UART rates: 1 Mbaud only) |
| I | measured on the ISS in a pytest test |
| B | limit from the assembler's static arming bound (checked against the ISS by T5/T6, D100) or from the program header's timing derivation, not run at that rate |

"Freeze" says whether the program existed at tag `rtl-freeze-v1` (commit `42457db`, 2026-11-18). After the tag the RTL took one bug fix (D109, engine reset FP = 1) and no feature; every protocol was brought up on the frozen RTL.

## 2. Protocols

| # | Protocol, role | Batch | Modes and options | Tested configurations | Max rate (source) | Words | Engines | Program | Tests | Freeze |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | UART transmitter | 1 designed in | 5–9 data bits, none/even/odd parity, 1/1.5/2 stop, inverted line, MSB first, low baud via prescaler, CTS | 1 Mbaud, 921 600, 115 200 (R); 1 Mbaud (G); 5–9 data bits x parity x stop, pairwise at 1 M and 115 200 (full cross with `FULL=1`) (R); 300 baud (I) | 4.5 Mbaud, BIT ≥ 11 clk (B) | 8 (8N1), max 12 | 1 | [uart_tx.s](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/uart_tx.s) | [sw](https://github.com/NotCleo/JS-PE/blob/main/sw/tests/test_uart.py), [rtl](https://github.com/NotCleo/JS-PE/blob/main/test/test_uart.py) | before |
| 2 | UART receiver | 1 designed in | as TX; framing error, parity error, break as events; RTS | senders at −2 %, 0, +2 % with narrowed stop bits at 1 M, 921 600, 115 200 (R); 3 Mbaud stop-sample drift 4.55 clk of 6.33 allowed (I) | 3.3 Mbaud 8N1; 2.6 with parity or 9 bits; 2.1 with both (B, D100) | 8 (8N1), 12 with parity | 1 | [uart_rx.s](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/uart_rx.s) | same | before |
| 3 | SPI controller | 1 designed in | modes 0–3, MSB/LSB first, 1–16-bit words, 1–255 words per CS, programmable CS setup/hold | SCK 6.25 / 5 / 1 MHz (BIT 8/10/50) x modes x bit order x word length (R, G); CS delays 1–1000 clk (R); MISO answer window 0–49 ns correct (R) | 6.25 MHz read/write (R); write-only 8.3 MHz, 12.5 MHz in solo slot map (B) | 18 (CPHA 0), 17 (CPHA 1), +3 for WL > 8 | 1 | [spi_ctrl.s](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/spi_ctrl.s) | [sw](https://github.com/NotCleo/JS-PE/blob/main/sw/tests/test_spi.py), [rtl](https://github.com/NotCleo/JS-PE/blob/main/test/test_spi.py) | before |
| 4 | SPI target (register device) | 1 designed in | modes 0–3; byte 1 = register, then table[reg], table[reg+1], … ; CS abort at any bit | modes 0–3 at 4.44, 4 and 2 MHz, random partner phase; mode 0 at 1 and 0.1 MHz; CS aborts after 1–7 bits; shared-bus traffic (R, G) | 4.44 MHz, T = 11.25 clk (R; the header states 4 MHz); 2 MHz is the MUST; ≈ 6 MHz solo (B). Rule: CS high ≥ 25 clk, first edge ≥ 7 clk after CS falls (D101) | 12 + table | 1 | [spi_tgt.s](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/spi_tgt.s) | same | before |
| 5 | I2C controller | 1 designed in | START, repeated START, STOP, write/read, ACK/NACK, clock stretching with timeout (event 3) | 400 kHz with 300 ns rise, UM10204 Fm table; 100 kHz with 1 µs rise, Sm table; stretches 2 µs–500 µs, 1.2 ms timeout (R, G) | 0.83 MHz, Fm+ table met (R and I agree, D096) | 41 (40 with THD 0) | 1 | [i2c_ctrl.s](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/i2c_ctrl.s) | [sw](https://github.com/NotCleo/JS-PE/blob/main/sw/tests/test_i2c.py), [rtl](https://github.com/NotCleo/JS-PE/blob/main/test/test_i2c.py) | before |
| 6 | I2C target | 1 designed in | 7-bit address (changeable by EXEC), receive with ACK, transmit until NACK, clock stretching while the host is late | 400 kHz and 100 kHz writes/reads of 1–16 bytes; START/STOP at every bit position; host late by 10 µs–1 ms; controller + target engines on one bus (R, G) | 400 kHz (R); 1 MHz marginal (B) | 32 | 1 | [i2c_tgt.s](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/i2c_tgt.s) | same | before |
| 7 | WS2812 / SK6812 LED stream | 2 not designed in | any number of LEDs, 3 bytes per LED, latch by pause | 256 LEDs streamed at link B = 16: T0H 420, T1H 840, T0L 840, T1L 420 ns on every bit (±150 ns allowed); host pauses of 10/100 µs (R, G) | 794 kbit/s, fixed by the LED (R) | 9 | 1 | [ws2812.s](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/ws2812.s) | [sw](https://github.com/NotCleo/JS-PE/blob/main/sw/tests/test_notdesigned.py), [rtl](https://github.com/NotCleo/JS-PE/blob/main/test/test_ws2812.py) | before |
| 8 | Manchester TX + RX (IEEE 802.3 convention or inverted) | 2 not designed in | TX gapless while data waits; RX clock recovery by windowed timer sync, end-of-frame event 4, code-violation check (CVCHK) | TX BIT 50 and 22; RX BIT 50, 22, 18 with ±2 % and cycle-to-cycle jitter up to BIT/4 − 2; loop-back both polarities at 1 Mbit/s and BIT 22 (R, G) | TX 2.27 Mbit/s at BIT 22, the bound's limit (R; header rounds to 2.2); RX 2.78 Mbit/s at BIT 18, the D100 limit (R; header: 2.7) | TX 9, RX 14 (11 with CVCHK 0) | 2 | [manchester.s](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/manchester.s) | same, [rtl](https://github.com/NotCleo/JS-PE/blob/main/test/test_manchester.py) | before |
| 9 | CAN 2.0A node | 3 not designed in | 11-bit ID, data and remote frames, arbitration, bit stuffing, CRC-15 on the shared LFSR, ACK, error flags; fault confinement left to the host | 500 kbit/s chip TX, chip RX, arbitration, errors vs an independent node model (R, G); 500 and 125 kbit/s at ±1 % between nodes, glitches, SOF race (I) | 500 kbit/s (R); limit 625 kbit/s (B) | 128 (RX 66 + TX 62) | 2 | [can.s](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/can.s), [design](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/can_design.md) | [sw](https://github.com/NotCleo/JS-PE/blob/main/sw/tests/test_can.py), [rtl](https://github.com/NotCleo/JS-PE/blob/main/test/test_can.py) | **after** |

Batch 1 is the v1 scope (REQUIREMENTS). Batch 2 programs were chosen in Phase 1 as the not-designed-in demos (D006: no dedicated hardware) and drafted as paper programs in Phase 2b while the ISA was being specified, so they could have influenced it; they were brought up after the RTL freeze with no RTL change. Batch 3 was written after the RTL freeze and needed no RTL change.

## 3. Limits per protocol, stated plainly

| # | Limit | Source |
|---|---|---|
| 2 | a 1-clk stop→start gap needs BIT ≥ 15 / 19 / 23 clk (3.3 / 2.6 / 2.1 Mbaud) | uart_rx.s header, D100 |
| 1, 2 | above 3.1 Mbaud the host link must run at 8 clk per bit to keep characters back to back | uart_tx.s header |
| 4 | outside the D101 timing rule (shared bus), the first sampling edge must come ≥ 32 clk after the CS rise | spi_tgt.s header, D093 |
| 5 | no arbitration check (single controller); SDA stuck low needs a host recovery step | i2c_ctrl.s header, D041 |
| 6 | 1 MHz needs THD 5 and has a 13-clk high phase against a 14-clk bound: marginal | i2c_tgt.s header |
| 8 | CVCHK = 0 has no code-violation check | manchester.s header, D099 |
| 9 | 128 words: on the shipped 512-word SRAM build it fits next to UART TX + RX; on the flop fallback it fills the memory and runs alone; ACK delimiter and EOF unchecked; 29-bit frames error-flagged; resync is a hard re-time on every recessive→dominant edge (an 8-clk glitch 28–72 clk into a recessive bit drops that bit → CRC error, retry); TEC/REC/bus-off on the host; host must read ≥ 1 byte per 8 CAN bits | can.s header, D102–D105 |
| all | programs that fit together: 512 words shared by 4 engines, e.g. CAN + UART TX + RX = 144 words, 4 engines, pads disjoint (`jspe_asm.py -D uart_tx.s:ENG=2 -D uart_rx.s:ENG=3 can.s uart_tx.s uart_rx.s` assembles with both deadlines ok; not yet run together in simulation); engines (4), not words, are now the usual limit. Flop fallback: 128 words, UART TX + RX fit next to anything except CAN | ARCH 10, D131 (D062 for the fallback) |

## 4. Change log of this table

| Date | Rows | Note |
|---|---|---|
| 2026-11-20 | 1–9 | first version, from program headers, PROGRESS and the test files |
| 2026-11-25 | G tag, 9, all | SRAM build shipped (D131): 512 words, G now also from run 45553919240 |
