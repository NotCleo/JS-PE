# PINS — pads, host pins, demo pin maps, Pmod parts (Phase 2b, 2026-11-15)

Only three pads have a fixed function (the host link). Every other pad is assigned at load time: inputs by the engine's BIND registers, outputs by the host-only pad configuration (ARCH.md 2.1, ISA.md 5). The maps below are the defaults used by the paper programs, tests and demos.

GPIO numbers are the RP2350 pins of the TT demo board in the TT MicroPython firmware: `GPIOMapTTDBv3`, tt-micropython-firmware `src/ttboard/pins/gpio_map_dbv3.py` (branch main, checked 2026-11-18: clk 16, rst_n 14, `ui_in` 17–24, `uio` 25–32, `uo_out` 33–40). UART function names are the RP2350 FUNCSEL values of pico-sdk `src/rp2350/hardware_regs/include/hardware/regs/io_bank0.h` (master, checked 2026-11-18; F2 = UART, F11 = UART TX/RX on CTS/RTS pins). I2C and SPI function names are not checked yet. A PIO-based partner works on any GPIO (D090).

## 1. Pad capabilities

| Pads | Count | Capability | RP2350 GPIO |
|---|---|---|---|
| `ui_in[7:0]` | 8 | input only; any engine may read any input | 17–24 |
| `uo_out[7:0]` | 8 | push-pull, always driven; cannot be released or open-drain | 33–40 |
| `uio[7:0]` | 8 | bidirectional; push-pull, tri-state (`z`), open-drain (`od`) | 25–32 |
| `clk` | 1 | 50 MHz from the RP2350 | 16 |
| `rst_n` | 1 | active low, ≥ 2 clk | 14 |

- All I/O is 3.3 V. No pull-ups and no analog pads on chip.
- After reset: `uio` high-Z, `uo_out` = 0 except H_TX = 1. An idle-high output (UART TX, CS) is therefore low until its pad is enabled; the loader sets the pin value first and enables the pad afterwards, and a UART partner should flush its receiver after load.
- A `uo` pad ignores `od` and `z` (it drives the pin value). Open-drain and tri-state signals must be on `uio`.

## 2. Fixed pins: host link (D031)

| Signal | Pad | RP2350 | Direction | Notes |
|---|---|---|---|---|
| H_RX | `ui_in[3]` | GPIO20, UART1 TX | host → chip | 8N1, idle high |
| H_TX | `uo_out[4]` | GPIO37, UART1 RX | chip → host | 8N1, idle high |
| H_RDY | `uo_out[5]` | GPIO38, UART1 CTS | chip → host | **active low**: 0 = the chip can take a byte. Wire-compatible with a UART CTS input |

- `ui_in[3]`/`uo_out[4]` are the first UART pair on the Tiny Tapeout recommended-pinouts page. REQUIREMENTS.md mentions `ui_in[7]`/`uo_out[0]`; that pair (RP2350 UART0) is used for the UART demo instead.
- `ui_in[3]` stays readable by engines (bind source 3), e.g. to monitor the link.
- Clocking: `clk` and the host UART baud rate must have an exact ratio (bit = 16, 8 or 4 clk). MicroPython `machine.freq(f)` leaves clk_peri, the UART clock, at 48 MHz (micropython `ports/rp2/modmachine.c`), so a hardware UART reaches at most 3 Mbaud. Demo setting (D090): `tt.clock_project_PWM(48_000_000)` (the firmware sets the system clock to 96 MHz, `clk` = sys/2; `demoboard.py`), link 3 Mbaud = B 16, programs assembled with `-D CLK_HZ=48000000` (the `.bin` CLK record is checked by the loader, HOSTIF 7). For `clk` = 50 MHz: `machine.freq(100_000_000, 100_000_000)` moves clk_peri to the system PLL, then link 3.125 Mbaud (divisor 2) or 6.25 Mbaud (divisor 1). 12.5 Mbaud (4 clk per bit) needs a PIO UART.

