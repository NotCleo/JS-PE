# PIO host link at LINK B = 4 (design note, BACKLOG rank 7)

Host-only; chip side exists (HOSTIF 1). Not run on hardware.

## Clocking (exact ratios, D219)

| sys | `clk` (PWM, GPIO16) | PIO div | Link | Use |
|---|---|---|---|---|
| 96 MHz (TT firmware default) | ÷2 = 48 MHz | 1 (8 cyc/bit) | 12 Mbaud = clk/4 | default |
| 100 MHz (`machine.freq(100_000_000, 100_000_000)`) | ÷2 = 50 MHz | 1 | 12.5 Mbaud | 50 MHz option |
| 192 MHz | ÷4 = 48 MHz | 2 | 12 Mbaud | more CPU for decoding; above the 150 MHz rating |

Rejected: 144 MHz (÷3 gives `clk` a 33/67 % duty cycle). B = 16 on the same programs: PIO div 4.
The link pins (GPIO20/37/38) sit above GPIO31, so the PIO block uses GPIOBASE = 16 (RP2350B); MicroPython support to be checked on the board.

## TX (H_RX = GPIO20 out + side-set + set; in_base = H_RDY, GPIO38)

The FIFO word is `data << 1 | force`. force = 1 for command bytes (ESC, op, args, the first ESC of a literal); data bytes and the second ESC of a literal wait for H_RDY low (HOSTIF 1, D080). A hardware CTS cannot do this per byte.

```
.side_set 1 opt
    pull            side 1 [5]  ; stop bit / idle
    out x, 1
    jmp x-- send                ; force: skip the H_RDY check
    wait 0 pin 0                ; H_RDY low = ready
send:
    set y, 7        side 0 [7]  ; start bit
bit: out pins, 1
    jmp y-- bit     [6]
```

80–81 cycles per byte; the check sits right before the start bit, so at most the byte in flight is sent after H_RDY rises. Break: once the TX FIFO is empty, `sm.exec("set(pins, 0)")`, wait ≥ 11 bits, then `set(pins, 1)`.

## RX (H_TX = GPIO37, jmp_pin = H_TX)

```
start: wait 0 pin 0
    set x, 7        [8]         ; [8], not [10]: 2-cycle input sync at div 1
bit: in pins, 1
    jmp x-- bit     [6]
    jmp pin ok
    wait 1 pin 0                ; framing error: wait for idle
    jmp start
ok: push
```

The sample falls 0.5–0.625 bit in (the delay gets tuned in the emulator). Ratios are exact, and pad delay is ~0.12 bit.

## DMA ring

One rp2.DMA channel reads RXF + 3 as bytes (shift right, byte in [31:24]), DREQ PIO RX. It writes a 16 KB ring (write-ring 14, 32 KB allocated for alignment), count 2^28 − 1, chained to itself. Python reads `count` for the produced total: produced − consumed > 16 KB means an overrun, which leads to resync (HOSTIF 9). The ring holds 13.6 ms at full rate. Bulk TX (load) goes through a second channel to TXF; short ops use `sm.put`.

## Selection and fallback

`Link(..., pio=True)` (default stays the hardware UART). The link starts at B = 16, then `WRREG LINK = 2`, waits for H_TX idle, sets div 1, then `SYNC`. If 3 SYNCs fail: rst_n, reload, hardware UART at B = 16.

## Throughput (design numbers)

Raw 1.18 MB/s host → chip and 1.2 MB/s chip → host at 48 MHz (1.25 at 50 MHz), 4× B = 16. Monitor sustained rates follow monitor_design.md: SPI both lines SCK ≤ 3.3 MHz (830 kHz now), UART TS=1/TS=2 engine-bound, I2C unchanged. This only holds if the host decoder drains the ring as fast on average; MicroPython parse rate gets measured, and the fallback is raw ring forwarding to the PC.
