; can.s - CAN 2.0A node (11-bit ID, data + remote frames). Not designed in (D102, can_design.md).
; words: RX 66, TX 62 (128 total); CAN uses 128/128 words: runs alone on the flop build (-D ORG_TX=0 -D ORG_RX=62) | clk/bit: BIT = 100 (500 kbit/s), sample point SPC = 75 clk | engines: 2 (E_RX, E_TX)
; pins: bus = uio[4], open-drain, external pull-up (PINS 5); E_TX owns the pad, E_RX reads it.
; LIMITS (stated, not hidden):
;   1. resync = hard re-time on every recessive->dominant edge (TCFG hard sync), i.e. SJW unlimited; no
;      phase-error limit, so a dominant glitch longer than the input filter re-times E_RX and can misframe.
;      E_TX never resyncs inside its own frame (own grid from SOF).
;      Measured (test_can_glitch_behaviour, BIT 100): an 8-clk dominant glitch at the input that starts 28..72 clk
;      into a recessive bit drops that bit (sample pushed past the bit end) -> CRC error, error flag, retry; before
;      28 or after the sample point it is harmless. No frame is delivered as good.
;   2. rate <= 625 kbit/s (BIT >= 80): E_TX reads the CRC from the shared LFSR after E_RX has stepped the last data
;      bit; the window BIT - SP_TX = BIT/4 - 7 must cover the 13-clk bound of `@tock : nop` -> `@tick`.
;      125-500 kbit/s are the supported rates (tested: 500 and 125, +-1 % between nodes).
;   3. fault confinement on the host: the chip is always error-active; TEC/REC, error-passive, bus-off and
;      retransmission are done by host software from the events.
;   4. ACK delimiter and EOF are not checked (128/128 words; the check costs 4): TX event 1 (sent) and RX event 1
;      (OK) come at the ACK slot. A CRC-error flag of another receiver (after the ACK delimiter) is not seen by
;      E_TX, so that receiver loses the frame while the host gets "sent". Trade-off rejected: the 4 words would
;      come from the DLC > 8 clamp (-3, then E_RX misparses legal DLC 9..15 frames and error-flags them for every
;      node) plus unrolling the CRC-error wait (-1).
;   5. 29-bit (extended) frames are not tolerated: SRR reads as RTR, the CRC fails and E_RX sends an error flag
;      (tolerating them costs 3 words). Use on 11-bit-only buses.
;   6. host read rate: E_RX pushes 3 + 8 bytes + 1 event per frame without waiting (@htx would stall the bit
;      grid): the host must read >= 1 byte per 8 CAN bits, or bytes / events are dropped (OVR notice).
;   7. a stuff-bit mismatch inside SOF + ID + RTR is reported as arbitration lost (fault case only: stuff bits are
;      equal on all transmitters up to the first differing bit).
; RX (E_RX): receives every frame, its own included. Bit grid: periodic timer, hard sync on falling edges,
;   tock = sample point. Stuffing: RUN limit 5; a stuff bit is booked with `q` (ALT = stuff) on padless p3 and
;   checked with MIS (bus = previous bit -> stuff error). CRC-15: shared LFSR, left form, POLY 0x4599 << 17,
;   stepped by every `shin` (SOF by `smp`: a leading 0 from state 0 does not change it); the 15 CRC bits are
;   shifted too, so LFSR_HI = 0 iff the CRC is good (a good frame leaves it 0, an error clears it).
;   WL stays 16 for the whole frame: `wcfg SHCFG` / `wrst` would zero RUN inside the stuffed region.
;   ACK: requested through flag G_DRV (E_TX drives the pad) unless G_TXA (own frame). Bus idle (flag G_IDLE)
;   after 11 recessive samples: ACK delimiter + EOF + IFS, also bus integration at start and after errors.
;   A dominant sample there restarts the count (EOF form errors and overload frames are not flagged).
; RX host: per frame 3 header bytes: [ID10..8 in bits 2:0, bits 7:3 = the previous frame's last bits, 0 after load] [ID7..0] [ID0 RTR IDE r0 DLC3..0], then the data
;   bytes (min(DLC, 8), none for RTR = 1), then an event: 1 = frame OK (CRC good, delimiter recessive),
;   2 = CRC error, 3 = stuff error, 4 = form error (CRC delimiter). Errors: active error flag (6 bits) from
;   the next bit, the CRC error after the ACK delimiter; partial bytes already pushed stay in the stream.
; TX (E_TX): owns the bus pad. Own bit grid: `tsync` when it drives SOF, no auto-sync; tock at SP_TX = SPC + 7
;   (after E_RX's sample of its own edge). Starts on guard @c1 = G_IDLE (E_RX clears it at the SOF edge); a node
;   whose SOF lands 0..24 clk before ours is either deferred to or arbitrated against cleanly (tested).
;   Arbitration tolerates a late own SOF of < BIT - SP_TX (18 clk at 500 k). Stuffing: after each `out`, RUN = 5
;   -> `q` drives the stuff bit (ALT = ~B, booked, not in the CRC). CRC: `rcfg S, LFSR_HI` after its tock of the
;   last data bit (E_RX has stepped the LFSR by then) puts CRC14 in S[15]. Watcher 0 (tock & MIS, abort) -> `mis`:
;   inside SOF + ID + RTR (return address of that `call`, which runs to IDE: the mismatch shows at the tock)
;   arbitration lost, else bit error; both watchers off then, so E_RX's view of E_TX's own error flag (a stuff
;   error) adds no second flag. Watcher 1 (cell G_DRV | G_IDLE, call mode) drives E_RX's ACK and error flags;
;   a G_IDLE rise returns at once; while idle it returns to the interrupted wait, inside an own frame it ends it. ACK: bus dominant at the ACK-slot tock, else ACK error.
; TX host: per frame [NB] [B0] [B1..B_NB]: B0 bits 7:5 = SOF (0), ID10, ID9; B1..B_NB = ID8..ID0 RTR IDE r0
;   DLC3..0 and the data bytes, MSB first, no CRC (NB = 2 + data bytes). The host keeps the FIFO ahead of the
;   bits (a late byte stretches a bit). Events: 1 = sent (ACK seen), 2 = arbitration lost, 3 = bit error,
;   4 = ACK error; bit and ACK errors send an active error flag. After an abort the rest of the frame is
;   drained from the FIFO; an E_RX error event during an own frame also ends it (no TX event). Retransmission
;   is the host's (limit 3).

.equ BIT,    100                ; clk per bit (50 MHz / 500 kbit/s)
.equ SPC,    (3 * BIT) / 4      ; E_RX sample point after the synchronizing edge
.equ BUSPIN, 12                 ; bind source of the bus: uio_in[4]
.equ G_DRV,  0                  ; signal flags: E_RX requests a dominant bit
.equ G_TXA,  1                  ; E_TX is sending a frame
.equ G_IDLE, 2                  ; bus idle (E_RX)
.equ EV_OK, 1
.equ EV_CRC, 2
.equ EV_STUFF, 3
.equ EV_FORM, 4
.equ EV_SENT, 1                 ; TX stream events
.equ EV_LOST, 2
.equ EV_BITERR, 3
.equ EV_ACKERR, 4
.equ SPTX,   SPC + 7            ; E_TX tock: bus settled after its own edge, E_RX has sampled

; ---------------- receiver ----------------
.equ ENG_RX, 1
.equ ORG_RX, 288
.engine ENG_RX
.init
        li    R0, #BIT - 1
        mov   P, R0
        li    R0, #BIT - SPC
        mov   C, R0                     ; tock SPC clk after each synchronizing edge
        li    R0, #0x0800               ; TCFG: hard sync on the falling edge of p0
        wcfg  TCFG, R0
        li    R0, #BUSPIN | ((16 + G_TXA) << 8)  ; BIND01: p0 = bus, p1 = G_TXA
        wcfg  BIND01, R0
        li    R0, #0x000F               ; SHCFG: 16 bits, MSB first, input p0
        wcfg  SHCFG, R0
        li    R0, #0x005A               ; TAPCFG: ALT = stuff bit, LFSR step on, run limit 5
        wcfg  TAPCFG, R0
        li    R0, #0
        wcfg  POLY_LO, R0
        wcfg  LFSR_LO, R0
        wcfg  LFSR_HI, R0
        wcfg  LFCFG, R0                 ; left form
        li    R0, #0x4599 << 1
        wcfg  POLY_HI, R0               ; CRC-15 in LFSR[31:17]
        li    S, #0                     ; S is not reset: the first header byte would carry X bits
        tsync
.entry idle
.code
.org ORG_RX
idle:   li    R1, #11                   ; recessive samples until the bus is idle
idl:    @tock : jlo p0, idle            ; dominant: count again
        add   R1, #-1
        jnz   idl
rehunt: sset  #G_IDLE
hunt:   @fall p0 : sclr #G_IDLE         ; SOF edge (it also re-times the timer): bus busy at once, E_TX defers
        wrst                            ; run count restarts
        @tock : smp                     ; SOF: booked for stuffing, not shifted
        jb    rehunt                    ; recessive at the sample point: glitch
        li    R1, #11
        call  rbits                     ; ID
        pushh S
        push  S
        li    R1, #7
        call  rbits                     ; RTR IDE r0 DLC
        push  S
        li    R2, #15
        and   R2, S                     ; DLC
        cmp   R2, #9
        jnc   rtr
        li    R2, #8                    ; DLC 9..15 = 8 bytes
rtr:    li    R0, #0x40
        tst   R0, S
        jz    data
        li    R2, #0                    ; remote frame: no data field
data:   add   R2, #-1
        jnc   crc
        li    R1, #8
        call  rbits
        push  S
        jmp   data
crc:    li    R1, #15
        call  rbits                     ; CRC sequence (and a stuff bit after it)
        rcfg  R0, LFSR_HI
        add   R0, #0
        jnz   crcerr
        @tock : smp                     ; CRC delimiter
        jnb   ferr
        jhi   p1, ok                    ; own frame: no ACK
        @tick : sset #G_DRV             ; ACK slot
        @tick : sclr #G_DRV             ; ACK delimiter
ok:     evt   #EV_OK
        jmp   idle

rbits:  @tock : shin                    ; R1 data bits, stuff bits removed
        jnrun rnext
        @tock : p3 <- q                 ; stuff bit: booked as ~B, p3 has no pad
        jmis  serr                      ; bus = previous bit: stuff error
rnext:  add   R1, #-1
        jnz   rbits
        ret

serr:   evt   #EV_STUFF
        jmp   errf
ferr:   evt   #EV_FORM
        jmp   errf
crcerr: evt   #EV_CRC
        li    R1, #3                    ; error flag after the ACK delimiter
cw:     @tick : nop
        add   R1, #-1
        jnz   cw
errf:   li    R1, #7                    ; active error flag: 6 dominant bits from the next tick
ef:     @tick : sset #G_DRV
        add   R1, #-1
        jnz   ef
        sclr  #G_DRV
        wcfg  LFSR_HI, R1               ; R1 = 0: CRC state cleared
        jmp   idle

; ---------------- transmitter ----------------
.equ ENG_TX, 0
.equ ORG_TX, 224                ; below 256: `li R1, #arbend` stays one word
.engine ENG_TX
.pad uio4, p0, od
.init
        li    R0, #BIT - 1
        mov   P, R0
        li    R0, #BIT - SPTX
        mov   C, R0                     ; tock SPTX clk after each bit start; no auto-sync (TCFG 0)
        li    R0, #BUSPIN | ((16 + G_DRV) << 8)   ; BIND01: p0 = bus, p1 = G_DRV
        wcfg  BIND01, R0
        li    R0, #(16 + G_IDLE) | ((16 + G_TXA) << 8)  ; BIND23: p2 = G_IDLE, p3 = G_TXA
        wcfg  BIND23, R0
        li    R0, #0x000F               ; SHCFG: 16 bits, MSB first, input p0
        wcfg  SHCFG, R0
        li    R0, #0x0052               ; TAPCFG: ALT = stuff bit, run limit 5, no LFSR step
        wcfg  TAPCFG, R0
        li    R0, #0x18ED               ; CC0: tock & MIS, abort
        wcfg  CC0, R0
        li    R0, #mis
        wcfg  WV0, R0
        li    R0, #0x2E21               ; CC1: G_DRV | G_IDLE, call. G_DRV rises only while G_IDLE is low (in a
                                        ; frame), so the watcher is the relay; as guard @c1 it is "bus idle"
        wcfg  CC1, R0
        li    R0, #drv
        wcfg  WV1, R0
        p0 <- 1
        li    S, #0                     ; defined S before the first byte reload
        tsync
        watch #2
.entry tidle
.code
.org ORG_TX
tbits:  @tick : p0 <- out               ; R1 bits from S[15], bytes reloaded from the host, stuff bits added
tsof:   jnrun tnext
        @tick : p0 <- q                 ; stuff bit
tnext:  add   R2, #-1                   ; bits left in this byte
        jnz   tcnt
        cmp   R0, #1                    ; bytes left
        jnc   tret                      ; none: end of the bit stream
        @hrx  : pullh S
        add   R0, #-1
        li    R2, #8
tcnt:   add   R1, #-1
        jnz   tbits
tret:   ret

tidle:  @hrx  : pull R0                 ; NB
        @hrx  : pullh S                 ; B0: SOF ID10 ID9
        li    R2, #3
        li    R1, #14                   ; SOF + ID + RTR, + IDE: a mismatch shows at the tock, after the bit's step
        wrst
tc1:    @c1   : jhi p1, tc1             ; bus idle, and G_DRV low: a flag raised while the relay was off is waited out
        p0 <- out                       ; SOF
        tsync                           ; own bit grid
        sset  #G_TXA
        watch #3
        call  tsof                      ; arbitration field
arbend: call  tbits                     ; R1 = 0: IDE r0 DLC data, up to the last byte
        li    R1, #15
        @tock : nop                     ; E_RX has stepped the LFSR with the last data bit
        rcfg  S, LFSR_HI                ; CRC14 in S[15]
        call  tbits                     ; CRC sequence (and a stuff bit after it)
        watch #2                        ; MIS off: the ACK slot is a wanted mismatch
        @tick : p0 <- 1                 ; CRC delimiter
        @tock : nop
        @tock : jhi p0, ackerr          ; ACK slot
        evt   #EV_SENT
drain:  watch #2                        ; relay on at once: a lost frame's winner gets its ACK during a slow drain
        sclr  #G_TXA
drl:    add   R0, #-1                   ; drop the rest of an aborted frame
        jnc   tidle
        @hrx  : pull R1
        jmp   drl

mis:    watch #0                        ; bus != own bit at the sample point; relay off: one error flag per node
        li    R1, #arbend
        cmp   R1, L
        jnz   berr
        evt   #EV_LOST                  ; arbitration lost: own pin is recessive, E_RX receives the winner
        jmp   drain
berr:   evt   #EV_BITERR
        jmp   terr
ackerr: evt   #EV_ACKERR
terr:   li    R1, #6                    ; active error flag from the next bit
te:     @tick : p0 <- 0
        add   R1, #-1
        jnz   te
        @tick : p0 <- 1
        jmp   drain

drv:    jlo   p1, dret                  ; G_IDLE rose: nothing to drive
        p0 <- 0                         ; E_RX asks for a dominant bit (ACK, error flag)
        watch #2
dw:     jhi   p1, dw
        p0 <- 1
        jhi   p3, drain                 ; own frame hit by an error flag: end it
dret:   ret                             ; idle: back to the interrupted wait