## 3. Demo pin maps

Logical pins follow one convention in all programs: **p0 = clock-like pin, p1 = second strobe or data** (only p0/p1 accept the B actions `0`, `1`, `t`).

### 3.1 UART (uart_tx.s on E0, uart_rx.s on E1)
| Signal | Pad | Engine pin | RP2350 partner |
|---|---|---|---|
| TXD (chip out) | `uo_out[0]` | E0.p0, push-pull | GPIO33, UART0 RX |
| RXD (chip in) | `ui_in[7]` | E1.p0, bind source 7 | GPIO24 = UART1 TX, the host-link UART: PIO UART TX. Hardware UART0 TX: GPIO18 (F11) = `ui_in[1]`, assemble with `-D RXPIN=1` |
| RTS (chip out, optional) | `uo_out[1]` | E1.p1 | GPIO34, UART0 CTS |
| CTS (chip in, optional) | `ui_in[2]` | E0.p1, bind source 2 | GPIO19, UART0 RTS |

Inverted lines: TX by pad `inv`, RX by the bind invert bit.

### 3.2 SPI controller (spi_ctrl.s on E0) — TT Pmod row uio[3:0]
| Signal | Pad | Engine pin | Pad mode |
|---|---|---|---|
| SCK | `uio[3]` | E0.p0 | push-pull; `inv` for CPOL = 1 |
| CS (active low) | `uio[0]` | E0.p1 | push-pull |
| MOSI | `uio[1]` | E0.p2 | push-pull |
| MISO | `uio[2]` | E0.p3 (shift input), bind source 10 | input |

RP2350 partner: PIO SPI target on GPIO25 (CS), 26 (MOSI), 27 (MISO), 28 (SCK).

### 3.3 SPI target (spi_tgt.s on E0)
| Signal | Pad | Engine pin | Notes |
|---|---|---|---|
| SCK | `uio[3]` | E0.p0, bind source 11 | bind invert = CPOL xor CPHA (modes 1 and 2) |
| CS | `uio[0]` | E0.p1, bind source 8 | |
| MISO | `uio[2]` | E0.p2 | push-pull, released (`z`) while CS is high |
| MOSI | `uio[1]` | E0.p3 (shift input), bind source 9 | |

Timing rule (spi_tgt.s, D101): CS high ≥ 25 clk (500 ns) between transfers, first sampling SCK edge ≥ 7 clk (140 ns) after CS falls, SCK ≤ 2 MHz (MUST; 4 MHz with the default slot map, about 6 MHz solo). Then MISO is driven ≤ 4 clk + pad after CS falls.

RP2350 partner: PIO or `SoftSPI` controller on the same GPIOs. For `machine.SPI(1)` (SCK GPIO26, TX GPIO27, RX GPIO28) rebind to SCK `uio[1]`, MOSI `uio[2]`, MISO `uio[3]`; no program change.

### 3.4 I2C controller and target (i2c_ctrl.s / i2c_tgt.s on E0)
| Signal | Pad | Engine pin | Pad mode | RP2350 partner |
|---|---|---|---|---|
| SCL | `uio[6]` | E0.p0, bind source 14 | `od` | GPIO31, I2C1 SCL |
| SDA | `uio[5]` | E0.p1 (shift input), bind source 13 | `od` | GPIO30, I2C1 SDA |

Open-drain pads never drive high: `uio_oe` is set only while the pin value is 0. A Pmod wired to the TT I2C row (SCL `uio[6]`, SDA `uio[7]`) needs only a different SDA bind/pad line.

### 3.5 WS2812 (ws2812.s on E0)
| Signal | Pad | Engine pin |
|---|---|---|
| DOUT | `uo_out[2]` | E0.p0, push-pull (GPIO35 for capture by the RP2350) |

