# Protocol matrix

Modes, configurations and operating conditions of the protocols a protocol emulator is usually asked for, with Cleo's status for each. Section 10 is the work list for protocols and options not yet built; it is updated as items land.

Every Cleo cell was checked against [CAPABILITY.md](CAPABILITY.md), the program headers in [docs/spec/programs](spec/programs) and the tests (2026-11-27). Rates assume the 50 MHz sign-off clock unless stated; source tags as in CAPABILITY §1 (R = RTL lockstep, I = ISS, B = static bound or header derivation).

| Code | Meaning |
|---|---|
| **DONE** | Implemented and verified in simulation (ISS tests; RTL lockstep for at least one representative configuration of the family) |
| **PART** | Partly implemented (see note) |
| **PROG** | Possible as a program on the current chip, not written yet |
| **HOST** | Belongs in host software (RP2350/PC), not on the chip |
| **RTL** | Needs new hardware (RTL change) |
| **NO** | Not possible on this chip (pads, clock, analog or pin count) |
| **VERIFY** | Feasibility not yet measured |

---

## 1. UART

| Parameter | Options in practice | Cleo |
|---|---|---|
| Baud rate | Any; common 300 … 115 200, 230 400, 460 800, 921 600, 1–3 Mbaud | DONE: tested 300 baud (I) to 1 Mbaud (R); limits TX 4.5 Mbaud, RX 3.3 Mbaud 8N1 (B) |
| Data bits | 5, 6, 7, 8, 9 | DONE 5–9 |
| Parity | None, even, odd, mark (always 1), space (always 0) | DONE none/even/odd · PROG mark/space |
| Stop bits | 1, 1.5, 2 | DONE (TX all three; RX checks the first stop bit) |
| Bit order | LSB first (standard), MSB first (some devices) | DONE both |
| Polarity | Idle-high (TTL), inverted (RS-232 after the level shifter) | DONE TX and RX inverted |
| Sampling | 16× or 8× oversampling, majority of 3 samples, mid-bit sample | DONE timer mid-bit sample (start edge re-timed) · PROG 3-sample majority |
| Baud generation | Integer and fractional divider | DONE (fraction in 1/256 bit steps, prescale 16/256 below 763 baud) |
| Flow control | None, RTS/CTS (hardware), XON/XOFF (0x11/0x13), DTR/DSR | PROG RTS/CTS (pins assigned in PINS, not in uart_tx.s/uart_rx.s) · HOST XON/XOFF · PROG DTR/DSR (same as RTS/CTS) |
| Duplex | Full, half (single wire, open drain), simplex | DONE full and simplex · PROG half-duplex single wire |
| Start-bit detection, glitch reject | Mid-start-bit re-check | DONE |
| Errors detected | Framing, parity, overrun, break, noise | DONE framing/parity/break/overrun (OVR) · PROG noise flag (needs 3-sample) |
| Break | TX: line low ≥ 1 frame (LIN: ≥ 13 bits); RX: detect | DONE detect (0x00 + framing event) · DONE generate in the `FAULTS = 1` build · PROG generate in the normal build |
| Inter-frame gap / RX idle timeout | Programmable delay; timeout after N idle bits | PROG |
| Autobaud | Measure start bit, or sync on 0x55 / 0x7F | DONE (0x55, 300 baud–1 Mbaud, I) |
| 9-bit multiprocessor | Address mark bit, own-address match, wake on address | PART 9-bit frames DONE · PROG address match |
| FIFOs | RX/TX FIFOs, thresholds | DONE (4-entry link FIFOs per engine, host link back-pressure) |
| Physical layers | TTL, RS-232 (external levels), RS-485/422 with driver-enable (DE) pin timing, IrDA SIR (3/16 pulse) | DONE TTL · PROG RS-485 DE control, IrDA SIR |
| Roles | TX, RX, loopback, monitor/sniffer, fault injection | DONE TX, RX, monitor (both directions), fault injection, TX → RX loop-back |
| Built on UART | LIN, Modbus RTU, DMX512, MIDI, ISO 7816 (smartcard), SDI-12 | PROG (section 10) |

## 2. SPI

