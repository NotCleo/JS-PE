# Debug family (PROTOCOL_MATRIX 10, items 1.1–1.6)

Program/host only. Chip does the bit timing and the decisions that must land inside one clock period; the host (host/debug.py, MicroPython and CPython) builds command bytes, walks the TAP, computes SWD parities, retries WAIT and models AP registers. At 50 MHz, default slot map unless stated.

## Pins (PINS.md conventions, SPI row uio[3:0])
| Program | p0 | p1 | p2 | p3 |
|---|---|---|---|---|
| jtag_ctrl | TCK out `uio[3]` | TMS out `uio[0]` | TDI out `uio[1]` | TDO in `uio_in[2]` (shift input) |
| jtag_tap | TCK in `uio_in[3]` | TMS in `uio_in[0]` | TDO out `uio[2]` (z outside Shift-xR); input = TRST `uio_in[4]` | TDI in `uio_in[1]` (shift input) |
| swd_host | SWCLK out `uio[3]` | SWDIO `uio[1]` (out/z, shift input) | — | — |
| swd_dp | SWCLK in `uio_in[3]` | SWDIO `uio[1]` (out/z, shift input) | — | — |
TRST (controller side) and nRESET are static: RP2350 GPIOs. jtag_tap reads TRST through p2's input bind while p2's output drives TDO (input and output of a logical pin are independent).

## (a) jtag_ctrl.s — one command = header + data byte, one TDO byte back
Header: [0] level held on the other pin, [1] data goes to TMS (else TDI), [4:2] n − 1 (1–8 bits, LSB first). TCK idles low; TMS/TDI change with the falling edge, TDO is sampled at the rising edge (SPI mode 0 timing, spi_ctrl's tick/tock). A scan = TMS walk, n − 1 bits on TDI with TMS held 0, last bit with TMS held 1, TMS walk. Rejected: a per-TCK (TMS, TDI) pair stream (twice the link bytes, a software bit loop).

## (b) jtag_tap.s — 16-state TAP in code
Each state is one `@rise p0 : jhi/jlo p1, next` (TMS read at the latched edge, valid until the next falling edge). Shift-xR = `loop { @rise p0 : shin }` with TDO following S[0]: an exact shift register of length WL ≤ 16 (S keeps wrapping); IDCODE (32 bits) swaps S with a second register at every 16-bit boundary. The exit edge (rise with TMS = 1) is watcher 0 (CC0 = rise p0 & TMS, abort), whose handler shifts the last bit; watcher 1 (TRST low, abort) → Test-Logic-Reset. Instructions: IDCODE, one user DR (ULEN ≤ 16, written at Update-DR and pushed to the host), everything else BYPASS; IR capture 0b01. Chains: two engines (TDO of one → TDI of the next) or Cleo next to external TAPs. Rejected: decoding per edge with a state register (3–5 instructions per edge instead of 1).

## (c) swd_host.s — transaction on chip, protocol policy on the host
Byte with bit 0 = 1 is a request (as on the wire, parity by the host); writes add 4 data bytes + 1 parity byte. Chip: request, turnaround + ACK (pushed), then read data + parity (pushed) or write data, or a turnaround only for WAIT/FAULT/no reply (write bytes drained). Byte with bit 0 = 0: raw, [1] release (sample), [4:2] n − 1, + 1 data byte, one byte back: line reset, JTAG-to-SWD, idle cycles, TARGETSEL (no ACK). SWCLK idles high; the clock is restarted per word, so all edges inside a word are exact (SWD is static). Rejected: ACK branching on the host (one host round trip per transaction).

## (d) swd_dp.s — DP on chip, AP and SELECT on the host
Request: start bit, 5-bit word (parity checked by PAR), then dispatch by a 16-entry table while the stop and park edges pass (`@rise : jhi/jlo`), so the reply decision costs only a `ret` after the park edge. State code R2 = C | ST computed while idle: 0 ok, 0x1x AP busy (WAIT), 0x20/0x80 sticky (FAULT), 0x100 reset state (only DPIDR read; protocol error = no reply, wait for a line reset). DPIDR (program words), CTRL/STAT (power-up ACK = REQ, WDATAERR, STICKYERR), ABORT (clears them), RDBUFF on chip; AP accesses and SELECT go through a 5-word box per engine (MEMMAP `SWD_BOX`: RDB_L, RDB_H, ST, DL, DH): the chip writes ST = 0x10 | request (+ data), pushes the request byte; the host answers by WRMEM [RDB_L, RDB_H, ST = 0 or 0x20] (ST last). AP reads are posted (return the previous AP read's value), WAIT while the host is busy. Write parity error → WDATAERR. `MDROP=1`: TARGETSEL after a line reset (no ACK driven) selects or deselects (lockout). Rejected: AP data through the RX FIFO (4 entries: a 4-byte value can arrive split across the reply decision).

## (e) SWO
NRZ: uart_rx unchanged (ITM bytes are UART 8N1, LSB first). Manchester SWO has a single start bit and LSB-first data; manchester.s hunts for a 0x55 preamble + SFD and is MSB first: tested decision recorded in PROTOCOL_MATRIX 10.
