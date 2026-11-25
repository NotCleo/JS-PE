# Decoded monitors (BACKLOG rank 3, MON1)

All monitor pads stay `en` = 0 (formally proven never to drive). The programs have no `.pad`; inputs come through BIND only.

| Monitor | Engines | Words (est.) | Notes |
|---|---|---|---|
| UART | 2 x `uart_rx.s` as is (`RXPIN`, `ENG`, `ORG`); with timestamps `uart_mon.s` (2 engines, shared code) | 2 x 8; `uart_mon.s` 30 | one engine per direction; uart_mon: 8N1, samples by tock from the start-edge capture, record [evt 63] [evt 60] lo hi byte [evt 1] pushed after the stop sample |
| `spi_mon.s` | 2: MOSI, MISO on shared SCK/CS | 14 shared (`TS=1` 40) | modes 0–3 (`MODE12`); `apush` micro-loop; watcher 0 = CS rise abort; n-th MOSI byte pairs with n-th MISO byte |
| `i2c_mon.s` | 1 | 39 | START/STOP on two abort watchers (as i2c_tgt); stretch measured on the low phases next to each ACK (timer /16); no timestamps (no cell left for the wrap watcher) |

## Stream
Data = plain bytes; I2C address + R/W = first byte after START/Sr; ACK implied.

| evt | UART | SPI | I2C |
|---|---|---|---|
| 1 | framing | CS fall | START |
| 2 | parity | CS rise | Sr |
| 3 | | ended mid-byte | STOP |
| 4 | | | NACK |
| 5 | | | stretch: SCL low ≥ `TLOW`, + lo, hi = low time in 16-clk steps |
| 60 | TS captured before the last wrap (uart_mon) | | |
| 61 | | wrap call may be cut (TS) | |
| 62 | timer wrap | wrap | |
| 63 | LOST | LOST | LOST |

**Timestamps** (`-D TS=1`): T free-running (P = 0xFFFF; `TSPRE` 20 ns / 320 ns / 5.12 µs per step, wrap 1.31 / 21 / 335 ms). `mov R, T`, pushed lo, hi: after each UART character (record, uart_mon.s; evt 60 if a wrap came since its start edge), after SPI evt 1–2 (none for I2C). Watcher 1 (call, CC1 = tick) pushes evt 62. The wrap call's latency races the capture, so a capture ≤ 40 clk before or ≤ 32 clk after a wrap waits until that evt 62 is out. Host: time = W · 65536 + 0xFFFF − T, W = evt 62 count before the TS hi byte, minus 1 if T < TSW. Lost entries can include evt 62: after evt 63 the host restarts its time base. SPI: a frame-end abort can cut the wrap call (watcher 0 wins): evt 61 flags it, and the host adds the wrap when the frame's end precedes its start. Cost (SPI, measured): 26 words, CS high ≥ 4.8 µs, first edge ≥ 3.2 µs after CS falls, data hold ≥ 14 clk.

**Never silent loss:** loss = push to a full FIFO or a missed edge, both set OVR → notice `ESC 0x80|e`, and at the next frame end `jovr` → `ovclr`, `evt #63` (before the frame-end event): the host knows which frame is incomplete. The event never blocks (an `@htx` wait would clear latched edges of the next frame); if it is dropped too, OVR is set again and LOST comes at a later frame end. SPI: a full FIFO drops the rest of the frame (`pull` from the empty RX FIFO sets OVR), because a re-armed loop would answer the next edge late and sample late. The guarantee holds inside each program's timing rules. Outside them, a single late-answered edge can flip a bit unreported (D055).

## Rates at 50 MHz
Engine side: UART 3.3 Mbaud per line (uart_rx); with TS back to back ≤ 156 kbaud, 1 Mbaud with ≥ 2.7 idle bits; SPI SCK 12.5 MHz measured (1.78 MHz with TS; CS high ≥ 500 ns, 800 ns after a partial byte or loss); I2C 400 kHz and 1 MHz (ISS).
Sustained rates are set by the host link, not the engines (several streams ≤ 1.5 link bytes per data byte, events 2). The PIO host link (BACKLOG rank 7, B = 4) gives ~4×:

| | B = 16 (312 KB/s) | B = 8 (625 KB/s) | B = 4, PIO (1250 KB/s) |
|---|---|---|---|
| UART both lines | 1 Mbaud continuous, at the limit | ok | ok |
| UART + TS | ≤ 340 kbaud | ≤ 690 kbaud | engine-bound (gap rule) |
| SPI both lines | SCK ≤ 830 kHz | ≤ 1.66 MHz | ≤ 3.3 MHz |
| I2C | 1 MHz | 1 MHz | 1 MHz |

Faster traffic: lossless in bursts (4 FIFO entries per engine + link drain), then LOST.

**Burst capture (documented option, not built):** SPI bytes stored to a MEMMAP region at engine speed, drained after CS rises. Estimated +16 words: store/compare/full path 6, drain loop 6, and 4 to report a frame that starts during the drain (watcher off/on, CS check) instead of losing it silently. Two engine buffers are also needed below `MEM_DATA`, and SCK drops to ≈ 2.9 MHz (one store per byte). Over the 15-word limit, and the PIO link gives the same sustained gain without words.

MEMMAP: no region (no shared data; wrap count kept on the host).
