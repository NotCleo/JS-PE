; manchester.s - Manchester (IEEE 802.3) transmitter and receiver. Not designed in.
; words: TX 9, RX 14 (11 with CVCHK = 0) | clk/bit: BIT = 50 (1 Mbit/s) | max: TX 2.2 Mbit/s (BIT >= 22), RX 2.7 Mbit/s (BIT >= 18, D100) | engines: 2
; pins: TX p0 = uo_out[3] (E0); RX p0 = ui_in[4] (E1, shift input)
; code: 1 = low then high, 0 = high then low. Other convention: `inv` on the TX pad, bind invert on RX.
; TX host: bytes, sent MSB first without gaps while data is available (preamble and delimiter are data);
;          the bit grid restarts after idle and the line returns low at the end of the last bit
; RX host: the bytes after the delimiter SFD; event 4 = end of frame
; TX: `out` drives the inverted bit at the bit boundary (SHCFG.inv), the toggle at tock is the mid-bit edge
; RX: clock recovery is the timer's windowed sync: a mid-bit edge starts a one-shot, edges are ignored
;     until tock (3/4 bit: skips the bit-boundary edge), tock samples the first half of the next bit,
;     the next mid-bit edge restarts it. No edge until tick = timeout.
;     CVCHK = 1 (D099): tick 1.25 bits after the last accepted edge. Inside a frame (SFD found) a timeout is
;     the end of frame or a code violation (missing mid-bit transition: the next edge is >= 1.5 bits later)
;     -> watcher 0, event 4, partial byte dropped (from data bit 0 on; a violation inside the SFD is a hunt). While hunting a timeout restarts the hunt silently
;     (watcher 1, to `rehunt`: watch and SHCFG are already set): a first-bit-0 frame locks half a bit off on its bit-boundary edge, times out at its first
;     bit change and relocks on that edge; a burst without SFD gives no event.
;     CVCHK = 0: tick 2 bits after the last accepted edge -> event 4 in both phases; the half-bit-off lock
;     relocks inside the window (D038); a missing transition ends the frame only if the line is then quiet
;     >= 2 bits, else the receiver slips (measured: up to 4 junk bytes, then event 4).
;     Needs >= 2 preamble bits before the delimiter.
; spacing: TX events BIT/2 apart; RX tocks one bit apart, tolerance +-BIT/4 on the edge position (mid-bit
;          edge vs one bit after the previous one; tested to BIT/4 - 2 with +-2 %); CVCHK = 1 catches a missing
;          transition whose next edge is >= BIT/4 + 1 clk past the tolerance (extreme jitter on those bits
;          may slip one edge first); frames >= 3 bits apart; any other edge (noise, the pad-enable edge of an
;          inverted line) >= 2 bits before a frame, so that the timeout clears S first (D053)
; bound: TX next byte armed <= 10 clk after the last mid-bit edge; RX next sample armed <= 10 clk
;        (hunting), <= 14 clk (delimiter found), <= 10 clk (first data bit, CVCHK: its guard follows the
;        zero-slack delimiter path, so `watch` is counted as not prefetched: <= 9)

.equ BIT, 50
.equ CVCHK, 1                   ; code-violation check (D099): 0 = 2-bit deadline, 3 words less
.equ SFD, 0xD5                  ; ...0101 0101 1101 0101 on the wire, MSB first

; ---------------- transmitter ----------------
.equ ENG_TX, 0                  ; engines and load addresses: override with -D
.equ ORG_TX, 192
.engine ENG_TX
.pad uo3, p0
.init
        li    R0, #BIT - 1
        mov   P, R0
        li    R0, #BIT - BIT / 2
        mov   C, R0                     ; tock at mid-bit
        li    R0, #0x87                 ; SHCFG: 8 bits, MSB first, inverted data
        wcfg  SHCFG, R0
        p0 <- 0
        tsync
.entry idle
.code
.org ORG_TX
idle:   @tick : p0 <- 0                 ; end of the last bit: line back to idle low
        @hrx  : nop                     ; wait for the host
        tsync                           ; fresh bit grid
mtx:    pull  S
        loop {
          @tick : p0 <- out             ; first half: inverted bit
          @tock : p0 <- t               ; mid-bit transition
        }
        @tock : p0 <- t                 ; mid-bit transition of the eighth bit
        .assume armed BIT / 2 - 1       ; BIT/2 after the loop's last tick, armed 1 clk after it (prefetched)
        .deadline 10, mtx
        jrx   mtx                       ; more data: no gap
        jmp   idle

; ---------------- receiver ----------------
.equ ENG_RX, 1
.equ ORG_RX, 208
.engine ENG_RX
.init
.if CVCHK
        li    R0, #BIT + BIT / 4
        mov   P, R0                     ; one-shot length 1.25 bits: deadline
        li    R0, #BIT + BIT / 4 + 1 - (3 * BIT) / 4
.else
        li    R0, #2 * BIT - 1
        mov   P, R0                     ; one-shot length 2 bits: end-of-frame deadline
        li    R0, #2 * BIT - (3 * BIT) / 4
.endif
        mov   C, R0                     ; tock 3/4 bit after the mid-bit edge
        li    R0, #0x4C00               ; TCFG: windowed sync on both edges of p0
        wcfg  TCFG, R0
        li    R0, #4                    ; BIND01: p0 = ui_in[4]
        wcfg  BIND01, R0
        li    R1, #0x0087               ; SHCFG while hunting: 8 bits, MSB first, inverted
        li    R2, #0x0287               ; SHCFG for data: + auto-push
        li    R0, #0x1A0C               ; CC0: tick, abort (end of frame)
        wcfg  CC0, R0
        li    R0, #eof
        wcfg  WV0, R0
.if CVCHK
        li    R0, #0x1A0C               ; CC1: tick, abort (timeout while hunting)
        wcfg  CC1, R0
        li    R0, #rehunt
        wcfg  WV1, R0
.else
        watch #1
.endif
.entry hunt
.code
.org ORG_RX
hunt:
.if CVCHK
        watch #2                        ; hunting: a timeout restarts the hunt, no event
.endif
        wcfg  SHCFG, R1                 ; no auto-push while hunting
rehunt: li    S, #0                     ; no stale bits: they can complete a false SFD (D053)
find:   @tock : shin                    ; one bit per mid-bit edge
        .assume armed (73 * BIT) / 100 - 9  ; tocks >= 0.98 BIT - (BIT/4 - 2) apart, armed <= 10 clk after: BIT >= 18
        .deadline 10, find
        .deadline 14
        cmp   S, #SFD                   ; last eight bits = start delimiter?
        jnz   find
        wcfg  SHCFG, R2                 ; byte boundary is here; also resets the bit counter
.if CVCHK
        @tock : shin                    ; first data bit, then: in a frame a timeout (end, violation) is event 4
        .deadline 10
        watch #1
.endif
data:   loop { @tock : shin }           ; completed bytes go to the host without leaving the loop
        push  S                         ; host FIFO was full: byte dropped, OVR reports it
        jmp   data
eof:    evt   #4
        jmp   hunt