| Parameter | Options in practice | Cleo |
|---|---|---|
| Roles | Controller, target, monitor | DONE all three |
| Mode (CPOL/CPHA) | 0, 1, 2, 3 | DONE all four, all roles |
| Bit order | MSB first, LSB first | DONE (controller); target MSB first |
| Word size | 4–32 bits typical, arbitrary | DONE 1–16 (controller) · PROG 24/32 |
| Clock rate | kHz to 100+ MHz (device dependent) | DONE controller 6.25 MHz (R); target 4.44 MHz (R), 2 MHz guaranteed; monitor 12.5 MHz (R, simulation), ≤ 8 MHz claimed for the bench |
| Chip select | Active low (standard) or active high; held for a whole transfer or toggled per word | DONE active low, held for n words or per word · PROG active high (pad invert, untested) |
| CS timing | Setup, hold and inactive time programmable | DONE (CSDLY) |
| Multiple targets | Separate CS per target | PROG (one engine driving several CS pins) |
| Daisy chain | Data passes through each device | PROG |
| 3-wire (bidirectional data, half duplex) | Shared SDIO with turnaround | PROG |
| Dual / Quad / Octal SPI (QPI, DTR) | 2/4/8 data lines, often DDR | Dual: PROG at low speed · Quad, Octal: RTL (multi-lane shift; one shifter tap per engine) |
| Command/address/dummy phases | Flash-style transactions, turnaround cycles | PROG (SPI-flash personality) |
| Target response delay | Programmable latency | PART fixed: one register byte, then the table |
| Variants | Microwire, TI SSI (frame-sync pulse) | PROG both |
| Errors / robustness | Shared-bus SCK while CS high, short/long words | DONE shared bus · DONE fault injection (target bit flip, short/long word) |

## 3. I²C

| Parameter | Options in practice | Cleo |
|---|---|---|
| Speed modes | Standard 100 kHz, Fast 400 kHz, Fast-mode Plus 1 MHz, High-speed 3.4 MHz, Ultra-fast 5 MHz (push-pull, write-only) | DONE 100/400 kHz · PART Fm+: controller 0.83 MHz (R), target 1 MHz marginal (B) · NO Hs (current-source pull-up, reaction time) · NO UFm |
| Roles | Controller, target, multi-controller, monitor | DONE controller/target/monitor · PROG multi-controller |
| Addressing | 7-bit, 10-bit | DONE 7-bit · PROG 10-bit |
| Reserved addresses | General call (0x00), START byte, CBUS, device ID, Hs-mode code | PROG general call, START byte, device ID |
| Conditions | START, repeated START, STOP | DONE |
| ACK/NACK | Generate and check | DONE |
| Clock stretching | Target holds SCL low | DONE (target stretches; controller waits, timeout event 3) |
| Arbitration and clock synchronisation | Multi-controller, wired-AND | PROG |
| Bus recovery | 9 SCL pulses + STOP when SDA stuck low | PART host step sequence documented in i2c_ctrl.s, not tested · PROG on chip |
| Bus busy / stuck-low detection, timeout | SMBus: 25–35 ms clock-low timeout | DONE controller stretch timeout · PROG bus-busy, stuck-low detect |
| Spike filter | 50 ns suppression (Fm, Fm+) | PART: hardware 3-sample filter per input (FILT, removes < 40 ns) exists; not exercised by the I2C tests |
| Timing compliance | tSU/tHD/tLOW/tHIGH per UM10204, rise-time limits | DONE checked by a UM10204 timing checker (Sm, Fm, Fm+ controller) |
| Higher layers | SMBus (PEC CRC-8, alert, ARP), PMBus, SPD, MCTP | PROG SMBus PEC (LFSR CRC-8); rest HOST |
| Device personalities | EEPROM, sensor register map, RTC | PROG (target + host model) |

## 4. USB Low Speed

