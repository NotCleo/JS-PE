; i2c_tgt.s - I2C target: 7-bit address, receive with ACK, transmit until NACK, clock stretching
; words: 32 (FAULTS = 1: 111 + fault word) | clk/bit: external (125 at 400 kHz); SCL hold 3-4 clk + pad after SCL falls, own SDA edges THD + 3..4 clk after it | max: 400 kHz (phases >= 30 clk against the 14-clk bound); 1 MHz needs THD 5 and is marginal (13-clk high phase) | engines: 1
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
; FAULTS = 1 (fault_design.md): at every ACK slot the target gives (SCL held, so the check only stretches) the
;          fault word FAULT_W + ENG is read; POS = ACK slots to let pass first (counted down in the word), then
;          TYPE 1 NACK ARG slots in a row (ARG counted down; a NACKed address returns to idle: no read, nothing
;          pushed), 2 SCL held ARG x 16 clk longer, then ACK, 3 not
;          addressed (no ACK, back to idle), 4 SDA held low ARG x 16 clk from the ACK (watchers off, SCL released,
;          then idle). Cleared when done (fires once). Trade-off: the check stretches every ACK slot even when
;          unarmed (~40 clk after the SCL fall; on the bus +10-17 clk at 400 kHz, 0 at 100 kHz; +62-69 clk while
;          POS counts down); UM10204 Fm/Sm timing stays clean (sw/tests/test_faults.py, i2c_violations).

.equ ADDR, 0x42
.equ THD,  13                   ; own SDA changes THD + 3..4 clk after SCL falls (320-340 ns)
.equ TSU,  20                   ; SCL is released TSU + 2..5 clk after SDA changes: 440 ns >= 300 ns rise
                                ; + 100 ns tSU;DAT (Fm). Standard mode with a 1 us rise: 60 (D043)
.assert (THD >= 1) & (THD <= 255) & (TSU <= 255), "THD and TSU must stay below the compare value 255"
.assert TSU >= 13, "ACK / data setup TSU + 2 clk >= 300 ns (Sm tSU;DAT 250 ns + margin, D097)"

.equ ENG,    0                  ; engine and load address: override with -D
.equ ORG,    144
.equ FAULTS, 0                  ; 1 = fault-injection variant
.include "cleo_mem.inc"
.equ FW,     FAULT_W + ENG
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
.if FAULTS
        call  fchk                      ; CY = 0: the slot was handled; Z = 1: NACKed
        jz    idle                      ; a NACKed address: not addressed (no read, nothing pushed)
        jnc   aack
.endif
        @tick : p1 <- 0                 ; THD later: ACK
aack:   @htx  : push S                  ; address byte to the host
        tset  R2                        ; ACK setup
        jb    rd                        ; last bit shifted in = R/W
; receive
wr:     @tick : p0 <- 1                 ; release SCL: the controller reads the ACK
        @tick : p1 <- 1                 ; THD after the ninth clock fell: end of the ACK bit
        loop { @rise p0 : shin }        ; next byte
        @fall p0 : p0 <- 0              ; eighth clock over: hold SCL
.if FAULTS
        call  fchk
        jnc   wack
.endif
        @tick : p1 <- 0                 ; THD later: ACK
wack:   @htx  : push S                  ; stretches while the host FIFO is full
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
.if FAULTS
; ACK slot check, SCL held low; R1, R2 scratch, R2 = TSU - 1 again on return
fchk:   li    R1, #FW
        ld    R2, [R1]
        shl   R2, R2                    ; CY = ARMED
        jnc   fret
        ld    R2, [R1]
        li    R1, #0x0F00
        tst   R2, R1                    ; POS = 0: fire at this slot
        jz    fire
        li    R1, #0x0100
        sub   R2, R1                    ; one slot nearer
        li    R1, #FW
        st    R2, [R1]
fret:   li    R2, #TSU - 1
        cmp   R2, #0                    ; CY = 1: the caller ACKs as usual
        ret
fire:   shl   R2, R2                    ; drop ARMED
        shl   R2, R2                    ; CY = TYPE[2]
        jc    fstk
        shl   R2, R2                    ; CY = TYPE[1]
        jc    f23
        @tick : nop                     ; TYPE 1: NACK (SDA stays released)
        li    R1, #FW
        ld    R2, [R1]
        add   R2, #-1                   ; one NACK used
        li    R1, #0xFF
        tst   R2, R1
        jnz   fput
fclr:   li    R2, #0                    ; fault complete
fput:   li    R1, #FW
        st    R2, [R1]
        li    R2, #TSU - 1
        li    R1, #0
        add   R1, #0                    ; CY = 0: slot handled, Z = 1: NACKed
        ret
f23:    shl   R2, R2                    ; CY = TYPE[0]; R2 = ARG << 4 (POS = 0)
        jc    fign
        tset  R2                        ; TYPE 2: SCL held ARG x 16 clk longer
        @tick : p1 <- 0                 ; then ACK
        li    R2, #0
        li    R1, #FW
        st    R2, [R1]                  ; fault complete
        li    R2, #TSU - 1
        cmp   R2, #255                  ; CY = 0: slot handled, Z = 0
        ret
fstk:   shl   R2, R2
        shl   R2, R2                    ; TYPE 4: R2 = ARG << 4
        @tick : p1 <- 0                 ; SDA low, THD after the fall
        watch #0                        ; START/STOP watchers off
        li    R1, #0
        wcfg  TCFG, R1                  ; no SCL sync of the timer meanwhile
        tset  R2
        p0 <- 1                         ; SCL released: the controller runs into the low SDA
        @tick : p1 <- 1                 ; SDA released
        li    R2, #0
        li    R1, #FW
        st    R2, [R1]                  ; cleared before the watchers can abort
        li    R1, #0x4800
        wcfg  TCFG, R1
        watch #3
fign:   li    R2, #0                    ; TYPE 3 (and after 4): not addressed, back to idle
        li    R1, #FW
        st    R2, [R1]
        li    R2, #TSU - 1
        jmp   idle
.reserve FW, 1
.org FW
        .word 0
.endif
