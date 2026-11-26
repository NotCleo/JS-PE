# PIO host link, LINK B = 4 (design note, BACKLOG rank 7)

Host-only; not run on hardware. D219.

## Clocking (exact ratios)

| sys | `clk` (PWM) | PIO div, 8 cyc/bit | Link |
|---|---|---|---|
| 96 MHz (TT default) | ÷2 = 48 MHz | 1 | 12 Mbaud (default) |
| 100 MHz | ÷2 = 50 MHz | 1 | 12.5 Mbaud |
| 192 MHz (above rating) | ÷4 = 48 MHz | 2 | 12 Mbaud |

B = 16: div 4. Rejected: 144 MHz (`clk` ÷3 has a 33/67 % duty cycle). GPIO20/37/38 need PIO GPIOBASE = 16 (RP2350B).

## TX (GPIO20 out/side-set/set; in_base = H_RDY)

Word = `data << 1 | force`. force = 1 for ESC, op, args and the first ESC of a literal; data bytes wait for H_RDY low (HOSTIF 1, D080). This is per byte, which hardware CTS cannot do.

```
.side_set 1 opt
    pull            side 1 [5]  ; stop / idle
    out x, 1
    jmp x-- send                ; forced
    wait 0 pin 0                ; H_RDY low
send:
    set y, 7        side 0 [7]  ; start
bit: out pins, 1
    jmp y-- bit     [6]
```

80–81 cycles per byte. After H_RDY rises, at most the byte in flight goes out. Break: FIFO empty, `sm.exec("set(pins, 0)")`, ≥ 11 bits, `set(pins, 1)`.

## RX (H_TX = GPIO37 = in_base, jmp_pin)

```
start: wait 0 pin 0
    set x, 7        [8]         ; 2-cycle input sync
bit: in pins, 1
    jmp x-- bit     [6]
    jmp pin ok
    wait 1 pin 0                ; framing error
    jmp start
ok: push
```

The sample falls at 0.5–0.625 bit, tuned in the emulator. Pad delay is ≈ 0.12 bit.

## DMA ring

A DMA channel moves bytes from RXF + 3 to a 16 KB write-ring (32 KB allocated for alignment), DREQ PIO RX, count 2^28 − 1, chained to itself. Python takes the produced total from `count`; produced − consumed > 16 KB means an overrun, then resync (HOSTIF 9). The ring holds 13.6 ms. Load data goes by DMA to TXF; ops by `sm.put`.

## Selection, fallback

`Link(..., pio=True)`; the hardware UART stays the default. Start at B = 16, `WRREG LINK = 2`, wait for H_TX idle, set div 1, `SYNC`. After 3 failed SYNCs: rst_n, reload, hardware UART at B = 16.

## Throughput

Host → chip 1.18 MB/s, chip → host 1.2 MB/s at 48 MHz, ×4 over B = 16. Monitors (monitor_design.md): SPI both lines SCK ≤ 3.3 MHz (from 830 kHz); UART TS=1/2 engine-bound; I2C unchanged. This holds only if MicroPython drains the ring as fast on average (to be measured); otherwise forward the raw ring to the PC.
