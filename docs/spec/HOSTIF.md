# HOSTIF — host link (Phase 2b, 2026-11-15)

RP2350 ↔ chip. Decisions D011, D016, D030, D031. Constants are in `isa.yaml` (`host:`); block ports in ARCH.md 2.5; pins in PINS.md 2.

## 1. Physical layer

| Item | Value |
|---|---|
| Pins | H_RX = `ui_in[3]` (host → chip), H_TX = `uo_out[4]` (chip → host), H_RDY = `uo_out[5]` (chip → host, **0 = ready**) |
| Byte | asynchronous 8N1, LSB first, idle high, one stop bit, no parity |
| Bit time | B = 16 clk after reset; 8 or 4 clk selectable (register LINK). At 50 MHz: 3.125 / 6.25 / 12.5 Mbaud |
| Clocking | the host derives `clk` and its baud rate from one clock, so the ratio is exact; the chip re-phases on every start bit |

- Receive: H_RX passes the 2-FF synchronizer. The first clock in which it is low while idle is the start of a byte; bit i (0 = start, 1…8 = data, 9 = stop) is sampled B/2 + i·B clk later. The byte is accepted at the stop sample if stop = 1; the receiver can take a new start bit from the next clock. If stop = 0 the byte is dropped and the receiver waits until H_RX reads high before it looks for a start bit, so the rest of a break is never received as data (D044).
- Transmit: exactly B clk per bit, bytes back to back while data is pending.
- H_RDY is high (not ready) only when the parser is in the data state or has just received an `ESC` (the next byte may be a literal 0x1B), a stream is selected, and that stream's RX FIFO has fewer than 2 free entries. The host must stop within one byte after seeing it high (a hardware CTS input does); the second `ESC` of a literal is a data byte and waits for H_RDY like any other, so a byte sent late plus a literal can never overflow the FIFO (D080). Command bytes are always accepted, whatever H_RDY shows: to regain control of a blocked stream the host ignores H_RDY and sends `ESC STOP` (a host UART with hardware CTS holds back command bytes too, so it switches flow control off for that command).
- After `SEL e` H_RDY shows stream e from the clock after the stop sample of the `SEL` byte. A stream may have been left full, so the host samples H_RDY after that point before the first data byte: it leaves one byte time, or sends the `SEL` twice (D044).
- There is no flow control toward the chip's transmitter. The host must buffer H_TX (UART receive ring ≥ 4 KB recommended).

## 2. Framing

`ESC` = 0x1B in both directions. Any other byte is stream data. After `ESC` exactly one operation byte follows, then that operation's fixed number of argument bytes, which are raw (not escaped).

- Host → chip data goes to the RX FIFO of the *selected* engine (`SEL`). After reset no engine is selected.
- Chip → host data comes from the TX FIFO of the *current* stream; the chip announces every change of stream. After reset there is no current stream.

## 3. Host → chip operations (byte after ESC)

| Op | Name | Arguments | Effect | Reply |
|---|---|---|---|---|
| 0x1B | literal | — | data byte 0x1B to the selected stream | — |
| 0x00–0x03 | `SEL e` | — | select engine e for following data bytes | — |
| 0x20 \| m | `STOP m` | — | stop the engines in mask m (bit e = engine e); an injected instruction that has not completed is cancelled | — |
| 0x30 \| m | `RUN m` | — | run the engines in m, all in the same clock; clears their `halted` flags | — |
| 0x40 \| m | `STEP m` | — | each engine in m executes one instruction, then stops | — |
| 0x50 \| m | `RST m` | — | engine reset (ARCH.md 9.1) and flush of both FIFOs of each engine in m; engines stay stopped | — |
| 0x60 \| e | `EXEC e` | word lo, hi | inject one instruction into stopped engine e (ARCH.md 9.2) | — |
| 0x70 | `WRMEM` | addr lo, addr hi, n − 1, then n words (lo, hi) | write n = 1…256 words from `addr` (wraps at 512) | — |
| 0x71 | `RDMEM` | addr lo, addr hi, n − 1 | read n words | `ESC 0xC1` + 2n bytes |
| 0x72 | `WRREG` | reg, lo, hi | write a host register | — |
| 0x73 | `RDREG` | reg | read a host register | `ESC 0xC3`, lo, hi |
| 0x74 | `SYNC` | — | no effect | `ESC 0xC4`, ID lo, ID hi |

- An operation takes effect in the clock after the stop-bit sample of its last byte. `addr hi` and the engine field use their low bits only; other bits must be 0.
- `STOP` against an injected instruction, two one-clock cases (defined behaviour, D086): an injected instruction that fires in the clock of the STOP pulse (the clock after the stop sample) completes with all its effects, as if it had fired one clock earlier; an injected `ld` granted in that clock is dropped, an injected `st` granted in that clock is written, with its base write-back. The `st` case cannot be reached through the link: an injected `ld`/`st` is granted within 4 clk of its `EXEC` pulse, or never if the engine owns no slot.
- Memory access uses free memory slots (ARCH.md 2.2). A word is written when its second byte has arrived; with engines stopped this takes ≤ 1 clk. The link holds one pending write: if a slot does not become free before the next word is complete, the earlier word is lost and a link error is reported. This can only happen while all engines saturate their slots.
- Only one reply is produced at a time: the host waits for the reply to `RDMEM`, `RDREG` or `SYNC` before sending another of these. A reply operation that arrives while a reply is still in progress replaces it: the byte on H_TX (and the second byte of a notice or stream escape) completes, the rest of the old reply is dropped, including words not yet read and a read in flight, and the new reply starts (D085).
- Any other op value is ignored and reported as a link error.