| Parameter | Options in practice | Cleo |
|---|---|---|
| Bit rate | 1.5 Mbit/s ± 1.5 % | DONE at 48 MHz (32 clk/bit) |
| Roles | Device, host, hub, sniffer | DONE device · PROG sniffer · PROG host (hard) |
| Line coding | NRZI, bit stuffing after 6 ones, SYNC, EOP (2 bits SE0 + J) | DONE on chip (no stuff-error check: CRC/key compare catches it) |
| CRC | CRC-5 (tokens), CRC-16 (data) | DONE CRC-16 on chip · PART CRC-5: tokens matched on chip against CRC-16 keys the RP2350 precomputes per (address, endpoint) |
| Clock recovery | Resync on every transition | DONE (software, per NRZI edge) |
| Packets | Tokens SETUP/IN/OUT, DATA0/DATA1, ACK/NAK/STALL, PRE (via hub) | DONE all except PRE |
| Transfers | Control (EP0) and interrupt only; max packet 8 bytes | DONE both |
| Turnaround | Device reply within 6.5 bit times | DONE 2.7–3.2 bit times (I) |
| Enumeration | Reset, GET_DESCRIPTOR, SET_ADDRESS, SET_CONFIGURATION, class requests | DONE in simulation, descriptors served by the RP2350 |
| Bus reset | SE0 ≥ 10 ms | PART detected by the RP2350 reading the pins |
| Keep-alive | LS host sends a short EOP every 1 ms | DONE ignored correctly |
| Suspend / resume / remote wake-up | 3 ms idle → suspend; K-state resume | PROG |
| Electrical | 1.5 kΩ pull-up on D−, 75–300 ns edges, SE0 at crossover up to 210 ns | DONE via Pmod; crossover SE0 210 ns (512-word build), 167 ns (128-word build) |

## 5. 10BASE-T Ethernet

| Parameter | Options in practice | Cleo |
|---|---|---|
| Line rate | 10 Mbit/s Manchester (50 ns half-bits) | RTL: Manchester in software reaches TX 2.27 / RX 2.78 Mbit/s; 10 Mbit/s needs BIT 4–6 clk and a gap-free shifter path |
| Link integrity | Normal link pulses: 100 ns every 16 ± 8 ms | PROG |
| Auto-negotiation | Fast link pulse bursts advertising half/full duplex | PROG |
| Frame format | Preamble, SFD, MACs, EtherType, 46–1500 B payload, CRC-32 FCS | RTL for line rate |
| CRC-32 | Generator/checker | PROG on the 32-bit LFSR (program rates only) |
| End of frame, inter-frame gap | TP_IDL, 96 bit times | RTL with frame TX |
| Duplex | Half (CSMA/CD) or full | RTL |
| Receiver | Squelch, polarity, clock recovery, ±100 ppm | RTL |
| PHY-chip routes | MII (4-bit, 2.5 MHz) or RMII (2-bit at 50 MHz ref) | VERIFY (feasibility probe in section 10) |
| Management | MDIO Clause 22/45 | PROG |
| Electrical | Differential through a transformer | Pmod (external driver and comparator) |

## 6. JTAG (IEEE 1149.1)

| Parameter | Options in practice | Cleo |
|---|---|---|
| Signals | TCK, TMS, TDI, TDO, optional TRST | DONE (controller: TRST as an RP2350 GPIO; emulator: TRST input) |
| Roles | Controller (probe), target (TAP emulator), monitor | DONE controller (jtag_ctrl.s), TAP emulator (jtag_tap.s) · PROG monitor |
| TAP state machine | 16 states; reset by TRST or 5 × TMS = 1 | DONE both roles (host/debug.py walks the TAP; emulator: one guarded jump per state, TRST and 5 × TMS = 1) |
| Instructions | BYPASS, EXTEST, SAMPLE/PRELOAD, IDCODE, USERCODE, private | DONE emulator: IDCODE (32 bits), one user DR (1–16 bits, pushed to the host on Update-DR), BYPASS for every other opcode · PROG boundary-scan instructions (as further user DRs) |
| IR/DR lengths, chains | Any length; multiple TAPs, BYPASS the others | DONE: controller any length (host splits scans); emulator IR 2–16 bits, exact shift registers; chains tested (2 model TAPs behind the controller; 2 emulator engines in series) |
| TCK rate | kHz to tens of MHz | DONE controller ≤ 6.25 MHz (BIT 8, TDO window SMP − 3 clk), 4.17 MHz symmetric (I); emulator ≤ 1.39 MHz (2.78 MHz solo slot map) (I); controller → emulator lockstep at 1.25 MHz (R) |
| Variants | RTCK, cJTAG (IEEE 1149.7), JTAG-to-SWD switch | DONE JTAG-to-SWD sequence (swd_host raw bits) · PROG RTCK, cJTAG (10.1) |

