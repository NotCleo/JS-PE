# Program catalogue

Every shipped assembly program, one row each. New programs add a row at the end of section 2 (and, if they need it, a row in section 3); existing rows do not change. Rates and test configurations are in [CAPABILITY.md](https://github.com/NotCleo/JS-PE/blob/main/docs/CAPABILITY.md); this page is about using the programs.

Programs live in [docs/spec/programs/](https://github.com/NotCleo/JS-PE/tree/main/docs/spec/programs). Each file starts with a header that states its words, clocks per bit, maximum rate, engines, pins, host stream format, events and timing assumptions; the `.deadline` and `.assume` lines inside make the assembler check the timing claims.

## 1. Building an image

```sh
# one program (512 words by default; --mem 128 for the flop fallback)
python sw/jspe_asm.py docs/spec/programs/uart_tx.s -o uart_tx
# two programs in one image, variant selected with -D, clock recorded for the host's check
python sw/jspe_asm.py -D CLK_HZ=48000000 -D BAUD=115200 \
    docs/spec/programs/uart_tx.s docs/spec/programs/uart_rx.s -o uart
# move a program to another engine / load address
python sw/jspe_asm.py -D spi_tgt.s:ENG=3 -D spi_tgt.s:ORG=64 docs/spec/programs/spi_tgt.s -o spi3
```

Outputs: `<name>.bin` (load image for [host/jspe.py](https://github.com/NotCleo/JS-PE/blob/main/host/jspe.py)), `<name>.hex` (simulation), `<name>.lst` (listing with the arming bound per guarded instruction). The default `ORG` of some programs (i2c_tgt 144, ws2812 176) assumes the 512-word layout; on the 128-word build give `-D ORG=…` so the image fits, or the `--mem 128` check fails.

## 2. Programs

| # | File | Protocol, role | Batch | Words | Engines (default) | Pads (default) | `-D` options | Host → chip | Chip → host, events | Host example |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | [uart_tx.s](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/uart_tx.s) | UART TX | 1 | 8–12 | 1 (E0) | TXD `uo_out[0]`, CTS `ui_in[2]` | `BAUD`, `CLK_HZ`, `DBITS` 5–9, `PARITY` 0/1/2, `STOP2`, `STOP15`, `INV`, `MSB`, `ENG`, `ORG` | one byte per character (two for 9 bits) | — | [uart.py](https://github.com/NotCleo/JS-PE/blob/main/host/examples/uart.py) |
| 2 | [uart_rx.s](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/uart_rx.s) | UART RX | 1 | 8–12 | 1 (E1) | RXD `ui_in[7]` (`RXPIN`), RTS `uo_out[1]` | as TX + `RXPIN` | — | bytes; event 1 framing error, 2 parity error (after the character); break = 0x00 + event 1 | [uart.py](https://github.com/NotCleo/JS-PE/blob/main/host/examples/uart.py) |
| 3 | [spi_ctrl.s](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/spi_ctrl.s) | SPI controller | 1 | 17–21 | 1 (E0) | SCK `uio[3]`, CS `uio[0]`, MOSI `uio[1]`, MISO `uio[2]` | `BIT`, `SMP`, `CPHA`, `CPOL`, `LSB`, `WL` 1–16, `CLK_HZ`, `ENG`, `ORG`; CSDLY in R2 by EXEC | `[n]` then n words (1–255) | n words read back | [spi.py](https://github.com/NotCleo/JS-PE/blob/main/host/examples/spi.py) (E0 controller reads E1 target) |
| 4 | [spi_tgt.s](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/spi_tgt.s) | SPI target, register table | 1 | 12 + table | 1 (E0) | same pads, MISO released while CS high | `ENG`, `ORG`; modes 1/2 by the BIND invert bit | — (table in memory) | — | [spi.py](https://github.com/NotCleo/JS-PE/blob/main/host/examples/spi.py) (as E1) |
| 5 | [i2c_ctrl.s](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/i2c_ctrl.s) | I2C controller | 1 | 40–41 | 1 (E0) | SCL `uio[6]` od, SDA `uio[5]` od | `TLOW`, `THIGH`, `THD`, `DEADLINE`, `ENG`, `ORG` | two bytes per step: flags (START, transfer, ninth bit, STOP), data (0xFF to read) | per byte two bytes as sampled on the bus; event 3 = SCL held past the deadline | — |
| 6 | [i2c_tgt.s](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/i2c_tgt.s) | I2C target | 1 | 32 | 1 (E0) | SCL `uio[6]` od, SDA `uio[5]` od | `ADDR`, `THD`, `TSU`, `ENG`, `ORG` | bytes to send on reads (SCL stretched until they arrive) | address byte, then received bytes | [i2c.py](https://github.com/NotCleo/JS-PE/blob/main/host/examples/i2c.py) |
| 7 | [ws2812.s](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/ws2812.s) | WS2812 / SK6812 | 2 | 9 | 1 (E0) | DOUT `uo_out[2]` | `ENG`, `ORG` | 3 bytes per LED (G, R, B) as one burst; pause ≥ 280 µs latches | — | [ws2812.py](https://github.com/NotCleo/JS-PE/blob/main/host/examples/ws2812.py) |
| 8 | [manchester.s](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/manchester.s) | Manchester TX + RX | 2 | TX 9, RX 11–14 | 2 (E0 TX, E1 RX) | TX `uo_out[3]`, RX `ui_in[4]` | `BIT`, `CVCHK`; `inv` pad / bind invert for the other convention | TX bytes, sent MSB first without gaps | RX bytes after the SFD; event 4 = end of frame or code violation | [manchester.py](https://github.com/NotCleo/JS-PE/blob/main/host/examples/manchester.py) |
| 9 | [can.s](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/can.s) ([design note](https://github.com/NotCleo/JS-PE/blob/main/docs/spec/programs/can_design.md)) | CAN 2.0A node | 3 | 128 | 2 (E_RX, E_TX) | bus `uio[4]` od | `BIT`, `ORG_TX`, `ORG_RX` (`-D ORG_TX=0 -D ORG_RX=62` on the flop build) | frames to send | received frames as bytes + event (1 = frame OK, others = errors; see header) | planned |

Words are as stated in each header (range = options). The sum has to fit 512 words for programs that run together (128 on the flop fallback): e.g. CAN + UART TX + RX = 144 words on 4 engines.

## 3. Combinations that have been run together

| Image | Engines | Where tested |
|---|---|---|
| uart_tx + uart_rx | E0, E1 | [test/test_uart.py](https://github.com/NotCleo/JS-PE/blob/main/test/test_uart.py) (R, G) |
| i2c_tgt + uart_tx + uart_rx + spi_tgt (PINS 3.7), then E3 reloaded to ws2812 while E1/E2 stream UART at 1 Mbaud | E0–E3 | `test_c2_reload_while_streaming` in test/test_uart.py (R) |
| spi_ctrl (E0) talking to spi_tgt (E1) on the same pads | E0, E1 | `test_ctrl_tgt_two_engines` in [test/test_spi.py](https://github.com/NotCleo/JS-PE/blob/main/test/test_spi.py) (R) |
| i2c_ctrl (E0) + i2c_tgt (E1) on one bus | E0, E1 | `test_ctrl_tgt_two_engines` in [test/test_i2c.py](https://github.com/NotCleo/JS-PE/blob/main/test/test_i2c.py) (R) |
| manchester TX → RX loop-back | E0, E1 | `test_m4_loopback` in [test/test_manchester.py](https://github.com/NotCleo/JS-PE/blob/main/test/test_manchester.py) (R) |
| can.s alone (both engines) | E_RX, E_TX | [test/test_can.py](https://github.com/NotCleo/JS-PE/blob/main/test/test_can.py) (R, G) |

## 4. Adding a program

1. Write the header in the same form (words, clk/bit, max, engines, pins, host, events, timing).
2. Put `.deadline` after every guarded instruction whose follow-up is timing-critical and `.assume armed` where a wait is guaranteed by the protocol; the assembler fails the build if a path is too slow.
3. Add an ISS test in `sw/tests/` against an independent model, then a cocotb lockstep test in `test/`.
4. Add a row to section 2 here and to [CAPABILITY.md](https://github.com/NotCleo/JS-PE/blob/main/docs/CAPABILITY.md) section 2.
