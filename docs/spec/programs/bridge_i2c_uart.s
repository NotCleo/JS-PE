; bridge_i2c_uart.s - on-chip bridge: I2C target writes (E0) -> mailbox -> UART TX (E1); no host in the data path
; words: 30 code (park 2, E0 20, E1 8) + 1 mailbox at 511 | engines: 2 | flag: G0 = mailbox full
; max: I2C limits while UART is faster: 38.8 kB/s at 400 kHz, 9.96 kB/s at 100 kHz (incl. START, address, STOP; UBIT 50)
; pins: E0 p0 = SCL uio6 (od), p1 = SDA uio5 (od), p2 = G0; E1 p0 = TXD uo0, p1 = G0
; I2C: 7-bit address ADDR, writes only: every data byte is ACKed and forwarded; R/W = 1 or another address -> no ACK
; UART: 8N1, UBIT clk/bit, a byte starts as soon as the mailbox fills (stop bit >= UBIT)
; back-pressure: after the ACK of a byte, SCL is held low until the mailbox is empty: one UART character minus
;       one I2C byte (3 275 clk at UBIT 434), so SMBus controllers (25 ms timeout) need > 400 baud;
;       st + sset run while SCL is held, so a START/STOP watcher abort cannot split them
; timing: I2C side as i2c_tgt.s (THD, TSU, phases >= 30 clk at 400 kHz)

.equ ADDR,   0x42
.equ THD,    13
.equ TSU,    20
.equ UBIT,   50
.equ MBOX,   511

.engine 0
.pad uio6, p0, od
.pad uio5, p1, od
.init
        li    R0, #14 | (13 << 8)       ; BIND01: p0 = uio_in[6], p1 = uio_in[5]
        wcfg  BIND01, R0
        li    R0, #16                   ; BIND23: p2 = G0
        wcfg  BIND23, R0
        li    R0, #0x27                 ; SHCFG: 8 bits, MSB first, input p1
        wcfg  SHCFG, R0
        li    R0, #0x4800               ; TCFG: windowed sync on the falling edge of p0 (SCL)
        wcfg  TCFG, R0
        li    R0, #THD - 1
        mov   P, R0
        li    R0, #255
        mov   C, R0
        li    R0, #0x1809               ; CC0: START -> start
        wcfg  CC0, R0
        li    R0, #start
        wcfg  WV0, R0
        li    R0, #0x1805               ; CC1: STOP -> idle
        wcfg  CC1, R0
        li    R0, #idle
        wcfg  WV1, R0
        li    R0, #ADDR << 1            ; {address, R/W = 0}
        li    R1, #MBOX
        li    R2, #TSU - 1
        watch #3
.entry idle
.engine 1
.pad uo0, p0
.init
        li    R0, #UBIT - 1
        mov   P, R0
        li    R0, #0x17                 ; SHCFG: 8 bits, LSB first
        wcfg  SHCFG, R0
        li    R0, #16 << 8              ; p1 input = G0
        wcfg  BIND01, R0
        li    R0, #0xA01                ; CC0 = level of p1 = G0
        wcfg  CC0, R0
        li    R1, #MBOX
        sclr  #0                        ; a reload without reset may leave G0 set
        p0 <- 1
.entry uidle
.code
.org 0
park:   halt                            ; reset PC = 0: a RUN without EXEC stops here
        jmp   park
.engine 0
.code
idle:   p1 <- 1, p0 <- 1
        @never : nop                    ; until a START
start:  p1 <- 1, p0 <- 1
        wrst
        loop { @rise p0 : shin }        ; address + R/W
        cmp   S, R0
        jnz   idle                      ; other address or a read: no ACK
        @fall p0 : p0 <- 0              ; eighth clock over: hold SCL
        @tick : p1 <- 0                 ; THD later: ACK
        tset  R2
wr:     @tick : p0 <- 1                 ; release SCL: the controller reads the ACK
        @tick : p1 <- 1                 ; THD after the ninth clock fell: end of the ACK bit
        loop { @rise p0 : shin }
        @fall p0 : p0 <- 0              ; hold SCL
        @tick : p1 <- 0                 ; ACK
full:   jhi   p2, full                  ; mailbox still full: SCL stays low
        st    S, [R1]
        sset  #0
        tset  R2
        jmp   wr
.engine 1
.code
uidle:  @c0   : nop
        tsync
        ld    S, [R1]
        sclr  #0                        ; mailbox empty: the I2C side may refill it during this character
        @tick : p0 <- 0                 ; start bit
        loop { @tick : p0 <- out }
        @tick : p0 <- 1                 ; stop bit
        jmp   uidle
cend:
.org MBOX
mbox:   .space 1
.assert cend <= MBOX, "code overlaps the mailbox"
