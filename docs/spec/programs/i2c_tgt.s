; i2c_tgt.s - I2C target: 7-bit address, receive with ACK, transmit until NACK, clock stretching
; words: 32 | clk/bit: external (125 at 400 kHz); SCL hold 3-4 clk + pad after SCL falls, own SDA edges THD + 3..4 clk after it | max: 400 kHz (phases >= 30 clk against the 14-clk bound); 1 MHz needs THD 5 and is marginal (13-clk high phase) | engines: 1
; pins: p0 = SCL (uio[6], open-drain), p1 = SDA (uio[5], open-drain, shift input)
; host: every addressed transfer starts with the address byte {addr, R/W}. Write: each received byte
;       follows; SCL is stretched while the host FIFO is full. Read: the host supplies each byte to
;       send; SCL is stretched until it arrives; a NACK from the controller ends the transfer.
; watchers: 0 = START (SDA falls while SCL high) -> start, 1 = STOP (SDA rises while SCL high) -> idle.
;       Both fire at any bit position; a partial byte is dropped.
; hold: every SCL fall starts a timer one-shot of THD clk (windowed sync, tock never reached), and every
;       own SDA change waits for its tick: THD + 3..4 clk after the fall at the pad (320-340 ns with
;       THD 13; UM10204 asks a transmitter for >= 300 ns to bridge the SCL fall, D049). SDA therefore
;       changes only while SCL is low and the engine never triggers its own watchers. SCL itself is
;       held at the pad edge after the fall is seen (fused, 3-4 clk + pad, D081).
; setup: SCL is released TSU + 2..5 clk (>= 440 ns with TSU 20) after the first bit of a read byte is put on SDA
;        and TSU + 3..6 clk after the ACK (one-shot started after the host push: a waiting @htx would clear a
;        latched tick, D097)
; no timeout: if the controller stops mid-read while a 0 is driven, SDA stays low until the host sends RST
; spacing: SCL high and low phases >= 30 clk at 400 kHz (>= 13 clk at 1 MHz); low phase > THD + 3 clk
; bound: every guard armed <= 14 clk after the previous SCL edge

.equ ADDR, 0x42
.equ THD,  13                   ; own SDA changes THD + 3..4 clk after SCL falls (320-340 ns)
.equ TSU,  20                   ; SCL is released TSU + 2..5 clk after SDA changes: 440 ns >= 300 ns rise
                                ; + 100 ns tSU;DAT (Fm). Standard mode with a 1 us rise: 60 (D043)
.assert (THD >= 1) & (THD <= 255) & (TSU <= 255), "THD and TSU must stay below the compare value 255"
.assert TSU >= 13, "ACK / data setup TSU + 2 clk >= 300 ns (Sm tSU;DAT 250 ns + margin, D097)"

.equ ENG,    0                  ; engine and load address: override with -D
.equ ORG,    144
.engine ENG
.pad uio6, p0, od
.pad uio5, p1, od
.init
        li    R0, #14 | (13 << 8)       ; BIND01: p0 = uio_in[6], p1 = uio_in[5]
        wcfg  BIND01, R0
        li    R0, #0x27                 ; SHCFG: 8 bits, MSB first, input p1
        wcfg  SHCFG, R0
        li    R0, #0x4800               ; TCFG: windowed sync on the falling edge of p0 (SCL)
        wcfg  TCFG, R0
        li    R0, #THD - 1
        mov   P, R0                     ; one-shot length: tick THD clk after the fall is seen
        li    R0, #255
        mov   C, R0                     ; T < C always: every SCL fall restarts the one-shot, no tock
        li    R0, #0x1809               ; CC0: fall p1 & level p0, abort (START)
        wcfg  CC0, R0
        li    R0, #start
        wcfg  WV0, R0
        li    R0, #0x1805               ; CC1: rise p1 & level p0, abort (STOP)
        wcfg  CC1, R0
        li    R0, #idle
        wcfg  WV1, R0
        li    R0, #ADDR                 ; own address (the host may inject another value)
        li    R2, #TSU - 1
        watch #3
.entry idle
.code
.org ORG
idle:   p1 <- 1, p0 <- 1                ; release SDA and SCL
        @never : nop                    ; until a START
start:  p1 <- 1, p0 <- 1                ; a repeated START may arrive while we hold a line
        wrst
        loop { @rise p0 : shin }        ; address + R/W
        shr   R1, S                     ; R1 <- address
        cmp   R1, R0
        jnz   idle                      ; not ours
        @fall p0 : p0 <- 0              ; eighth clock over: hold SCL
        @tick : p1 <- 0                 ; THD later: ACK
        @htx  : push S                  ; address byte to the host
        tset  R2                        ; ACK setup
        jb    rd                        ; last bit shifted in = R/W
; receive
wr:     @tick : p0 <- 1                 ; release SCL: the controller reads the ACK
        @tick : p1 <- 1                 ; THD after the ninth clock fell: end of the ACK bit
        loop { @rise p0 : shin }        ; next byte
        @fall p0 : p0 <- 0              ; eighth clock over: hold SCL
        @tick : p1 <- 0                 ; THD later: ACK
        @htx  : push S                  ; stretches while the host FIFO is full
        tset  R2                        ; ACK setup
        jmp   wr
; transmit
rd:     @tick : p0 <- 1                 ; release SCL: the controller reads the ACK
rdbyte: @fall p0 : p0 <- 0              ; ninth clock over: hold SCL
        @tick : p1 <- 1                 ; THD later: SDA released
        @hrx  : pull S                  ; byte from the host (SCL stretched meanwhile)
        p1 <- out                       ; bit 7 (SCL has been low for more than THD)
        tset  R2                        ; data setup time
        @tick : p0 <- 1                 ; release SCL
        loop { @tick : p1 <- out }      ; bits 6..0, each THD after its SCL fall
        @tick : p1 <- 1                 ; THD after the eighth fall: SDA released for the controller's ACK
        @rise p0 : jlo p1, rdbyte       ; ACK: another byte
        jmp   idle                      ; NACK: done, wait for STOP or a new START
