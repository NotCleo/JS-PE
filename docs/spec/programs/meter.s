; meter.s - pulse meter: low and high time of every period on one pin (measure_design.md)
; words: 7 (EXT = 1: 9) | engines: 1
; pins: p0 = input (ui_in[MPIN]; MPIN 8-15 = uio_in[0..7]), never driven
; method: TCFG sedge = both edges of p0: every edge restarts the timer in hardware, so each capture reads the phase
;         that just ended: steps = 0x10000 - T. EXT = 0: one-shot (swin), T = 0 = over range (>= 65536 steps).
; host: per period 4 bytes: low phase lo, hi, then high phase lo, hi (the one before it). The first record is
;       meaningless (timer not started yet). EXT = 1: evt 1 = one timer wrap (65536 clk) inside the phase whose
;       record follows it: clk = 65536 * wraps + 0x10000 - T.
; accuracy (ISS, 4 slot phases): +-1 clk per phase (input sampling); consecutive phases share their edge sample, so
;       a sum of phases is +-1 clk too (period, or a host average over any time). PRE > 0: host takes the step centre,
;       (steps - 0.5) * 16^PRE, +-(step / 2 + 1 clk). Range per phase 65 535 steps: 1.31 ms / 21 ms / 335 ms.
; min phases: EXT = 0: high >= 1 clk, low >= 22 clk; EXT = 1: high >= 10 clk, low >= 14 clk. Why: the fall guard is
;       prefetched and armed 1 clk after the rise capture, but after the fall capture the 4 pushes + jmp (~4 clk each)
;       run before the rise guard is armed again (EXT splits them: 2 pushes after each capture). Short low pulses:
;       INV = 1 (BIND invert) swaps the roles: low >= 1 clk, high >= 22 clk; records are then high, low. A shorter phase is
;       captured late; readings <= the minimum (low <= 22, EXT low <= 14 / high <= 10) mean "possibly shorter, length
;       unknown": a rise in the first clock after a fall does not restart the timer (swin: T = C), so a 1-clk low reads
;       21-22 clk; an unflagged low reading needs a low >= 24 clk. Pulses < 1 clk may vanish (synchronizer). PRE > 0: the same window lasts one step, so a phase of
;       <= 1 step reads 1 step and the next phase includes it (host flags both).
; EXT = 1: an edge 0-13 clk after a wrap is captured after the wrap call: -12 .. +13.5 clk; host flags readings with
;       wraps >= 1 and 0x10000 - T <= 14.
; rate: one record (4 bytes) per period, pushed at the logical fall; the 4-entry TX FIFO holds one record, so
;       consecutive logical falls must be at least one record's link time apart: 40 B clk (640 at B = 16, 160 with
;       the PIO link, B = 4), up to 100 B with escapes (a data byte 0x1B is sent as ESC 0x1B) and a stream change
;       (ESC e), plus the same again for each other engine streaming on the link (HOSTIF 4: up to 4 entries per turn).
;       On average <= 78 k / 312 k periods/s. Else bytes are dropped and OVR is set (host notice ESC 0x80|e), never a
;       late capture.

.equ PRE,    0                  ; timer prescale 0, 1, 2 = /1, /16, /256 (EXT = 0 only)
.equ EXT,    0                  ; 1 = count wraps: phases of any length at 1 clk
.equ MPIN,   0                  ; pad of the measured signal
.equ INV,    0                  ; 1 = measure the inverted pin (short low pulses)
.equ ENG,    0                  ; engine and load address: override with -D
.equ ORG,    16
.engine ENG
.init
        li    R0, #0xFFFF
        mov   P, R0
        mov   C, R0                     ; swin: restart only while T < C, i.e. after the first step
        li    R0, #MPIN | (INV << 5)
        wcfg  BIND01, R0
        li    R0, #0
        tset  R0                        ; T is not reset (ARCH 11): defined 0, stopped, before the first capture
.if EXT
        li    R0, #0x0C00               ; TCFG: sync on both edges, periodic, /1
        wcfg  TCFG, R0
        li    R0, #0x2A0C               ; CC0: tick, call
        wcfg  CC0, R0
        li    R0, #wrap
        wcfg  WV0, R0
        watch #1
.else
        li    R0, #0x4C00 | (PRE << 8)  ; TCFG: one-shot window sync on both edges, prescale
        wcfg  TCFG, R0
.endif
.entry m
.code
.org ORG
.if EXT
m:      @rise p0 : mov R1, T            ; low phase ended
        push  R1                        ; out before the next wrap can start counting for the high phase
        pushh R1
        @fall p0 : mov R2, T            ; high phase ended
        push  R2
        pushh R2
        jmp   m
wrap:   evt   #1                        ; watcher 0, tick: call, the preempted instruction repeats
        ret
.else
m:      @rise p0 : mov R1, T            ; low phase ended
        @fall p0 : mov R2, T            ; high phase ended
        push  R1                        ; full FIFO: dropped, OVR (never blocks: a late capture would be wrong)
        pushh R1
        push  R2
        pushh R2
        jmp   m
.endif
