# PIO host link, LINK B = 4 (design note, BACKLOG rank 7)

Host-only; not run on hardware. D219.

## Clocking (exact ratios)

| sys | `clk` (PWM) | PIO div, 8 cyc/bit | Link |
|---|---|---|---|
| 96 MHz (TT default) | ÷2 = 48 MHz | 1 | 12 Mbaud (default) |
| 100 MHz | ÷2 = 50 MHz | 1 | 12.5 Mbaud |
| 192 MHz (above rating) | ÷4 = 48 MHz | 2 | 12 Mbaud |

B = 16: div 4. Rejected: 144 MHz (`clk` ÷3 has a 33/67 % duty cycle).

## TX (GPIO20 = out/set pin; in_base = H_RDY)

Word = `data << 1 | force`. force = 1 for ESC, op, args and the first ESC of a literal; data bytes wait for H_RDY low (HOSTIF 1, D080). This is per byte, which hardware CTS cannot do. No side-set (optional side-set is avoided).

```
    pull()                      ; line high (stop / idle)
    out(x, 1)
    jmp(x_dec, "send")          ; forced
    wait(0, pin, 0)             ; H_RDY low
    label("send")
    set(pins, 0)        [6]     ; start
    set(y, 7)
    label("bit")
    out(pins, 1)
    jmp(y_dec, "bit")   [6]
    set(pins, 1)        [4]     ; stop: 8 cycles to the next start (9 unforced)
```

80–81 cycles per byte. After H_RDY rises, at most the byte in flight goes out. Break: FIFO empty, `sm.exec("set(pins, 0)")`, ≥ 11 bits, `set(pins, 1)`.

## RX (H_TX = GPIO37 = in_base, jmp_pin)

```
start: wait 0 pin 0
    set x, 7        [10]        ; to mid bit 0
bit: in pins, 1
    jmp x-- bit     [6]
    jmp pin ok
    wait 1 pin 0                ; framing error
    jmp start
ok: push
```

The sample falls at 0.5 bit + 0–1 cycle: the 2-cycle input sync delays detection and sampling alike. Pad delay ≈ 0.12 bit.

## DMA ring

A DMA channel moves bytes from RXF + 3 to a 16 KB write-ring (32 KB allocated for alignment), DREQ PIO RX, count 2^28 − 1, chained to itself. Python takes the produced total from `count`; produced − consumed > 16 KB means an overrun, then resync (HOSTIF 9). The ring holds 13.6 ms. Load data goes by DMA to TXF; ops by `sm.put`.

## Pass-through (high rates)

`Link.forward(out)`: the board writes new ring bytes raw to USB CDC (REPL port, `micropython.kbd_intr(-1)`), with no decoding; on the PC, `jspe.Parser` + host/monitor.py decode them. On-board decoding stays for low rates. USB CDC assumption: ≤ 1.2 MB/s (FS bulk ceiling), 0.8 MB/s assumed for MicroPython, **unmeasured**.

## Bench checks

Link pins above GPIO31 (gpiobase = 16) in MicroPython; USB CDC rate; on-board parse rate.

## Selection, fallback

`Link(..., pio=True)`; the hardware UART stays the default. Start at B = 16, `WRREG LINK = 2`, wait for H_TX idle, set div 1, `SYNC`. After 3 failed SYNCs: rst_n, reload, hardware UART at B = 16.

## Throughput

Host → chip 1.18 MB/s, chip → host 1.2 MB/s at 48 MHz, ×4 over B = 16. Monitors (monitor_design.md): SPI both lines SCK ≤ 3.3 MHz (from 830 kHz); UART TS=1/2 engine-bound; I2C unchanged. Sustained = min(link, consumer): on-board parsing is unmeasured; pass-through at 0.8 MB/s gives SPI both lines ≈ 2.1 MHz.