## 7. SWD (ADIv5/ADIv6)

| Parameter | Options in practice | Cleo |
|---|---|---|
| Signals | SWCLK, SWDIO (bidirectional), optional SWO, nRESET | DONE (SWDIO on a uio pin with a pull-up; nRESET as an RP2350 GPIO) |
| Roles | Host, target (DP emulator), monitor, SWO receiver | DONE host (swd_host.s), DP emulator (swd_dp.s), SWO NRZ receiver (uart_rx.s) · PROG monitor |
| Packet | Request, turnaround, ACK (OK/WAIT/FAULT), 32-bit data + parity | DONE both roles |
| Line reset / switching | ≥ 50 ones + idle; JTAG-to-SWD 0xE79E; dormant wake-up | DONE line reset and JTAG-to-SWD (both roles) · PROG dormant wake-up (host raw bits; emulator does not model dormant state) |
| Versions | v1; v2 multidrop (TARGETSEL) | DONE v1 · DONE v2 TARGETSEL (host: raw sequence; emulator `MDROP=1`, two DPs on one bus) |
| DP registers | DPIDR, CTRL/STAT, SELECT, RDBUFF; posted AP reads | DONE emulator: DPIDR, CTRL/STAT (power-up ACK = REQ, WDATAERR, STICKYERR), ABORT, SELECT and AP registers on the host, RDBUFF, posted AP reads · PART RESEND answered as RDBUFF, DPBANKSEL ignored |
| Error handling | WAIT/FAULT, sticky errors, parity error | DONE host (WAIT retry, FAULT, no reply, read parity check); emulator (WAIT while the host serves an AP access, FAULT on sticky flags, write parity → WDATAERR, protocol error → lockout until line reset) |
| SWO trace | UART/NRZ or Manchester | DONE NRZ (uart_rx.s unchanged, ITM decoder on the host, ≤ 3.125 Mbaud tested) · PROG Manchester (needs a start-bit hunt, LSB-first variant of manchester.s) |
| Rate | Probe-defined, typically 1–50 MHz | host ≤ 8.3 MHz (BIT 6; target answer window BIT − 4 clk) (I, R at 781 kHz); emulator ≤ 781 kHz back to back, 1.04 MHz with ≥ 2 idle cycles (I) |

## 8. PS/2

| Parameter | Options in practice | Cleo |
|---|---|---|
| Signals | CLK, DATA, open collector | PROG (uio, open drain) |
| Clock | Device generates 10–16.7 kHz | PROG |
| Frame | Start 0, 8 data LSB first, odd parity, stop 1 | PROG |
| Host → device | Inhibit (CLK low ≥ 100 µs), request-to-send, device clocks, ACK bit | PROG |
| Roles | Keyboard/mouse device, host, sniffer | PROG all |
| Keyboard, mouse command models | Scan codes, commands, mouse packets and IDs | HOST (chip does framing) |
| Errors | Parity error, timeout, resend | PROG |

## 9. CAN

| Parameter | Options in practice | Cleo |
|---|---|---|
| Versions | 2.0A, 2.0B (29-bit), CAN FD, CAN XL | DONE 2.0A · PROG 2.0B (29-bit frames are error-flagged today) · PROG FD without BRS (fit unknown) · RTL FD with BRS · NO XL |
| Bit rate | 10 kbit/s – 1 Mbit/s | DONE 500 kbit/s (R), 125 kbit/s (I), 625 kbit/s limit (B) · NO 1 Mbit/s with this program |
| Bit timing | Sample point, SJW, triple sampling | DONE configurable sample point · PART hard resync only (SJW unlimited) · PROG proper SJW, triple sampling |
| Frames | Data, remote, error, overload, interframe space | DONE data/remote/error flags · PROG overload frames |
| Arbitration, stuffing, CRC-15, ACK | | DONE on chip |
| EOF / delimiters | 7 recessive bits, ACK and CRC delimiters | PROG: ACK delimiter and EOF not checked (CRC delimiter checked as form) |
| Error confinement | Bit/stuff/CRC/form/ACK errors; TEC/REC; passive; bus-off | DONE error detection · HOST counters and states |
| Operating modes | Normal, listen-only, loopback, one-shot | DONE normal, self-reception · PROG listen-only · HOST one-shot |
| Acceptance filters | ID masks | HOST |
| Concurrency | With other protocols | DONE CAN + UART TX + RX, timing identical to alone (I, R) |
| Electrical | Transceiver or open-drain bench bus | Pmod or open-drain bus |

