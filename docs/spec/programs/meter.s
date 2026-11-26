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
; min phases: EXT = 0: high >= 1 clk, low >= 22 clk; EXT = 1: high >= 10 clk, low >= 14 clk. A shorter phase is
;       captured late and reads the time since its edge: a reading below the minimum means "shorter, length unknown"
;       (the next phase stays exact: the timer restarted at the true edge). Pulses < 1 clk may vanish (synchronizer).
; EXT = 1: an edge 0-13 clk after a wrap is captured after the wrap call: -12 .. +13.5 clk; host flags readings with
;       wraps >= 1 and 0x10000 - T <= 14.
; rate: 4 bytes per period: <= 78 k periods/s at B = 16, 312 k with the PIO link (B = 4); a full FIFO drops bytes
;       and sets OVR (host notice), never a late capture.

.equ PRE,    0                  ; timer prescale 0, 1, 2 = /1, /16, /256 (EXT = 0 only)
.equ EXT,    0                  ; 1 = count wraps: phases of any length at 1 clk
.equ MPIN,   0                  ; pad of the measured signal
.equ ENG,    0                  ; engine and load address: override with -D
.equ ORG,    16
.engine ENG
.init
        li    R0, #0xFFFF
        mov   P, R0
        mov   C, R0                     ; swin: restart only while T < C, i.e. after the first step
        li    R0, #MPIN
        wcfg  BIND01, R0
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