### 3.6 Manchester (manchester.s: TX on E0, RX on E1)
| Signal | Pad | Engine pin |
|---|---|---|
| TX | `uo_out[3]` | E0.p0, push-pull; `inv` selects the opposite convention (GPIO36) |
| RX | `ui_in[4]` | E1.p0 (shift input), bind source 4 (GPIO21) |

Loop-back: jumper `uo_out[3]` → `ui_in[4]`, or the RP2350 relays through PIO.

### 3.7 Concurrency demo (all four engines)
E0 I2C target on `uio[6:5]`, E1 UART TX on `uo_out[0]`, E2 UART RX on `ui_in[7]`, E3 SPI target on `uio[3:0]`. No pad is shared; the default slot map is used. Image: `jspe_asm.py i2c_tgt.s uart_tx.s uart_rx.s spi_tgt.s -D uart_tx.s:ENG=1 -D uart_rx.s:ENG=2 -D spi_tgt.s:ENG=3`, plus `-D i2c_tgt.s:ORG=32 --mem 128` for the fallback build (64 words).

### 3.8 Monitor mode
The same maps with every pad `en` = 0 and the monitored lines bound as inputs (`ui_in` or `uio`). The chip cannot drive any pad in this configuration.

## 4. Pads free in every default map
`ui_in[0]`, `ui_in[1]`, `ui_in[5]`, `ui_in[6]`, `uo_out[6]`, `uo_out[7]`, `uio[4]`, `uio[7]`.

## 5. External parts (Pmod)

| Use | Parts | Why |
|---|---|---|
| I2C 400 kHz | 2 x 4.7 kΩ to 3.3 V on SDA (`uio[5]`) and SCL (`uio[6]`) | tr ≤ 300 ns needs R·C ≤ 350 ns: bus ≤ 70 pF with 4.7 kΩ; use 2.2 kΩ for longer wires or 1 MHz |
| I2C 100 kHz demo without Pmod | RP2350 internal pull-ups (≈ 50–80 kΩ) | functional only; tr exceeds 1 µs, not compliance evidence (D005) |
| UART, SPI, Manchester | none | push-pull 3.3 V, RP2350 or a jumper is the partner |
| WS2812 strip at 5 V | 74AHCT1G125 (3.3 V → 5 V data), 330 Ω series resistor, 5 V supply with ≥ 470 µF | WS2812B VIH = 0.7·VDD; a few LEDs also run directly from 3.3 V without the buffer |
| CAN bench (stretch) | 1 x 2.2 kΩ pull-up on one `uio` pad | open-drain wired-AND bus between chip and RP2350, no transceiver |
| Level shifting | none | all partners are 3.3 V; pads are not assumed 5 V tolerant |

v1 hardware demos need no external device: the RP2350 is the bus partner on every map above (D008). The Pmod is one 2x6 header on `uio[7:0]` carrying the two pull-ups.

## 6. `info.yaml` pinout names
| Pad | Name | Pad | Name | Pad | Name |
|---|---|---|---|---|---|
| ui[0] | GPI0 | uo[0] | GPO0 / UART TXD | uio[0] | IO0 / SPI CS |
| ui[1] | GPI1 | uo[1] | GPO1 / UART RTS | uio[1] | IO1 / SPI MOSI |
| ui[2] | GPI2 / UART CTS | uo[2] | GPO2 / WS2812 | uio[2] | IO2 / SPI MISO |
| ui[3] | H_RX (host link) | uo[3] | GPO3 / Manchester TX | uio[3] | IO3 / SPI SCK |
| ui[4] | GPI4 / Manchester RX | uo[4] | H_TX (host link) | uio[4] | IO4 |
| ui[5] | GPI5 | uo[5] | H_RDY (host link, active low) | uio[5] | IO5 / I2C SDA |
| ui[6] | GPI6 | uo[6] | GPO6 | uio[6] | IO6 / I2C SCL |
| ui[7] | GPI7 / UART RXD | uo[7] | GPO7 | uio[7] | IO7 |
