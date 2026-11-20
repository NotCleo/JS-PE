; i2c_ctrl.s - I2C controller: START, repeated START, STOP, byte write/read, ACK/NACK, clock stretching with timeout
; words: 41 (40 with THD 0) | clk/bit: 128+ with the 400 kHz constants at rise 0 (THD + fetch + TLOW + SCL rise + THIGH; measured, D096) | max: 0.83 MHz measured (TLOW 20, THIGH 16, THD 0, 120 ns rise: 60 clk/bit since D081, D096, Fm+ tLOW/tHIGH met) | engines: 1
; pins: p0 = SCL (uio[6], open-drain), p1 = SDA (uio[5], open-drain, shift input)
; host: two bytes per step: flags, data.  flags bit0 START before, bit1 transfer a byte, bit2 ninth bit to
;       drive (1 = release: every write, and NACK after a read; 0 = ACK after a read), bit3 STOP after.
;       data = byte to write, 0xFF to read (ignored without bit1).
;       Per transferred byte two bytes come back, as sampled on the bus: {d6..d0, ack}, {0000000, d7}.
;       Event 3 = SCL held low past the deadline; the bus is released and the step abandoned.
;       Not checked: arbitration (single controller in v1, D041); the host can compare the returned byte
;       of a write with the byte it sent. SDA stuck low: one step flags 0x06, data 0xFF clocks nine bits
;       with SDA released, then a STOP step.
; timing: each phase is a one-shot `tset (DEADLINE + length - 1)`; tock ends the phase, tick (DEADLINE
;         clk later) means the phase never ended and goes to watcher 1. One 9-bit word per byte: the
;         ninth bit is the ACK slot, so write, read+ACK and read+NACK are the same loop.
; hold: inside a byte SDA changes THD + 3..6 clk after SCL is driven low (15-18 clk = 300-360 ns with
;       THD 12; UM10204 asks for >= 300 ns to bridge the SCL fall, D042). THD 0 removes the hold word:
;       SDA then changes 3-6 clk after SCL. THD is 0 or >= 6.
; 400 kHz (measured since D081, rise 0): tLOW >= 92 clk (1.84 us), tHIGH >= 36 clk, tSU;STA = tSU;STO = 36,
;          tHD;STA = 33 clk (0.66 us), tBUF >= 72 clk, tSU;DAT >= 75 clk, SCL period >= 128 clk (<= 390 kHz)
; 100 kHz: TLOW = 242, THIGH = 240 (period >= 504 clk)
; slow buses: the high phase is timed from the clock in which SCL reads high (`@c0`), so the rise is
;          already absorbed and THIGH stays 32. THIGH 40 is the option for a rise near 300 ns seen
;          through a threshold above VDD/2: +160 ns on tHIGH, tSU;STA, tHD;STA, tSU;STO (<= 373 kHz)
; spacing: phases >= 16 clk; bound: every guard armed <= 14 clk after the previous event

.equ TLOW,     72
.equ THIGH,    32
.equ THD,      12               ; SDA hold after SCL low, clk; 0 = none
.equ DEADLINE, 50000            ; 1 ms stretch timeout
.assert (THD == 0) | (THD >= 6), "THD is 0 or >= 6"

.equ ENG,    0                  ; engine and load address: override with -D
.equ ORG,    96
.engine ENG
.pad uio6, p0, od
.pad uio5, p1, od
.init
        li    R0, #14 | (13 << 8)       ; BIND01: p0 = uio_in[6], p1 = uio_in[5]
        wcfg  BIND01, R0
        li    R0, #0x28                 ; SHCFG: 9-bit words, MSB first, input p1
        wcfg  SHCFG, R0
        li    R0, #0x0A00               ; CC0: level p0 (guard c0 = SCL reads high)
        wcfg  CC0, R0
        li    R0, #0x1A0C               ; CC1: tick, abort (deadline)
        wcfg  CC1, R0
        li    R0, #tmo
        wcfg  WV1, R0
        li    R0, #DEADLINE
        mov   C, R0
.if THD
        li    R0, #DEADLINE + THD - 1
        mov   P, R0                     ; hold phase length (P is free: no periodic timer here)
.endif
        li    R1, #DEADLINE + TLOW - 1
        li    R2, #DEADLINE + THIGH - 1
        p0 <- 1, p1 <- 1                ; bus released
        watch #2
.entry next
.code
.org ORG
next:   tstop                           ; no phase is running while the host is awaited
        @hrx  : pull R0                 ; flags
        @hrx  : pull S                  ; data
        shr   R0, R0                    ; CY <- START?
        jnc   nost
        p1 <- 1                         ; SDA high (SCL is low after a byte, or the bus is idle)
        call  clk_hi                    ; SCL high; tSU;STA running
        @tock : p1 <- 0                 ; START: SDA falls while SCL is high
        tset  R2                        ; tHD;STA
        @tock : p0 <- 0                 ; SCL low
nost:   shr   R0, R0                    ; CY <- byte?
        jnc   skip
        shr   R0, R0                    ; CY <- ninth bit
        rlc   S, S                      ; S = {data, ninth}
.if THD
bit:    tset  P                         ; hold: SCL has just been driven low
        @tock : p1 <- d                 ; SDA <- next bit (0 drives low, 1 releases)
.else
bit:    p1 <- d                         ; SDA <- next bit (0 drives low, 1 releases); SCL is low
.endif
        call  clk_hi                    ; low phase, release SCL, wait until it reads high
        shin                            ; sample SDA
        @tock : p0 <- 0                 ; high phase over: SCL low
        jw    bit
        tstop                           ; no deadline while the host link is busy (SCL is low)
        @htx  : push S                  ; {d6..d0, ack}
        @htx  : pushh S                 ; {0, d7}
        jmp   stopq
skip:   shr   R0, R0                    ; drop the ninth-bit flag
stopq:  shr   R0, R0                    ; CY <- STOP?
        jnc   next
        p1 <- 0                         ; SDA low while SCL is low
        call  clk_hi                    ; SCL high; tSU;STO running
        @tock : p1 <- 1                 ; STOP: SDA rises while SCL is high
        tset  R1                        ; bus free time
        @tock : nop
        jmp   next

clk_hi: tset  R1                        ; low phase (data setup)
        @tock : p0 <- 1                 ; release SCL
        @c0   : tset R2                 ; SCL reads high (a stretching target delays this): high phase
        ret

tmo:    p0 <- 1, p1 <- 1                ; deadline passed: release the bus
        evt   #3
        wrst
        jmp   next