---

## 10. Work list (protocols and options not yet built)

Program/host only: no change to the RTL, the ISA or the ISS. Status codes as above; a reclassified item carries its measured reason. Families are worked in this order.

| # | Family | Item | Status |
|---|---|---|---|
| 1.1 | Debug | JTAG controller (TAP walk, IR/DR shift streamed from the host) | DONE: jtag_ctrl.s 23 words, ≤ 6.25 MHz (D230) |
| 1.2 | Debug | JTAG TAP emulator (IDCODE, BYPASS, IR/DR lengths, chains, TLR by TMS / TRST) | DONE: jtag_tap.s 84 words, ≤ 1.39 MHz (D231) |
| 1.3 | Debug | SWD host (request, ACK, WAIT/FAULT, parity, line reset, JTAG-to-SWD) | DONE: swd_host.s 68 words, ≤ 8.3 MHz (D232) |
| 1.4 | Debug | SWD DP emulator (DPIDR, CTRL/STAT, SELECT, RDBUFF, WAIT/FAULT, parity error) | DONE: swd_dp.s 188 + 5 box words, ≤ 781 kHz back to back (D233) |
| 1.5 | Debug | SWD v2 multidrop (TARGETSEL) | DONE: swd_dp.s `MDROP=1` (+16 words), host raw sequence (D233) |
| 1.6 | Debug | SWO receive (UART/NRZ and Manchester, reusing uart_rx / manchester) | PART: NRZ DONE (uart_rx unchanged, ITM tests); Manchester PROG — measured: manchester.s needs a 0x55 preamble + SFD and is MSB first, SWO Manchester has one start bit and is LSB first (D234) |
| 2.1 | PS/2 | Device: keyboard and mouse framing, clock generation, request-to-send, ACK | PROG |
| 2.2 | PS/2 | Host: inhibit, request-to-send, receive, parity/timeout errors | PROG |
| 3.1 | Ethernet | 10BASE-T normal link pulses | PROG |
| 3.2 | Ethernet | FLP auto-negotiation burst | PROG |
| 3.3 | Ethernet | CRC-32 on the LFSR at program rate | PROG |
| 3.4 | Ethernet | MDIO Clause 22/45 controller | PROG |
| 3.5 | Ethernet | MDIO PHY emulator | PROG |
| 3.6 | Ethernet | RMII-10 / MII TX feasibility probe | VERIFY |
| 4.1 | USB | CRC-5 computed on chip (512-word build) | PART |
| 4.2 | USB | Bus reset (SE0 ≥ 10 ms) detected on chip | PART |
| 4.3 | USB | Suspend / resume + remote wake-up | PROG |
| 4.4 | USB | LS sniffer | PROG |
| 4.5 | USB | LS host | PROG |
| 5.1 | CAN | 2.0B 29-bit frames | PROG |
| 5.2 | CAN | Proper SJW resync | PROG |
| 5.3 | CAN | EOF / ACK delimiter checks | PROG |
| 5.4 | CAN | Overload frames | PROG |
| 5.5 | CAN | Listen-only | PROG |
| 5.6 | CAN | Triple sampling | PROG |
| 5.7 | CAN | CAN FD without BRS | PROG |
| 6.1 | UART | RTS/CTS | PROG |
| 6.2 | UART | 9-bit multidrop address match | PROG |
| 6.3 | UART | Half-duplex single wire | PROG |
| 6.4 | UART | Mark/space parity | PROG |
| 6.5 | UART | RX idle timeout | PROG |
| 6.6 | UART | Break generation in the normal build | PROG |
| 6.7 | UART | 3-sample noise flag | PROG |
| 6.8 | UART | RS-485 DE timing | PROG |
| 6.9 | UART | IrDA SIR | PROG |
| 6.10 | UART | LIN | PROG |
| 6.11 | UART | Modbus RTU | PROG |
| 6.12 | UART | DMX512 | PROG |
| 6.13 | UART | MIDI | PROG |
| 6.14 | UART | SDI-12 | PROG |
| 6.15 | UART | ISO 7816 T=0 | PROG |
| 7.1 | I2C | 10-bit addressing | PROG |
| 7.2 | I2C | General call | PROG |
| 7.3 | I2C | START byte | PROG |
| 7.4 | I2C | Multi-controller arbitration + clock sync | PROG |
| 7.5 | I2C | Bus recovery | PART |
| 7.6 | I2C | Bus-busy / stuck-low detect | PROG |
| 7.7 | I2C | SMBus PEC | PROG |
| 7.8 | I2C | EEPROM personality | PROG |
| 7.9 | I2C | Register-map sensor personality | PROG |
| 8.1 | SPI | 3-wire | PROG |
| 8.2 | SPI | Microwire | PROG |
| 8.3 | SPI | TI SSI | PROG |
| 8.4 | SPI | Multi-CS | PROG |
| 8.5 | SPI | Daisy chain | PROG |
| 8.6 | SPI | 24/32-bit words | PROG |
| 8.7 | SPI | Active-high CS | PROG |
| 8.8 | SPI | Dual SPI at low speed | PROG |
| 8.9 | SPI | SPI-flash personality | PROG |
| 9.1 | Extras | 1-Wire (ROM search, CRC-8) | PROG |
| 9.2 | Extras | DHT22 | PROG |
| 9.3 | Extras | DShot150/300/600 | PROG |
| 9.4 | Extras | SK6812 | PART: ws2812.s streams 4 bytes per LED, but T1H 840 ns exceeds SK6812's 0.6 ± 0.15 µs |
| 9.5 | Extras | APA102 | PROG |
| 9.6 | Extras | IR TX with carrier | PROG |
| 9.7 | Extras | RC5 / RC6 / Sony RX | PROG |
| 9.8 | Extras | PWM / servo / PPM / quadrature / tachometer | PROG |
| 9.9 | Extras | HC-SR04 echo emulation | PROG |
| 9.10 | Extras | DALI | PROG |
| 9.11 | Extras | HDMI-CEC | PROG |
| 9.12 | Extras | SENT | PROG |
| 9.13 | Extras | Wiegand | PROG |
| 9.14 | Extras | I2S at low rate (one engine) | VERIFY |
| 10.1 | Debug | cJTAG (IEEE 1149.7), optional | PROG |
| — | High speed | 10BASE-T frames, CAN FD BRS, I²C Hs/UFm, USB full speed, Quad SPI / SD 4-bit / HUB75 / 8080 LCD | RTL or NO (out of scope) |

## 11. Corrections made while checking (2026-11-27)

| Cell | Was | Now, with evidence |
|---|---|---|
| UART RTS/CTS | claimed in CAPABILITY rows 1–2, PROGRAMS, INTERFACING | PROG: neither uart_tx.s nor uart_rx.s reads CTS or drives RTS; no test |
| SK6812 | listed with WS2812 (CAPABILITY row 7) | PART: T1H 840 ns is outside SK6812's 0.45–0.75 µs |
| SPI active-high CS | unverified | PROG: pad invert would do it; no test |
| I2C spike filter | DONE | PART: FILT exists; the I2C tests run with FILT = 0 |
| I2C bus recovery | PROG | PART: host step sequence in the i2c_ctrl.s header; untested |
| CAN FD no BRS | PART | PROG: nothing of FD is implemented |
| CAN EOF | PART | PROG: ACK delimiter and EOF both unchecked (can.s limit 4) |
| SWO | PROG | PART: the existing receivers decode it; no test (2026-11-27: NRZ tested, Manchester does not fit manchester.s framing, D234) |
| 9-bit multidrop | PROG | PART: 9-bit frames done, address match missing |
| SPI target response delay | DONE | PART: fixed one-byte latency |
