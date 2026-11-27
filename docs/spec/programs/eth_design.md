# Ethernet family: link pulses, CRC-32, MDIO (PROTOCOL_MATRIX 10, items 3.1–3.6)

Program/host only. The chip makes and times the pulses, the CRC and the MDIO frames; autonegotiation arbitration (ACK handshake, priority resolution, parallel detection), PHY register semantics and the command API live on the host (host/eth.py, MicroPython and CPython). 50 MHz, default slot map. Full 10BASE-T frames stay out (ROADMAP_V2 §2, D125). Every time constant derives from `CLK_HZ`; tests scale `CLK_HZ` down (same ratios, shorter runs) and run the as-written rate once.

## Pins (10BASE-T Pmod of ROADMAP_V2 §1: TXP `uio[0]`, TXN `uio[1]`, RXD `uio[2]`, LED `uio[3]`; MDIO pair on `uio[5:4]`)
| Program | p0 | p1 |
|---|---|---|
| lp_tx | TXP `uio[0]` (pulse high) | TXN `uio[1]` (held 0: idle = both low) |
| lp_rx | RXD `uio_in[2]` (comparator, idles 0) | LED `uio[3]` (1 = link up) |
| mdio_ctrl | MDC `uio[4]` | MDIO `uio[5]` (driven or released, shift input) |
| mdio_phy | MDC `uio_in[4]` (`PINS2`: `uio_in[6]`) | MDIO `uio[5]` (`PINS2`: `uio[7]`; driven only for TA + read data) |
MDIO needs a pull-up (1.5 kΩ on the Pmod). crc32 uses no pad.

## (a) lp_tx.s — NLP or FLP bursts (3.1 TX, 3.2 TX)
Timer periodic, period `TP` = 62.5 µs (one FLP pulse position); tick = pulse start, tock `PW` clk later = pulse end (100 ns = 5 clk), so the width is exact (the tock guard is armed 1 clk after the tick fires; `.assume armed` on the long tick wait). A period is `NTICK` = 256 positions = 16 ms. NLP: one pulse per period. FLP: 17 clock pulses with the 16 data positions between them (`p0 <- out` of the code word, LSB first = D0 first), 2 ms per burst. Host bytes between periods: 0x00 NLP, 0x01 + 2 bytes FLP with that link code word (repeated every period until changed), 0x02 silence (link loss, autoneg restart).

## (b) lp_rx.s — NLP link integrity and FLP decode (3.1 RX, 3.2 RX)
Timer one-shot, prescaler 256 (5.12 µs steps), restarted by every pulse (rise of RXD) with `LOSS` (100 ms); `mov R1, T` before the restart gives the spacing to the previous pulse. tock (`TEND` = 205 µs after the last pulse, watcher 0) ends a pulse group; tick (no pulse for `LOSS`, watcher 1) = link loss. Inside a group each pulse after a clock pulse is short (≤ `TH` = 94 µs: data bit 1, the next must be a short clock pulse) or long (data bit 0, this pulse is the clock); bits enter R2 by `rrc` (D0 first). Group end: 1 pulse = NLP; 16 bits and ending on a clock pulse = FLP → evt FLP + 2 bytes; else evt BAD. NLPs spaced ≥ `TMIN` (4 ms) count; the `LC`-th (3) in a row → evt UP + LED; silence `LOSS` while up → evt DOWN. A pulse < 1 timer step after the previous one (ringing) is ignored. Link-test window `TMIN`…`LOSS` (802.3 link_test_min 2–7 ms, link_loss 50–150 ms, lc_max 2–10).

## (c) crc32.s — IEEE 802.3 CRC-32 at program rate (3.3)
Shared LFSR in the right form, POLY 0xEDB88320, preset 0xFFFFFFFF. Host: length (2 bytes, low first) + that many bytes; each byte is shifted out LSB first on a padless pin with TAPCFG `lfen` (one LFSR step per bit, 8 clk per byte); reply = CRC xor 0xFFFFFFFF, 4 bytes low first (= the FCS bytes in wire order, = zlib.crc32). Check mode for free: frame + FCS gives 0x2144DF1C. Needs the LFSR alone (not with usb_ls or can). Rejected: a table CRC in memory (256 words), the auto-pull stream (cannot count bytes inside the loop).

## (d) mdio_ctrl.s — Clause 22/45 station (3.4)
swd_host's tx/rx bit routines: MDC idles high, the station's bits change at the falling edge (tick), MDC rises `SMP` clk later (tock) and read data is sampled there. Host: header 2 bytes (low first) = the 14 frame bits ST OP PHYAD/PRTAD REGAD/DEVAD in [13:0] (sent MSB first), [14] = 1 skips the preamble (32 ones); OP[1] = 0 (C22 write, C45 address/write) → + 2 data bytes, TA 10 driven; OP[1] = 1 (C22 read, C45 read/read-inc) → TA released, 16 bits sampled, reply 2 bytes, preceded by evt NOPHY if the second TA bit was not 0. Frame encoders on the host. MDC ≤ 2.5 MHz (`BIT` 20).

## (e) mdio_phy.s — PHY register file (3.5)
Clocked by MDC (`@rise`). Hunt: ≥ `NPRE` ones (32; ≥ 17 keeps sync on a shared bus: no frame holds 17 ones), the first 0 is ST[1]; then 13 header bits in one loop. C22 read for `ADDR`: `ld` from MEMMAP `MDIO_BOX` + REGAD, drive TA2 = 0 and 16 bits (`out`, one per rising edge), release. Writes: 18 bits sampled (the TA pair slides out of the 16-bit window), stored in the box and pushed (type byte + 2 bytes) for the host's register semantics (self-clearing reset, read-only bits, latched status: the host rewrites the box with WRMEM). Clause 45 (`C45 = 1`): address / write frames pushed to the host; read / read-increment frames answer from box word 32, which the host refills after each address frame (host latency ≤ one frame). Rejected: register values from the host per read (the read data starts 2 MDC periods after REGAD: no host round trip fits).

## (f) MII / RMII-10 TX probe (3.6)
Measured in the ISS (sw/tests/test_eth.py): pins per engine (4 logical, E acts on two, only one with data), clk per nibble/dibit vs available, data source rate (host link vs 1.25 MB/s, memory `ld` gaps). Result in PROTOCOL_MATRIX 10.

## Memory
New MEMMAP region `MDIO_BOX` (33 words, 422…454): 32 C22 registers + C45 read word; `MEM_DATA` moves to 422 (all code ends ≤ 418).
