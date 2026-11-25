# Decoded monitors (BACKLOG rank 3, MON1)

All monitor pads stay `en` = 0 (formally proven never to drive). The programs have no `.pad`; inputs come through BIND only.

| Monitor | Engines | Words (est.) | Notes |
|---|---|---|---|
| UART | 2 x `uart_rx.s` as is (`RXPIN`, `ENG`, `ORG`) | 2 x 8; `TS=1` 2 x ≈ 22 | one copy per direction |
| `spi_mon.s` | 2: MOSI, MISO on shared SCK/CS | 2 x ≈ 12 | modes 0–3 (`MODE12`); `apush` micro-loop; watcher 0 = CS rise abort; n-th MOSI byte pairs with n-th MISO byte |
| `i2c_mon.s` | 1 | ≈ 35 | after each SCL rise samples b, CC0 = SCL fall or SDA edge away from b; `@c0` with SCL high = START (b = 1) / STOP (b = 0) |

## Stream
Data = plain bytes; I2C address + R/W = first byte after START/Sr; ACK implied.

| evt | UART | SPI | I2C |
|---|---|---|---|
| 1 | framing | CS fall | START |
| 2 | parity | CS rise | Sr |
| 3 | | ended mid-byte | STOP |
| 4 | | | NACK |
| 5 | | | stretch: SCL low > `TLOW`, + byte = low time / 16 clk (saturating) |
| 6 | | | START/STOP mid-byte |
| 62 | timer wrap | wrap | wrap |
| 63 | LOST | LOST | LOST |

**Timestamps** (`-D TS=1`): T free-running (P = 0xFFFF; `TSPRE` 20 ns / 320 ns / 5.12 µs per step, wrap 1.31 / 21 / 335 ms). `mov R, T`, pushed lo, hi at once: before each UART character, after SPI evt 1–2, I2C evt 1–3. Watcher 1 (call, CC1 = tick) pushes evt 62 in order. Time = wraps · 65536 + 0xFFFF − T; a TS < 256 directly after a wrap predates it.

**Never silent loss:** loss = push to a full FIFO or a missed edge, both set OVR → notice `ESC 0x80|e`, and at the next frame end `jovr` → `@htx : evt #63`, `ovclr` (before the frame-end event): the host knows which frame is incomplete.

## Rates at 50 MHz
Engine side (verified in steps 2–3): UART 3.3 Mbaud per line (≈ 1 Mbaud with TS), SPI SCK ≥ 4 MHz, I2C 400 kHz (1 MHz target).
Sustained limit = host link (several streams ≤ 1.5 link bytes per data byte, events 2):

| | B = 16 (312 KB/s) | B = 8 (625 KB/s) |
|---|---|---|
| UART both lines | 1 Mbaud continuous, at the limit | ok |
| UART + TS | ≤ 340 kbaud | ≤ 690 kbaud |
| SPI both lines | SCK ≤ 830 kHz | ≤ 1.66 MHz |
| I2C | 1 MHz | 1 MHz |

Faster traffic: lossless in bursts (4 FIFO entries per engine + link drain), then LOST.

MEMMAP: no region (no shared data; wrap count kept on the host).
