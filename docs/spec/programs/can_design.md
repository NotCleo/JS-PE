# CAN 2.0A — design (step 1; not designed in, no RTL/ISS change)

Scope: 11-bit ID, 125–500 kbit/s, one `od` `uio` pad + Pmod pull-up (1 kΩ). Partner: RP2350 PIO node (D090).

## 1. Engines
- **E_RX** always receives, its own frames included: sync, destuff, CRC, ACK, errors, bus idle.
- **E_TX** drives: stuffing, arbitration, CRC send, ACK check.
- Not one engine: one S can't hold unsent and received bits; losing arbitration would need a state handoff.
- Only one engine can own the pad (ARCH 2.1), so E_TX owns it. E_RX requests a dominant bit by setting flag **G_DRV**; E_TX watcher 1 (G_DRV rise, abort) drives 0 until G_DRV falls. 0-word alternative: two pads jumpered.
- Flags: **G_TXA** (E_TX busy, E_RX skips ACK), **G_IDLE** (E_RX: bus idle).

## 2. Bit timing
- BIT = 50 MHz / rate. Timers: P = BIT − 1, periodic, tick = bit boundary.
- E_RX: hard sync on every falling edge (`sedge`=fall, `spin`=bus); tock = sample point SP_RX = 75 %.
- E_TX: own grid, `tsync` at its SOF, no auto-sync (it must not resync on its own edges). Its tock SP_TX = SP_RX + d_f + 2, where d_f ≈ 5 clk is its own falling edge seen at `lvl`.
- A foreign SOF ≤ ~10 clk earlier is absorbed by arbitration.

## 3. Stuffing (ALT/RUN)
- TAPCFG `rl`=5, `alt`=2.
- E_TX: after `p0 <- out`, `jrun` → `@tick : p0 <- q` (drives ~B; tap update gives RUN = 1).
- E_RX: after `shin`, `jrun` → `@tock : p3 <- q` on a pin with no pad, then `jmis serr`. MIS = bus ≠ ~B is the stuff error.
- `q` never steps the LFSR.
- Found: `wcfg SHCFG` and `wrst` zero RUN, so WL cannot change inside the stuffed region. Both engines use WL 16, MSB first; lengths are counted in registers. E_TX loads bytes with `pullh S`.

## 4. CRC-15 (shared LFSR)
- Left form, POLY_HI = 0x8B32; CRC = LFSR[31:17].
- Only E_RX sets `lfen`. SOF goes through `smp`, which is a no-op from state 0.
- E_RX also shifts the CRC bits in: LFSR_HI = 0 iff the CRC is good. A good frame leaves 0; errors clear it.
- E_TX: `rcfg S, LFSR_HI` after its tock of the last data bit. The window to the next tick must cover the arming bound (~10 clk): limit 625 kbit/s (BIT/4 − 7 ≥ 13-clk bound; D105).
- No paper program uses the LFSR.

## 5. Arbitration (MIS)
- CC0 = tock & MIS, abort.
- WV0 = `lost` for SOF+ID+RTR, then `wcfg WV0` ← `berr`.
- `lost`: `evt`, drain the frame's remaining FIFO bytes, `sclr G_TXA`, idle. E_RX keeps receiving.

## 6. ACK
- E_RX: CRC good, delimiter recessive, G_TXA clear → `sset G_DRV` at the ACK-slot tick, `sclr` at the next tick.
- E_TX: `watch #2` (MIS off) after the CRC, sends 1s, then `@tock : jhi pBUS, ackerr`.

## 7. EOF and idle
- E_RX counts 11 recessive samples (ACK delimiter + EOF + IFS), then `sset G_IDLE` and hunts `@fall`.
- SOF is confirmed at tock, then `sclr G_IDLE`.
- The same count is the bus integration at start and after errors.
- A dominant sample restarts it: EOF form errors and overload frames go unflagged.
- E_TX: `@hrx`, wait for G_IDLE, `wrst`, SOF.

## 8. Errors
- E_RX: stuff, CRC, form (CRC delimiter) → `evt` code, 6-bit error flag via G_DRV, clear LFSR_HI, bus integration.
- E_TX: bit error (MIS after arbitration), ACK error → `evt`, 6-bit error flag, drain, idle.
- Always error-active; the host does retransmission, TEC/REC, bus-off.

## 9. Host streams
- TX in: bit count, then SOF..data packed MSB first. Events: SENT, LOST, BITERR, ACKERR.
- RX out: 3 header bytes, then data. Events: OK, CRCERR, STUFFERR, FORMERR. Own frames are echoed.

## 10. Word budget (128-word flop build)
| Part | Words |
|---|---|
| E_RX: SOF 5, bit routine 8, header 7, DLC 5, data 6, CRC 5, ACK 5, idle 6, errors 13 | ≈ 60 |
| E_TX: idle/SOF 5, bit routine 10, fields/CRC 6, ACK 4, lost/errors 8 | ≈ 33 |
| Total | ≈ 93 |

- Fits with uart_tx + uart_rx (109).
- Cuts: DLC clamp −3, stuff-error check −1, merged error tails −4. `.init` is free.

## 11. Possibly RTL (flagged, not solved)
1. No SJW-limited resync: E_RX hard-syncs every edge, E_TX never resyncs. A glitch re-times E_RX; the input filter is the only defence.
2. SHCFG write zeroes RUN: worked around with WL 16 + counters.
3. One owner per pad: the relay delays ACK and error-flag bits ~10 clk.
4. E_TX CRC read window caps the rate at 625 kbit/s (D105).
5. Fault confinement is in the host.
