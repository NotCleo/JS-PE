; nec.s - NEC IR receiver: leader, 32-bit frame, repeat code (measure_design.md)
; words: 30 | engines: 1 | timer prescale 256 at 50 MHz (5.12 us steps)
; pins: p0 = IR receiver module output (ui_in[NPIN], active low, 38 kHz demodulated; inverted by BIND: mark = 1)
; host: per frame 4 bytes as received (LSB first): address, ~address, command, ~command (host checks the
;       complements; extended NEC: 16-bit address). evt 1 = repeat code (key held). evt 2 = frame broken off (a mark
;       end missing for 3 ms inside the 32 bits): the partial frame is dropped; evt 2 right after 4 bytes = the stop
;       mark end was missing, the 32 bits are complete.
; timing (nominal 562.5 us unit): leader mark >= 6.75 ms (9 ms nominal; no upper limit); leader space < 1.69 ms:
;       ignored, < 3.375 ms: repeat (2.25 ms), else frame (4.5 ms). Bit = space after a mark, sampled 0.84 ms after
;       the mark end: next mark already on = 0 (0.5625 ms space), still space = 1 (1.6875 ms). Margins: a 0 needs
;       space <= 0.84 ms < space + next mark, a 1 needs space > 0.84 ms (tested with +-10 % random jitter per edge,
;       and +-20 % uniform timing).
; bound (T6, structured IR stimulus, 4 slot maps x 4 phases): measured = bound on every step, max 30 clk (space end
;       to the bit loop); no .deadline needed: the shortest gap between events is a 562.5 us mark.

.equ NPIN,   0                  ; pad of the IR receiver output
.equ CLK_HZ, 50000000
.equ PRE,    2                  ; timer prescale 0, 1, 2 = /1, /16, /256 (tests at a low CLK_HZ use 1)
.equ STEP,   (1 << (4 * PRE)) * (1000000000 / CLK_HZ)  ; ns per timer step
.equ LMIN,   6750000 / STEP     ; leader mark minimum (steps)
.equ SPMAX,  3375000 / STEP     ; leader space: frame if longer
.equ RPMIN2, 1690000 / STEP / 2 ; leader space: repeat if longer (halved for an 8-bit compare)
.equ BITTO,  3000000 / STEP     ; mark end to mark end timeout inside a frame
.equ SAMPLE, 840000 / STEP      ; bit sample point after a mark end
.equ ENG,    0                  ; engine and load address: override with -D
.equ ORG,    16
.engine ENG
.init
        li    R0, #NPIN | (1 << 5)      ; p0 = inverted pad: mark = 1
        wcfg  BIND01, R0
        li    R0, #0x009F               ; SHCFG: 16 bits, LSB first, input p0, inverted (space at the sample = 1)
        wcfg  SHCFG, R0
        li    R0, #PRE << 8             ; TCFG: prescale
        wcfg  TCFG, R0
        li    R0, #0x1A0C               ; CC0: tick, abort (enabled inside a frame only)
        wcfg  CC0, R0
        li    R0, #tout
        wcfg  WV0, R0
        li    R0, #BITTO - SAMPLE
        mov   C, R0                     ; tock SAMPLE steps after tset BITTO
        li    R0, #SPMAX
        mov   P, R0
        li    R2, #BITTO
        li    R0, #LMIN
.entry start
.code
.org ORG
start:  watch #0                        ; a re-RUN after a STOP mid-frame: watcher 0 off (wen survives STOP)
idle:   @rise p0 : tset R0              ; mark start: one-shot, T = 0 after LMIN steps
        @fall p0 : mov R1, T            ; mark end
        tset  P                         ; space timer: T = 0 after SPMAX steps
        cmp   R1, #0
        jnz   idle                      ; mark shorter than LMIN: not a leader
        @rise p0 : mov R1, T            ; space end
        cmp   R1, #0
        jz    frame                     ; space >= SPMAX
        shr   R1, R1
        cmp   R1, #SPMAX / 2 - RPMIN2   ; CY = T / 2 >= (SPMAX - RPMIN) / 2: space too short
        jc    idle
        evt   #1                        ; repeat code
        jmp   idle
frame:  tset  R2                        ; bit 0 mark started: timeout armed
        li    R1, #2                    ; two 16-bit words
        watch #1
bit:    @fall p0 : tset R2              ; mark end: sample timer, timeout
        @tock : shin
        jw    bit
        push  S
        pushh S
        add   R1, #-1
        jnz   bit
        @fall p0 : watch #0             ; stop mark end
        jmp   idle
tout:   watch #0                        ; watcher 0, tick: no mark end for BITTO steps
        wrst
        evt   #2
        jmp   idle
