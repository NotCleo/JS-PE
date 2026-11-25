; i2c_mon.s - passive I2C monitor (monitor_design.md): START, repeated START, STOP, bytes, ACK/NACK, clock stretching
; words: 39 | clk/bit: external | max: 400 kHz (Fm), 1 MHz (Fm+: SCL high 13 / low 25 clk) in simulation (ISS) | engines: 1
; pins (inputs only, no .pad: both pads stay en = 0): p0 = SCL (uio[6]), p1 = SDA (uio[5], shift input)
; host: evt 1 = START, evt 2 = repeated START, evt 3 = STOP; data bytes (MSB first; the first byte after evt 1/2 is
;       address + R/W, a 10-bit address is two bytes); ACK is implied, evt 4 = NACK after the byte before it;
;       evt 5 + lo, hi = clock stretched: an SCL low phase next to an ACK (the one before the ACK clock and the one
;       after it) lasted >= TLOW timer steps of 16 clk; lo, hi = its length in steps (320 ns at 50 MHz). An event
;       before both bytes means they are missing (START/STOP came first). evt 63 = LOST (OVR seen since the previous
;       START/STOP: a byte dropped at a full FIFO or an SCL edge missed), placed before evt 2 / evt 3.
;       A monitor started mid-transfer waits for the next START. The host never sends data to a monitor engine.
; watchers: 0 = START (SDA falls while SCL high) -> start, 1 = STOP (SDA rises while SCL high) -> stop; both abort at
;       any bit position. Bits of an unfinished byte are dropped without an event (the bit an Sr or STOP is set up
;       in looks the same as a first data bit). No timestamps: both condition cells are taken by the watchers.
; stretch: timer free-running in 16-clk steps (TCFG prescale 1); a low phase is measured from the capture after the
;       SCL fall to the capture after the next rise, each up to the guard's arming delay late (+-1 step). Low phases
;       inside a byte are not measured (the micro-loop shifts bits 2..8). Lengths are modulo 65536 steps (21 ms):
;       a longer stretch is reported short or missed.

.equ CLK_HZ, 50000000
.equ RATE,   400000             ; bus rate the stretch threshold is derived from
.equ TLOW,   CLK_HZ / RATE / 16 ; threshold in 16-clk steps: one SCL period (~2 x the nominal low phase)
.equ ENG,    0                  ; engine and load address: override with -D
.equ ORG,    352
.engine ENG
.init
        li    R0, #14 | (13 << 8)       ; BIND01: p0 = uio_in[6] (SCL), p1 = uio_in[5] (SDA)
        wcfg  BIND01, R0
        li    R0, #0x27                 ; SHCFG: 8 bits, MSB first, input p1
        wcfg  SHCFG, R0
        li    R0, #1 << 8               ; TCFG: prescale 16
        wcfg  TCFG, R0
        li    R0, #0xFFFF
        mov   P, R0
        tsync                           ; free-running: low time = capture at the fall - capture at the rise
        li    R0, #0x1809               ; CC0: fall p1 & level p0, abort (START)
        wcfg  CC0, R0
        li    R0, #start
        wcfg  WV0, R0
        li    R0, #0x1805               ; CC1: rise p1 & level p0, abort (STOP)
        wcfg  CC1, R0
        li    R0, #stop
        wcfg  WV1, R0
        li    R2, #TLOW
        li    R0, #0
        mov   C, R0                     ; C = 0: no transfer open (C is a flag here, the tock is unused)
        watch #3
.entry idle
.code
.org ORG
idle:   @never : nop                    ; until a START
start:  jovr  slost                     ; watcher 0
sevt:   tst   C, C
        jz    s1
        evt   #2                        ; repeated START
        jmp   s2
s1:     evt   #1
        mov   C, R2                     ; transfer open (TLOW >= 1)
s2:     wrst
next:   @fall p0 : mov R0, T            ; low phase starts (after START hold, or after the ACK clock)
        @rise p0 : shin                 ; bit 1 (or the setup bit of an Sr / STOP)
        mov   R1, T
        call  chk
        loop { @rise p0 : shin }        ; bits 2..8
        push  S                         ; full FIFO: dropped, OVR
        @fall p0 : mov R0, T
        @rise p0 : mov R1, T            ; ACK clock
        jlo   p1, ack
        evt   #4                        ; NACK
ack:    call  chk
        jmp   next
chk:    sub   R0, R1                    ; low phase in steps (the timer counts down)
        cmp   R0, R2
        jnc   chkr
        evt   #5                        ; stretched
        push  R0
        pushh R0
chkr:   ret
slost:  ovclr
        evt   #63
        jmp   sevt
stop:   jovr  plost                     ; watcher 1
pevt:   evt   #3
        li    R0, #0
        mov   C, R0
        jmp   idle
plost:  ovclr
        evt   #63
        jmp   pevt
