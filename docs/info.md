## How it works

Cleo is a reprogrammable protocol emulator. Four identical engines (E0–E3) run 16-bit `guard : operation` instructions from one shared 512x16 program memory (an IHP SRAM macro). An engine holds its armed instruction until the guard's event (pin edge, timer, shifter, FIFO state) occurs and acts in that same clock, so a pad edge becomes a pad change in 3–4 clk + pad with no fetch in between. Each engine has a timer, a shifter with taps, an ALU and four logical pins bound to any input; shared blocks are a CRC/LFSR unit, eight signal flags, per-engine RX/TX byte FIFOs and a host link. Output pads are owned by one (engine, pin) each and only the host can enable them.

The host (the RP2350 on the demo board) loads programs and streams data over a 3-pin UART-style link: H_RX `ui[3]`, H_TX `uo[4]`, H_RDY `uo[5]` (0 = ready), 8N1 at 16 clk/bit after reset (8 or 4 selectable). Programs are written in assembly and assembled on a PC (`sw/jspe_asm.py`), which also checks each program's timing claims. Shipped programs: UART TX/RX, SPI controller/target, I2C controller/target, and ones not designed in: WS2812, Manchester, CAN 2.0A. The chip loses its program when deselected; reload it from the host.

Sign-off (GDS run 45553919240): 37.9 % of the core placed including the SRAM macro, setup slack 2.68 ns at 50 MHz in the slow corner, DRC/LVS/antenna/precheck pass, 45/45 system tests at gate level. All protocol rates so far are from simulation. Full documentation: the repository README.

## How to test

1. Clock the design from the RP2350 and pulse `rst_n`. The host examples use 48 MHz (`tt.clock_project_PWM(48_000_000)`) so the 3 Mbaud host UART is exactly 16 clk per bit; images carry their clock and the loader refuses a mismatch.
2. With `host/jspe.py`: `sync()` checks the ID (0x4A10), `load("x.bin")` writes and reads back the image, configures pads with enable = 0, runs the initialisation instructions, enables the pads and starts the engines.
3. Run a demo from `host/examples/` (`uart.py`, `spi.py`, `i2c.py`, `ws2812.py`, `manchester.py`). The RP2350 is the bus partner on the pins listed in the pinout, so no external device is needed. These demos have not yet run on a board.

## External hardware

None for UART, SPI, Manchester or the I2C 100 kHz demo (RP2350 internal pull-ups, functional only). I2C 400 kHz: Pmod on `uio` with 2 x 4.7 kΩ pull-ups to 3.3 V on SDA `uio[5]` and SCL `uio[6]`. WS2812 strip at 5 V: 74AHCT1G125 level shifter, 330 Ω series resistor, 5 V supply with ≥ 470 µF. CAN bench: one pull-up on `uio[4]` (open-drain bus with the RP2350, no transceiver). All I/O is 3.3 V.
