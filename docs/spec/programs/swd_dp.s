; swd_dp.s - SWD DP emulator (ADIv5 debug port, target side): DPIDR, CTRL/STAT, ABORT, SELECT, RDBUFF on chip,
;            AP accesses and SELECT through a memory box served by the RP2350; WAIT, FAULT, parity, line reset
; words: 189 + 5 box (MDROP = 1: 209 + 5) | clk/SWCLK: external | max SWCLK: 781 kHz back to back, 1.04 MHz with
;        >= 2 idle cycles after each transaction (default slot map, ISS) | engines: 1
;        per DP (2 on one bus: MDROP = 1, DIO = 4 and TSEL for the second)
; pins: p0 = SWCLK (uio_in[3]), p1 = SWDIO (uio[1], DIO = 4: uio[4]; driven only for ACK and read data, also
;       the shift input)
; registers: DPIDR (read A0, value DPIDR); ABORT (write A0: STKERRCLR clears STICKYERR, WDERRCLR clears WDATAERR);
;       CTRL/STAT (A4: CSYSPWRUPREQ/CDBGPWRUPREQ stored, their ACKs read back equal, WDATAERR, STICKYERR; DPBANKSEL
;       ignored); SELECT (write A8) and AP accesses go to the host; RDBUFF (read AC) = value of the last AP read;
;       RESEND (read A8) answers as RDBUFF; ABORT DAPABORT is ignored (a stalled host box keeps
;       AP accesses at WAIT). AP reads are posted: they return the previous AP read's value
; box (MEMMAP SWD_BOX + 5 * ENG): RDB_L, RDB_H (host), ST (chip: 0x10 | request = busy; host: 0 done, 0x20 done
;       with an AP error = STICKYERR), DL, DH (write data, chip). After an AP access or SELECT write the chip writes
;       ST (+ DL, DH) and pushes the request byte (0x10 | A3 A2 RnW APnDP); the host serves it (host/debug.py
;       DpBox) and writes [RDB_L, RDB_H, ST] in one WRMEM, ST last. While ST is busy: AP accesses, SELECT, RDBUFF
;       get WAIT
; replies: OK / WAIT / FAULT (sticky flag set: everything but DPIDR read, CTRL/STAT read, ABORT write); no reply
;       (protocol error: start/stop/park/request parity, or anything but a DPIDR read after a line reset) -> lockout
;       until a line reset: >= 50 ones from a transaction boundary, then a 0 (while locked out, 44 ones after any 0
;       already count; ones sent inside a write data phase are taken as data: a line reset there needs ~90 ones). Write data parity error ->
;       WDATAERR, write ignored. CTRL/STAT writes WAIT while the box is busy. MDROP = 1 (SWD v2 multidrop): TARGETSEL after a line reset gets no ACK and
;       selects (data = TSEL) or deselects (lockout; also on a data parity error) this DP
; rule: SWCLK period >= 64 clk with transactions back to back (no idle cycles), >= 48 clk with >= 2 idle cycles
;       after each; host bits change at the falling edge; the DP drives ACK/data <= 6 clk + pad after a rising edge,
;       so a host sampling at the next falling edge has >= 26 clk - pad. Longest work: the ABORT effect after an AP
;       error (60 clk fails, test_swd.py). The released turnaround cycle after read data, WAIT and FAULT is skipped
;       (the line floats high). MDROP: after a line reset a DPIDR read is answered without TARGETSEL

.equ MDROP,  0
.equ DPIDR,  0x0C001001 + (MDROP << 12)  ; RAO bit 0, version 1 (MDROP: 2), PARTNO 0xC0, RES0 [19:17]
.equ TSEL,   0x0C1E0001         ; TARGETSEL value that selects this DP (MDROP)
.equ DIO,    1                  ; SWDIO pad: 1 or 4
.equ SHC,    0x30               ; SHCFG: LSB first, input p1

.equ ENG,    0                  ; engine and load address: override with -D
.equ ORG,    40
.include "cleo_mem.inc"
.equ BOX,    SWD_BOX + 5 * ENG
.assert ENG <= 1, "SWD_BOX holds two instances"
.engine ENG
.if DIO == 4
.pad uio4, p1
.else
.pad uio1, p1
.endif
.init
        li    R0, #11 | ((8 + DIO) << 8) ; BIND01: p0 = uio_in[3] (SWCLK), p1 = SWDIO
        wcfg  BIND01, R0
        li    R0, #0x100
        mov   C, R0                     ; reset state; C also holds WDATAERR (0x80)
        li    R0, #0
        mov   P, R0                     ; power-up requests
