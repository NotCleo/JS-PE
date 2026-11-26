; freq.s - frequency counter: rising edges of one pin per gate window, windows back to back (measure_design.md)
; words: 17 | engines: 1
; pins: p0 = input (ui_in[FPIN]; FPIN 8-15 = uio_in[0..7]), never driven; p1 = constant 1 (shifter input)
; window: tock to tick of a one-shot timer = GATE * 16^PRE clk exactly; defaults 1 ms (PRE 0), 10 ms (PRE 1,
;         GATE 31250), 99.9987 ms (PRE 2, GATE 19531). Gap between windows: handler (waits for FIFO room) + K + 1 steps (dead, not counted)
; host: per window 4 bytes: S lo, hi, R0 lo, hi; edges = popcount(S) + 16 * R0 (< 2^20: 100 ms only to 10.4 MHz).
;       evt 2 before them: OVR in that window (two rises merged: edges lost). The host drops the first record after a
;       RUN: it may carry a stale OVR or a stale count from before the RUN.
; accuracy (ISS, 4 slot phases): window exact; count within -1 .. +1 edge of window / period (a rise latched in the
;       pass overhead when the tick aborts is not counted)
; limits (ISS): period >= 7 clk (<= 7.1 MHz), high >= 1 clk and low >= 1 clk (20 ns, 2-FF synchronizer); the
;       16-rise loop is left once per pass for 3 instructions (~12 clk), one rise is latched meanwhile.
;       Beyond: period 6-7 clk may lose 1 edge unreported, shorter periods lose edges with evt 2 in every window;
;       pulses < 1 clk vanish and inputs > 25 MHz alias, both SILENTLY: the count is then simply wrong

.equ PRE,    0                  ; gate timer prescale 0, 1, 2 = /1, /16, /256
.equ GATE,   50000              ; window in timer steps (<= 65535 - K)
.equ K,      (PRE == 0) * 7 + 1  ; tset to tock - 1, in steps: >= 8 clk, so @tock waits >= 1 clk (D055)
.equ FPIN,   0                  ; pad of the measured signal
.equ ENG,    0                  ; engine and load address: override with -D
.equ ORG,    16
.engine ENG
.init
        li    R0, #FPIN | (25 << 8)     ; p1 = constant 1
        wcfg  BIND01, R0
        li    R0, #0x002F               ; SHCFG: 16-bit words, input p1: every counted rise shifts in a 1
        wcfg  SHCFG, R0
        li    R0, #PRE << 8
        wcfg  TCFG, R0
        li    R0, #0x1A0C               ; CC0: tick, abort
        wcfg  CC0, R0
        li    R0, #gate
        wcfg  WV0, R0
        li    R0, #GATE + K
        mov   P, R0                     ; one-shot start value: tock K + 1 steps later, tick GATE steps after the tock
        li    R0, #GATE
        mov   C, R0
        li    R2, #0                    ; constant 0 for adc
        li    R0, #0
        watch #1
.entry clr
.code
.org ORG
gate:   adc   R0, R2                    ; watcher 0, tick: window over. CY = 1 only if the tick cut add S / adc R0:
        jnovr g1                        ;   count that pass here (else 16 edges lost unreported, reviewer D224)
        ovclr
        @htx : evt #2                   ; two rises merged in this window
g1:     @htx : push S                   ; blocks: the next window starts after the record is out (dead time)
        @htx : pushh S
        @htx : push R0
        @htx : pushh R0
clr:    li    S, #0
        wrst                            ; the aborted word restarts
        li    R0, #0
        tset  P
        .deadline 7
        @tock : nop                     ; window start; its waiting clocks drop the rises latched in the handler
cnt:    loop { @rise p0 : shin }        ; 16 rises
        add   S, #1                     ; S = 0xFFFF: clears S, CY = 1
        adc   R0, R2                    ; count the pass
        jmp   cnt
