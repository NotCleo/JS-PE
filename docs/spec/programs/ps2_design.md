# PS/2 family (PROTOCOL_MATRIX 10, items 2.1, 2.2)

Program/host only. The chip does the wire (framing, clock generation, request-to-send, ACK, inhibit, error flags); keyboard and mouse command models live on the host (host/ps2.py, MicroPython and CPython). At 50 MHz, default slot map. CLK and DATA are open drain on uio pads (a pull-up per line, Pmod as for I2C).

## Pins (PINS.md 3.4 row; `PINS2 = 1` for a second instance on one bus in the tests)
| Program | p0 | p1 |
|---|---|---|
| ps2_dev | CLK `uio[6]` (od; `PINS2`: `uio[4]`) | DATA `uio[5]` (od, shift input; `PINS2`: `uio[7]`) |
| ps2_host | same | same |

## Wire (both directions)
Frame: start 0, 8 data LSB first, odd parity, stop 1. The device makes the clock (half period `HALF`, 2000 clk = 12.5 kHz as written, 1500–2500 for 10–16.7 kHz). Device → host: the device changes DATA in the middle of CLK high, the host samples at the CLK fall. Host → device: the host holds CLK low ≥ 100 µs (inhibit), pulls DATA low, releases CLK; the device clocks 11 pulses, the host changes DATA after each fall, the device samples in the middle of CLK high; after the stop bit the device pulls DATA low (ACK) for the 11th pulse.

## (a) ps2_dev.s — device framing on chip, keyboard/mouse on the host
Host byte = one frame to send; one byte in flight: the chip answers `evt SENT` or `evt ABORT` (the host inhibited before the stop bit: byte not delivered, the host resends). Before a frame the bus must be idle (CLK and DATA high) `IDLE` clk (50 µs). Timer periodic with period `HALF`: tick = CLK edge, tock = middle of the phase. In every CLK high phase the device reads CLK: low = host inhibit → abort. Request-to-send = CLK rises with DATA low (watcher 0 in idle, plus a level check when idle is entered). Received command byte → pushed, preceded by `evt DROP` (a byte waiting for the bus was dropped: the command response goes first) and `evt PERR` on a parity error; stop bit 0 → no ACK, clocks on until DATA is released, `evt FERR` (byte dropped); host inhibit during a receive → `evt RABORT`. The device owns the clock, so it has no timeout.

## (b) ps2_host.s — host framing on chip
Host bytes: 0x00 release (device may send), 0x01 inhibit (CLK held low), 0x02 + byte: send the byte (inhibit `TBIT` (the frame timeout, 200 µs), DATA low, CLK released, bits after each device fall, parity from PAR, stop released, ACK sampled at the 11th fall) → `evt ACK` or `evt NACK`. Received frames: watcher 1 (CLK fall, armed in idle) starts the receive; data byte pushed, preceded by `evt PERR` on a parity error; start 1 or stop 0 → `evt FERR` (byte dropped; frames are read as a 9-bit word, start + data, so all 11 bits are consumed). Timeouts: the timer (prescaler 16) is restarted by every CLK fall (TCFG hard sync); its tick = no fall for `TBIT` (200 µs) inside a frame, or no first clock `TFIRST` (15 ms) after a request-to-send → watcher 0 → `evt TMO`, lines released.

## (c) Host side (host/ps2.py)
Device personalities (keyboard: FF → FA AA, ED + LEDs → FA FA, F2 → FA AB 83, F4/F5 → FA, FE → resend last, EE → EE, scan codes; mouse: FF → FA AA 00, F2 → FA ID, F3 rate, IntelliMouse sequence F3 C8, F3 64, F3 50 → ID 3, 3- and 4-byte packets) drive ps2_dev through a one-byte-in-flight queue (resend on ABORT, requeue on DROP). Host driver: command → ACK (FA) / resend (FE) / error handling over ps2_host.

Rejected: a per-bit host round trip (the inhibit check and ACK must land inside a CLK phase); parity in software (PAR and `q` do it); a sniffer (would need a third engine; optional, not built).