.entry lock
.code
.org ORG
lock:   p1 <- z                         ; no reply until a line reset
        li    R0, #44                   ; 50 ones minus the 6 a rejected all-ones request consumed
lk:     @rise p0 : jlo p1, lock
        add   R0, #-1
        jnz   lk
lk1:    @rise p0 : jhi p1, lk1          ; more ones; the first 0 ends the reset
        li    R0, #0x100
        or    C, R0                     ; reset state: only a DPIDR read (MDROP: or TARGETSEL)
idle:   li    R1, #15
        li    R0, #SHC | 4
        wcfg  SHCFG, R0                 ; 5 bits after the start bit
idl:    @rise p0 : jlo p1, idl          ; start bit
        loop { @rise p0 : shin }        ; APnDP, RnW, A2, A3, parity
        jpar  lock                      ; request parity
        and   S, R1                     ; S = A3 A2 RnW APnDP
        li    R0, #tab
        ld    R0, [R0+S]
        mov   L, R0                     ; handler, entered after the park edge
        @rise p0 : jhi p1, lock         ; stop bit must be 0
        li    R0, #BOX + 2
        ld    R2, [R0]                  ; ST
        or    R2, C                     ; state: 0 ok, 0x1x busy, 0x20/0x80 sticky, >= 0x100 reset state
        @rise p0 : jlo p1, lock         ; park bit must be 1
        ret

; ---------------- writes: ACK OK, turnaround, 32 bits + parity in, then the effect (L)
abw:    cmp   R2, #0xFF
        jc    lock
        @rise p0 : p1 <- 1
        li    R0, #eab
        jmp   ackw
csw:    cmp   R2, #1
        jc    nk1                       ; busy: WAIT (only DPIDR/CTRL-STAT reads and ABORT skip it)
        @rise p0 : p1 <- 1
        li    R0, #ecsw
        jmp   ackw
apw:    cmp   R2, #1
        jc    nk1
        @rise p0 : p1 <- 1
        mov   R1, S
        li    R0, #eapw
        jmp   ackw
tsl:    cmp   R2, #0xFF
        jc    tsr
        cmp   R2, #0x20
        jc    nk2
        @rise p0 : p1 <- 1              ; TARGETSEL outside the reset state: ignored
        li    R0, #idle
ackw:   mov   L, R0
        @rise p0 : p1 <- 0
        @rise p0 : p1 <- 0
wtrn:   li    R0, #SHC | 15
        wcfg  SHCFG, R0
        @rise p0 : p1 <- z              ; release
        @rise p0 : nop                  ; turnaround
        loop { @rise p0 : shin }        ; data 0..15
        mov   R2, S
        jnpar w1
        li    R0, #4
        wcfg  TAPCFG, R0
w1:     loop { @rise p0 : shin }        ; data 16..31
        @rise p0 : smp                  ; parity bit: PAR = 0 when correct
        li    R0, #0
        wcfg  TAPCFG, R0
        jpar  perr
        ret                             ; effect
perr:
.if MDROP
        li    R0, #etsel
        cmp   R0, L
        jz    lck                       ; TARGETSEL with a parity error: deselected, no WDATAERR
.endif
        li    R0, #0x80
        or    C, R0                     ; WDATAERR
        jmp   idle
tsr:
.if MDROP
        li    R0, #etsel
        mov   L, R0
        @rise p0 : nop                  ; turnaround and ACK slots: no reply (multidrop)
        @rise p0 : nop
        @rise p0 : nop
        jmp   wtrn
.else
        jmp   lock
.endif

; ---------------- WAIT (010) / FAULT (001), then release
lck:    p1 <- z                         ; in reach of the jc below (not a bare jmp: jump targets that are jmps fire)
        jmp   lock
nk1:    cmp   R2, #0xFF
        jc    lck
        @rise p0 : p1 <- 0              ; ACK bit 0
        cmp   R2, #0x20
        jc    flt
        @rise p0 : p1 <- 1              ; WAIT
        @rise p0 : p1 <- 0
        jmp   rel
