; lp_tx.s - 10BASE-T link pulses: normal link pulses (NLP) or fast link pulse (FLP) bursts with a link code word
;           (802.3 clause 14 / 28); autonegotiation arbitration on the host (host/eth.py, eth_design.md)
; words: 28 | pulse: PW clk high (5 = 100 ns as written), positions TP apart (3125 clk = 62.5 us), period NTICK
;        positions (256 = 16 ms) | max: TP >= 100, PW 2..TP / 2 | engines: 1
; pins: p0 = TXP (uio[0], pulse = 1), p1 = TXN (uio[1], held 0): idle = both low (Pmod: line driver + magnetics)
; host: one command per period, taken at the period start: 0x00 NLP (the default), 0x01 + code word (2 bytes, low
;       first) FLP bursts with that word until the next command, 0x02 (or any other byte) silence (link loss,
;       autonegotiation restart). Send 0x01 and its 2 bytes in one write (a split wait delays that period)
; timing: timer periodic TP: tick = pulse start (pin state at the tick clock), tock PW clk later = pulse end, so the
;       width is exact. NLP: one pulse per period. FLP: clock pulse at positions 0, 2, .. 32, data pulse at the odd
;       positions where the code word bit (D0 first) is 1; burst 2 ms, burst to burst NTICK positions
; bound: tock armed 1 clk after its tick (tick waits >= TP - 80 clk: prefetch credited)

.equ CLK_HZ, 50000000
.equ TP,    CLK_HZ / 16000      ; pulse position spacing: 62.5 us
.equ PW,    CLK_HZ / 10000000   ; pulse width: 100 ns
.equ NTICK, 256                 ; positions per period: 16 ms
.assert (TP >= 100) & (TP <= 65536) & (PW >= 2) & (PW <= TP / 2), "TP 100..65536, PW 2..TP / 2"
.assert (NTICK >= 34) & (NTICK <= 32767), "NTICK 34..32767"

.equ ENG,    0                  ; engine and load address: override with -D
.equ ORG,    160
.engine ENG
.pad uio0, p0
.pad uio1, p1
.init
        li    R0, #TP - 1
        mov   P, R0
        li    R0, #TP - PW
        mov   C, R0                     ; tock PW clk after each tick
        li    R0, #0x1F                 ; SHCFG: 16 bits, LSB first
        wcfg  SHCFG, R0
        li    R2, #0                    ; NLP
        p0 <- 0
        p1 <- 0
        tsync
.entry top
.code
.org ORG
top:    li    R0, #NTICK
        jnrx  run
        pull  R2                        ; command
        cmp   R2, #1
        jnz   run
        @hrx  : pull R1                 ; link code word
        @hrx  : pullh R1
run:    cmp   R2, #1
        jz    flp
        jc    gap                       ; >= 2: silence
        @tick : p0 <- 1                 ; NLP
        .assume armed 8                 ; ticks TP >= 100 clk apart, <= 80 clk of work since the last one
        .deadline PW - 1
        @tock : p0 <- 0
        add   R0, #-1
        jmp   gap
flp:    mov   S, R1
        add   R0, #-33
fb:     @tick : p0 <- 1                 ; clock pulse
        .assume armed 8
        .deadline PW - 1
        @tock : p0 <- 0
        @tick : p0 <- out               ; data pulse if the bit is 1
        .assume armed 8
        .deadline PW - 1
        @tock : p0 <- 0
        jw    fb
        @tick : p0 <- 1                 ; 17th clock pulse
        .assume armed 8
        .deadline PW - 1
        @tock : p0 <- 0
gap:    @tick : nop
        add   R0, #-1
        jnz   gap
        jmp   top