## 4. Chip → host escapes (byte after ESC)

| Code | Meaning | Followed by |
|---|---|---|
| 0x1B | literal data byte 0x1B of the current stream | — |
| 0x00–0x03 | following data and events belong to engine e (stream change) | — |
| 0x40 \| c | event c (0–63) from the engine of the current stream (`evt #c`), in order with its data | — |
| 0x80 \| e | engine e: overrun flag became set | — |
| 0x84 \| e | engine e stopped by `halt` | — |
| 0x88 \| e | engine e trapped on an undefined instruction | — |
| 0x8C | link error (section 9) | — |
| 0xC1 | `RDMEM` reply | 2n raw bytes |
| 0xC3 | `RDREG` reply | lo, hi |
| 0xC4 | `SYNC` reply | ID lo, hi |

Transmit scheduling, highest first: a reply in progress; pending notices (0x80–0x8C); engine streams. A stream is served until its TX FIFO is empty, or after 4 entries when another engine has data waiting; then the next non-empty stream in ascending engine order. Escape sequences are never interleaved. Notices and replies do not change the current stream. A notice reaches the host within about 2 byte times when the link is idle.

## 5. Host registers (`WRREG` / `RDREG`)

| Addr | Name | Access | Reset | Contents |
|---|---|---|---|---|
| 0x00 | ID | R | 0x4A10 | chip identification and version |
| 0x01 | LINK | RW | 0 | [1:0] bit time: 0 = 16, 1 = 8, 2 = 4 clk (3 = 16). Applies from the first start bit after the write in both directions; the host waits for H_TX idle, changes its baud rate, then sends `SYNC` |
| 0x02 | SLOTMAP | RW | 0xE4 | [2s+1:2s] = engine owning memory slot s. Accepted only while all engines are stopped and none has an injected instruction pending |
| 0x03 | SIG | RW | 0 | [7:0] signal flags G7…G0; a write replaces all eight |
| 0x04 | FILT | W | 0 | input filter enable, [7:0] `ui_in`, [15:8] `uio_in` |
| 0x05 | PINS | R | — | synchronized input levels, [7:0] `ui_in`, [15:8] `uio_in` |
| 0x06 | ECLR | W | — | [3:0] clear the overrun flag, [7:4] clear `halted` and `trap`, of the engines in the mask; a clear wins over the engine setting that flag in the same clock (D065, D084) |
| 0x08–0x0B | EST0–EST3 | R | 0 | engine status, below |
| 0x10–0x17 | PAD_UO0–7 | W | 0 | pad configuration of `uo_out[k]`; 0x14, 0x15 (host link) are ignored |
| 0x18–0x1F | PAD_UIO0–7 | W | 0 | pad configuration of `uio[k]` |

Unlisted addresses: writes ignored, reads 0. Write-only registers read 0.

PAD: [1:0] `lp` logical pin, [3:2] `eng` engine, [4] `inv`, [5] `od`, [6] `en` (ARCH.md 2.1).

EST: [8:0] PC; [9] running; [10] waiting (an instruction is armed and its guard is false); [11] overrun; [12] trap; [13] halted; [14] RX FIFO not empty; [15] TX FIFO not empty.

Engine registers and configuration are not host registers. The host reaches them by `EXEC`: e.g. `li R0,#…`/`wcfg SHCFG,R0` to configure, `push R0`/`pushh R0` to read a register through the engine's stream.

## 6. Procedures

**Load and start** (after power-up, reset, deselect, or to replace a program):
1. Pulse `rst_n` (whole chip), or `STOP m`, `RST m` for the engines being replaced. `SYNC` and check ID.
2. `WRREG SLOTMAP` if the image asks for a non-default map (all engines stopped).
3. `WRMEM` the image; `RDMEM` it back and compare (load integrity, D016).
4. `WRREG FILT`; `WRREG PAD_*` for every pad the program drives, with `en` = 0.
5. `EXEC` the engine's initialisation instructions (bindings, shifter, timer, constants, initial pin levels, watchers), then `EXEC jmp entry` (absolute form).
6. `WRREG PAD_*` again with `en` = 1.
7. `SEL e`, pre-fill data if wanted, then one `RUN m` for all engines that must start together.

**Stop / inspect / resume**: `STOP m`; `RDREG ESTe`; `EXEC` register read-outs; `RUN m` (the engine refetches from PC).
**Single step**: `STOP`, then `STEP` + `RDREG ESTe` per instruction.
**Abort a hung program**: `STOP m`, `RST m`; pads owned by the engine return to released / value 0 (the host should clear `en` first if a pad must not glitch low).

