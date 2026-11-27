; ps2_dev.s - PS/2 device (keyboard / mouse side): frames to the host with the device clock, host inhibit abort,
;             request-to-send receive with ACK; scan codes, commands and packets: host/ps2.py (ps2_design.md)
; words: 70 | clk/bit: 2 HALF (4000 as written: 12.5 kHz; 10-16.7 kHz = HALF 2500-1500) | max: HALF 28 (893 kHz, ISS) | engines: 1
; pins: p0 = CLK (uio[6], od; PINS2 = 1: uio[4]), p1 = DATA (uio[5], od, shift input; PINS2 = 1: uio[7]); pull-ups
; host: each byte = one frame (start 0, 8 data LSB first, odd parity, stop 1); keep one byte in flight: the chip
;       answers evt 1 SENT, or evt 2 ABORT (host inhibit before the 11th fall: not delivered, send it again).
;       Received bytes (host commands) are pushed, preceded by evt 6 DROP if a byte was waiting for the bus (it is
;       dropped: the command response goes first) and evt 3 PERR on a parity error (ACK given); evt 4 FERR (stop
;       bit 0: no ACK, byte dropped, the clock runs on until DATA is released, no limit); evt 5 RABORT (host
;       inhibit during the receive, byte dropped). A host byte arriving in the ~5 clk between the DROP check and
;       the push is not dropped: it goes out before the response
; timing: timer periodic HALF: tick = CLK edge, tock = middle of the phase. Device -> host: DATA changes at the
;       tock of CLK high (HALF / 2 from both edges); before every bit CLK is read at that tock, and once more at
;       the 11th fall: low = inhibit -> abort. A frame starts only after CLK and DATA were high at both ends of IDLE clk (50 us;
;       the window opens on the CLK level; a host inhibits >= 100 us, so a look at both ends is enough).
;       Host -> device: request-to-send = CLK rises with DATA low (watcher 0 in idle, and a level check on entering
;       idle); 11 clock pulses start HALF later, DATA is sampled at the tock of CLK high, ACK driven from the tock
;       after the stop bit to the tock of pulse 11
; no timeout: the device makes the clock; a host that holds CLK low only delays the next frame
; bound: every guard armed <= 13 clk after the previous tick/tock (HALF / 2 >= 14)

.equ CLK_HZ, 50000000
.equ HALF,  2000                ; CLK half period, clk
.equ IDLE,  CLK_HZ / 20000      ; bus idle before a frame: 50 us
.equ PINS2, 0                   ; 1 = CLK uio[4], DATA uio[7] (second instance on one bus)
.equ E_SENT, 1
.equ E_ABORT, 2
.equ E_PERR, 3
.equ E_FERR, 4
.equ E_RABORT, 5
.equ E_DROP, 6
.assert (HALF >= 28) & (HALF <= 65536) & (IDLE >= 1) & (IDLE <= 65535), "HALF 28..65536, IDLE 1..65535"

.equ ENG,    0                  ; engine and load address: override with -D
.equ ORG,    300
.engine ENG
.if PINS2
.equ CLKP,  4
.equ DATP,  7
.pad uio4, p0, od
.pad uio7, p1, od
.else
.equ CLKP,  6
.equ DATP,  5
.pad uio6, p0, od
.pad uio5, p1, od
.endif
.init
        li    R0, #(8 + CLKP) | ((8 + DATP) << 8)  ; BIND01: p0 = CLK, p1 = DATA
        wcfg  BIND01, R0
        li    R0, #0x37                 ; SHCFG: 8 bits, LSB first, input p1
        wcfg  SHCFG, R0
        li    R0, #0x04                 ; TAPCFG: odd parity (q = PAR)
        wcfg  TAPCFG, R0
        li    R0, #HALF - 1
        mov   P, R0
        li    R0, #HALF - HALF / 2
        mov   C, R0                     ; tock HALF / 2 after each tick
        li    R0, #0x1214               ; CC0: rise p0 & ~level p1, abort (request-to-send)
        wcfg  CC0, R0
        li    R0, #rx
        wcfg  WV0, R0
        li    R0, #0x08F0               ; CC1: level p0 & hrx (guard: CLK high and a byte to send)
        wcfg  CC1, R0
        li    R0, #IDLE
        p0 <- 1                         ; both released
        p1 <- 1
.entry idle
.code
.org ORG
idle:   p0 <- 1, p1 <- 1                ; release CLK and DATA
        watch #1                        ; request-to-send watcher
        jlo   p0, iw                    ; CLK low: inhibited
        jlo   p1, rx                    ; CLK high, DATA low: request-to-send already pending
iw:     @c1   : tset R0                 ; CLK high and a byte to send: the bus must stay idle IDLE clk
        jlo   p1, rx
        @tick : jlo p0, iw              ; CLK low meanwhile: look again
        jlo   p1, rx
        watch #0
        pull  S
        wrst
        tsync                           ; tock = middle of CLK high, tick = CLK edge
        .deadline 5
        @tock : jlo p0, tab             ; CLK low since the idle check: inhibit
        p1 <- 0                         ; start bit
        @tick : p0 <- 0
        @tick : p0 <- 1
tbit:   @tock : jlo p0, tab             ; CLK held low by the host: inhibit
        .deadline 9
        p1 <- out                       ; data bits 0..7
        @tick : p0 <- 0
        @tick : p0 <- 1
        .deadline 9, tbit
        jw    tbit
        @tock : jlo p0, tab
        p1 <- q                         ; odd parity
        @tick : p0 <- 0
        @tick : p0 <- 1
        @tock : jlo p0, tab
        p1 <- 1                         ; stop
        @tick : jlo p0, tab             ; inhibit after the last check, before the 11th fall
        p0 <- 0
        @tick : p0 <- 1
        @htx  : evt #E_SENT
        jmp   idle
tab:    p1 <- 1
        @htx  : evt #E_ABORT
        jmp   idle

rx:     watch #0
        wrst
        tsync
        .deadline 5
rbit:   @tick : p0 <- 0                 ; the host changes DATA while CLK is low
        @tick : p0 <- 1
        @tock : shin                    ; data bits 0..7
        .deadline 13
        jlo   p0, rab
        jw    rbit
        @tick : p0 <- 0
        @tick : p0 <- 1
        @tock : smp                     ; parity: PAR = 0 when odd parity holds
        jlo   p0, rab
        @tick : p0 <- 0
        @tick : p0 <- 1
        @tock : jlo p0, rab
        .deadline 13
        jlo   p1, rfe                   ; stop bit 0: framing error, no ACK
        p1 <- 0                         ; ACK
        @tick : p0 <- 0                 ; pulse 11
        @tick : p0 <- 1
        @tock : p1 <- 1                 ; DATA released
        jnrx  rnd                       ; checked before the push: the host cannot have answered yet
        pull  R1                        ; a byte waiting for the bus: dropped, the response goes first
        @htx  : evt #E_DROP
rnd:    jnpar rok
        @htx  : evt #E_PERR
rok:    @htx  : push S
        jmp   idle
rfe:    @tick : p0 <- 0                 ; clock on until the host releases DATA
        @tick : p0 <- 1
        @tock : jlo p0, rab
        jlo   p1, rfe
        @htx  : evt #E_FERR
        jmp   idle
rab:    @htx  : evt #E_RABORT
        jmp   idle
