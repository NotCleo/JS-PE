; ps2_host.s - PS/2 host: receive device frames, inhibit, request-to-send and send a command byte with ACK check;
;              parity, framing and timeout errors as events; command sequencing: host/ps2.py (ps2_design.md)
; words: 47 | clk/bit: external (device clock 10-16.7 kHz) | max: device CLK phases >= 14 clk (1.79 MHz, ISS) | engines: 1
; pins: p0 = CLK (uio[6], od; PINS2 = 1: uio[4]), p1 = DATA (uio[5], od, shift input; PINS2 = 1: uio[7]); pull-ups
; host: 0x00 release (the device may send), 0x01 inhibit (CLK held low until the next command), 0x02 + byte: send
;       it -> evt 1 ACK, evt 2 NACK (pulse 11 without ACK) or evt 5 TMO; the lines are released afterwards.
;       Device frames: the byte is pushed, preceded by evt 3 PERR on a parity error; start 1 or stop 0: evt 4 FERR
;       (byte dropped; all 11 bits are read either way); evt 5 TMO: no CLK fall for TBIT inside a frame (byte
;       dropped, lines released)
; timing: device frames are read at the CLK falls (frame start = watcher 1 on a CLK fall, armed in idle). Send:
;       CLK low for (TBIT + 1) x 16 clk (200 us; the inhibit reuses the frame timeout), DATA low TDSU x 16 clk
;       (5 us) before CLK is released, each bit put on DATA 1-5 clk + pad after a device CLK fall, stop = DATA
;       released, ACK read at the 11th fall. Timer prescaler 16, restarted by every CLK fall (TCFG hard sync): watcher 0 (tick) =
;       no fall for TBIT x 16 clk (200 us) inside a frame, or no first device clock TFIRST x 16 clk (15 ms) after
;       the request-to-send
; bound: bit guards armed <= 13 clk after a CLK fall; frame start: first data guard <= 25 clk after the start fall

.equ CLK_HZ, 50000000
.equ MHZ,   CLK_HZ / 1000000
.equ TBIT,  MHZ * 200 / 16      ; inside a frame: CLK fall to CLK fall at most 200 us; also the inhibit (>= 100 us)
.equ TDSU,  MHZ * 5 / 16        ; DATA low before CLK is released: 5 us
.equ TFIRST, CLK_HZ / 1000 * 15 / 16  ; request-to-send to the first device clock: 15 ms
.equ PINS2, 0                   ; 1 = CLK uio[4], DATA uio[7] (second instance on one bus)
.equ H_ACK, 1
.equ H_NACK, 2
.equ H_PERR, 3
.equ H_FERR, 4
.equ H_TMO, 5
.assert (TDSU >= 1) & (TBIT > TDSU) & (TBIT <= 65535) & (TFIRST <= 65535), "timer steps"

.equ ENG,    0                  ; engine and load address: override with -D
.equ ORG,    370
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
        li    R0, #0x38                 ; SHCFG for frames: 9 bits (start + data), LSB first, input p1
        mov   L, R0                     ; kept in L (no call in this program)
        li    R0, #0x04                 ; TAPCFG: odd parity (q = PAR)
        wcfg  TAPCFG, R0
        li    R0, #0x0900               ; TCFG: prescaler 16, hard sync on the CLK fall
        wcfg  TCFG, R0
        li    R0, #TBIT
        mov   P, R0
        li    R0, #TDSU
        mov   C, R0
        li    R0, #0x1A0C               ; CC0: tick, abort (timeout)
        wcfg  CC0, R0
        li    R0, #tmo
        wcfg  WV0, R0
        li    R0, #0x1A08               ; CC1: fall p0, abort (device frame start)
        wcfg  CC1, R0
        li    R0, #rx
        wcfg  WV1, R0
        li    R1, #TFIRST
        p0 <- 1                         ; both released
        p1 <- 1
.entry idle
.code
.org ORG
idle:   p0 <- 1, p1 <- 1                ; release: the device may send
        wcfg  SHCFG, L                  ; 9-bit word for a frame, restarted
        watch #2                        ; frame start watcher
cmd:    @hrx  : watch #0                ; a command: watchers off before it is taken
        pull  R2
        shr   R2, R2                    ; CY = bit 0
        jc    inh                       ; 0x01: inhibit
        jz    idle                      ; 0x00: release
        @hrx  : pull S                  ; 0x02: the byte to send
        li    R2, #0x37
        wcfg  SHCFG, R2                 ; 8 bits, LSB first, input p1
        p0 <- 0                         ; inhibit
        tsync                           ; TBIT; the CLK fall this causes restarts it with the same period
        @tock : p1 <- 0                 ; DATA low (start bit)
        @tick : p0 <- 1                 ; CLK released: request-to-send
        tset  R1                        ; first device clock within TFIRST; each fall then restarts TBIT
        watch #1                        ; timeout watcher
        loop { @fall p0 : p1 <- out }   ; data bits 0..7, each after a device CLK fall
        .deadline 5
        @fall p0 : p1 <- q              ; odd parity
        @fall p0 : p1 <- 1              ; stop: DATA released
        @fall p0 : smp                  ; pulse 11: ACK = DATA low
        .deadline 13
        watch #0
        jb    nack
        @htx  : evt #H_ACK
        jmp   idle
nack:   @htx  : evt #H_NACK
        jmp   idle
inh:    p0 <- 0                         ; CLK held low: the device cannot send
        jmp   cmd

rx:     shin                            ; start bit, read just after the fall that fired watcher 1
        watch #1                        ; timeout watcher on, frame start watcher off
        loop { @fall p0 : shin }        ; data bits 0..7 (9-bit word: start + data)
        .deadline 5
        @fall p0 : smp                  ; parity
        @fall p0 : smp                  ; stop
        .deadline 25, rok
        watch #0
        jnb   ferr
        shr   S, S                      ; CY = start bit, S = data
        jc    ferr
        jpar  rok                       ; PAR = 1 (start value 1, start 0, parity, stop 1): odd parity holds
        @htx  : evt #H_PERR
rok:    @htx  : push S
        jmp   idle
ferr:   @htx  : evt #H_FERR
        jmp   idle
tmo:    p0 <- 1, p1 <- 1
        @htx  : evt #H_TMO
        jmp   idle