nk2:    cmp   R2, #0xFF
        jc    lck
        @rise p0 : p1 <- 0
flt:    @rise p0 : p1 <- 0              ; FAULT
        @rise p0 : p1 <- 1
rel:    @rise p0 : p1 <- z
        @rise p0 : nop                  ; turnaround cycle
        jmp   idle

; ---------------- reads: ACK OK at the turnaround edge, 32 bits + parity, release
dpidr:  @rise p0 : p1 <- 1              ; ACK bit 0
        li    R0, #idv
        ld    R1, [R0]
        ld    R2, [R0+1]!
        li    R0, #0x80
        and   C, R0                     ; reset state left
        jmp   ack1
csr:    cmp   R2, #0xFF
        jc    lck
        @rise p0 : p1 <- 1
        li    R0, #0xA0
        and   R2, R0
        mov   R1, R2                    ; [15:0]: WDATAERR, STICKYERR
        shl   R2, P
        or    R2, P                     ; [31:16]: requests and their ACKs
        jmp   ack1
rdb:    cmp   R2, #1
        jc    nk1
        @rise p0 : p1 <- 1
        li    R0, #BOX
        ld    R1, [R0]
        ld    R2, [R0+1]!
        jmp   ack1
apr:    cmp   R2, #1
        jc    nk1
        @rise p0 : p1 <- 1
        li    R0, #BOX
        ld    R1, [R0]                  ; posted: the previous AP read
        ld    R2, [R0+1]!
        @rise p0 : p1 <- 0              ; ACK bit 1
        add   S, #0x10
        st    S, [R0+1]!                ; ST = busy | request
        push  S
        jmp   ack2
ack1:   @rise p0 : p1 <- 0              ; ACK bit 1
ack2:   @rise p0 : p1 <- 0              ; ACK bit 2
        li    R0, #SHC | 15
        wcfg  SHCFG, R0
        mov   S, R1
        loop { @rise p0 : p1 <- out }   ; data 0..15
        jnpar r1
        li    R0, #4
        wcfg  TAPCFG, R0                ; podd = parity of 0..15
r1:     mov   S, R2
        loop { @rise p0 : p1 <- out }   ; data 16..31
        @rise p0 : p1 <- q              ; parity of all 32
        li    R0, #0
        wcfg  TAPCFG, R0
        @rise p0 : p1 <- z              ; released after the parity bit
        @rise p0 : nop                  ; turnaround cycle (the line floats high: not a start bit)
        jmp   idle

; ---------------- write effects
eab:    li    R0, #4
        tst   R2, R0                    ; STKERRCLR
        jz    ab1
        li    R1, #BOX + 2
        ld    R0, [R1]
        cmp   R0, #0x20
        jnz   ab1
        li    R0, #0
        st    R0, [R1]                  ; host-reported STICKYERR cleared (ST is not busy then)
ab1:    li    R0, #8
        tst   R2, R0                    ; WDERRCLR
        jz    ab2
        li    R0, #0x80
        or    C, R0
        xor   C, R0
ab2:    jmp   idle
ecsw:   li    R0, #0x5000
        and   R0, S
        mov   P, R0
        jmp   idle
eapw:   li    R0, #BOX + 2
        add   R1, #0x10
        st    R1, [R0]                  ; ST = busy | request
        st    R2, [R0+1]!               ; DL
        st    S, [R0+1]!                ; DH
        push  R1
        jmp   idle
.if MDROP
etsel:  li    R0, #tsv
        ld    R1, [R0]
        cmp   R1, R2
        jnz   tlk                       ; deselected until the next line reset
        ld    R1, [R0+1]!
        cmp   R1, S
        jnz   tlk
        jmp   idle                      ; selected: a DPIDR read must follow
tlk:    p1 <- z                         ; (a jump target that is a jmp would fire: keep one instruction first)
        jmp   lock
tsv:    .word TSEL & 0xFFFF, TSEL >> 16
.endif
tab:    .word abw, apw, dpidr, apr, csw, apw, csr, apr, apw, apw, rdb, apr, tsl, apw, rdb, apr
idv:    .word DPIDR & 0xFFFF, DPIDR >> 16
.reserve BOX, 5
.org BOX
        .word 0, 0, 0, 0, 0             ; RDB_L, RDB_H, ST idle, DL, DH (loaded with the image)