## 7. Load image (`.bin`)

Little-endian. Header: `"JSPE"`, version (1 byte = 1), 0x00, record count (u16). Records, replayed by the host in file order:

| Type | Payload | Host action |
|---|---|---|
| 0x01 MEM | addr u16, n u16, n words | `WRMEM` in chunks of ≤ 256 words, `RDMEM` verify |
| 0x02 REG | reg u8, value u16 | `WRREG` |
| 0x03 EXEC | engine u8, word u16 | `EXEC` |
| 0x04 RUN | mask u8 | `RUN` |
| 0x05 RST | mask u8 | `STOP` + `RST` |
| 0x06 CLK | clk_hz u32 | none on the chip: the host stops if it does not drive `clk` at clk_hz (D089) |

The assembler emits: CLK (when the sources define `CLK_HZ`; all files must agree); RST; REG SLOTMAP (only with `.slots`); MEM; REG FILT; REG PAD with `en` = 0; EXEC (`.init`, then `.entry`); REG PAD with `en` = 1; RUN. The `.hex` file is the plain 512-word memory image for simulation.

## 8. Stream FIFOs and bandwidth

| Item | Value |
|---|---|
| FIFOs | per engine: RX 4 x 8 bit (host → engine), TX 4 x 9 bit (engine → host, bit 8 = event) |
| Engine side | `@hrx : pull`, `@htx : push` block; unguarded forms never block and set OVR instead (ISA.md 8.4); auto-transfer (ISA.md 7) |
| Raw link rate, each direction, 50 MHz | 312.5 KB/s (B = 16), 625 KB/s (B = 8), 1250 KB/s (B = 4) |
| Efficiency | one stream: 1 link byte per data byte, 2 for a data byte equal to 0x1B. Stream change: +2 bytes. Event: 2 bytes. Several active streams, worst case: 6 link bytes per 4 data bytes |
| Host → chip latency | byte in the FIFO 9.5 B + 3 clk after its start bit (155 / 79 / 41 clk at B = 16 / 8 / 4, D086) |
| Program load | 1 KB in ≈ 3.3 ms at B = 16 (plus read-back) |

UART at 1 Mbaud full duplex moves 100 KB/s in each direction: 32 % of the slowest link setting. H_RDY stops the host with 2 FIFO entries in reserve, so no host byte is lost at any setting.

Reload next to running engines (D088): a reply holds H_TX, and the streams of running engines wait behind it in their 4-entry TX FIFOs; a 1 Mbaud UART receiver fills its FIFO in 2 000 clk. While any engine runs, the load read-back therefore uses `RDMEM` of at most ⌊(200/B − 3)/2⌋ words (4, 11, 23 at B = 16, 8, 4), so that one reply plus the byte already on H_TX lasts (2n + 3)·10·B < 2 000 clk; the host waits for each reply before the next `RDMEM`. An engine streaming to the host faster than one byte per 500 clk must be stopped for a reload. The host does not send `RDREG` or `SYNC` during the reload either.

## 9. Errors

| Condition | Chip behaviour | Host sees |
|---|---|---|
| Stop bit low on H_RX | byte dropped | `ESC 0x8C` |
| Break (stop bit low and data 0x00) | byte dropped, parser returns to the data state, a command in progress is abandoned; reception resumes after H_RX has returned high | `ESC 0x8C` once |
| Unknown op after ESC | ignored | `ESC 0x8C` |
| Data byte with no engine selected | dropped | `ESC 0x8C` |
| Data byte for a full RX FIFO | dropped, engine OVR set | `ESC 0x80\|e` |
| `EXEC` to a running engine; `EXEC`, `RUN` or `STEP` to an engine whose injected instruction is still pending; SLOTMAP write while an engine is running or has an injected instruction pending; lost memory write | ignored | `ESC 0x8C` |
| Engine overrun (missed event, FIFO misuse) | OVR sticky | `ESC 0x80\|e` once per setting |
| `halt` / undefined instruction | engine stops | `ESC 0x84\|e` / `ESC 0x88\|e` |
| Host loses chip → host bytes | none (no back-pressure in this direction) | host resynchronizes: wait for idle, `SYNC` |
| Reset or deselect | all state lost | no reply to `SYNC` until reset is released; reload (section 6) |

Recovery from any host-side confusion: send a break (H_RX low for ≥ 11 bit times), then `SYNC`.

## 10. Host side (MicroPython sketch, final code in `host/`)

```python
from machine import UART, Pin
link = UART(1, baudrate=3_000_000, tx=Pin(20), rx=Pin(37), cts=Pin(38),
            flow=UART.CTS, rxbuf=8192)          # clk = 48 MHz (PINS.md 2): B = 16
ESC = 0x1B
def op(code, *args): link.write(bytes((ESC, code) + args))
def wrmem(addr, words):
    op(0x70, addr & 0xFF, addr >> 8, len(words) - 1)
    link.write(b"".join(w.to_bytes(2, "little") for w in words))
def run(mask): op(0x30 | mask)
def send(e, data): op(e); link.write(data.replace(b"\x1b", b"\x1b\x1b"))
```
