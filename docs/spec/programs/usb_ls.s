; usb_ls.s - USB 2.0 Low-Speed device (1.5 Mbit/s), enough for a HID keyboard. Not designed in (usb_ls_design.md).
; engines: E0 = TX (owns D+, D-, pull-up), E1 = RX | clk: 48 MHz, BIT = 32 clk | slots 0,1,0,1 (G = 2)
; pins: uio4 = D+ (E0.p0), uio5 = D- (E0.p1, pad inv), uio6 = 1.5 kOhm pull-up on D- (E0.p2, 1 = attached)
; memory the RP2350 writes (WRMEM, engines running): BUF..BUF+6 IN buffer [total bits = 32 + 8n (16 for a
;   STALL/handshake), CRC preset of the PID, SYNC | PID << 8, data bytes LSB first], KEY0 / KEY1 = token keys
;   of (address, EP0 / EP1), BUFKEY = key of the armed endpoint (0 = disarmed; the chip writes 0 on IN-ACK and
;   on every SETUP/OUT to us). Key = LFSR_LO after shifting token bytes b1, b2 from 0xFFFF (CRC-16 right form).
; RX host stream: per SETUP/OUT data packet [DATA PID, payload, CRC lo, CRC hi, token PID] + EV_OK or EV_CRC;
;   EV_INACK when the host ACKed the armed buffer. Bus reset: RP2350 reads uio4/uio5 on its GPIOs (SE0 >= 2.5 us).
; LIMITS: no stuff-error check (CRC / key compare catch it); a token key equal to 0, 0xFFFF or 0xB001 aliases.
; builds (the option defaults follow MEMWORDS; any -D overrides one):
;   128 words (flop, default): --mem 128           = -D OWNCHK=1 -D PIDCHK=0 -D EOPCHK=1 -D TXJK=0 -> 128 words
;   512 words (SRAM): -D MEMWORDS=512 --mem 512    = -D OWNCHK=1 -D PIDCHK=1 -D EOPCHK=3 -D TXJK=1 -> 142 words
;   128: no PID check, crossover SE0 skew <= 8 clk (167 ns), D+ read back; 512: all on, skew <= 10 clk (210 ns).

.equ CLK_HZ, 48000000
.equ BIT,    CLK_HZ / 1500000   ; 32 clk per bit
.equ MEMWORDS, 128               ; program memory of the build: 128 or 512
.equ OWNCHK, 1                  ; 1: NAK only INs to our address; 0: NAK every IN not armed (-7 words)
.equ PIDCHK, MEMWORDS > 128     ; 1: SETUP/OUT and DATA PIDs checked exactly (+8 words)
.equ EOPCHK, 1 + 2 * (MEMWORDS > 128)  ; SE0 checks before an EOP counts (+1 + 2 n words; the .filter is per line
                                ;   and passes crossover SE0): 0 any SE0 ends the packet, 1 at 9-10 clk, 3 at 21-22 clk
.equ TXJK,   MEMWORDS > 128     ; 1: E_TX keeps the J/K state in C (+2 words); 0: reads D+ back before each drive
.equ SAMPLE, 13                 ; E_RX samples D+ SAMPLE clk after its last edge: edges may be up to ~SAMPLE late,
                                ;   an early EOP (crossover skew) leaves BIT - SAMPLE - skew clk to push the last byte
.equ TOCK,   6                  ; TXJK 0: D+ is read TOCK clk before the drive tick
.assert (PIDCHK & OWNCHK) == 0 | MEMWORDS > 128, "PIDCHK 1 with OWNCHK 1 needs the 512-word build"
.equ G_ACK,  0                  ; E_RX -> E_TX: send ACK
.equ G_NAK,  1                  ; send NAK
.equ G_DAT,  2                  ; send the IN buffer
.equ G_TXB,  3                  ; E_TX is driving the bus
.equ EV_OK,  1
.equ EV_CRC, 2
.equ EV_INACK, 3
.equ ORG_TX, 0
.equ ORG_RX, 46 + 2 * TXJK
.equ BUF,    118 + 128 * (MEMWORDS > 128)   ; < 256: one-word li; 246 catches MEMWORDS 512 with --mem 128
.equ KEY0,   BUF + 7
.equ BUFKEY, BUF + 8
.equ KEY1,   BUF + 9

.slots 0, 1, 0, 1
.filter uio4, uio5              ; removes crossover SE0 glitches

; ---------------- transmitter ----------------
.engine 0
.pad uio4, p0
.pad uio5, p1, inv
.pad uio6, p2
.init
        li    R0, #BIT - 1
        mov   P, R0
        li    R0, #TOCK * (TXJK == 0)   ; TXJK 0: tock TOCK clk before the tick; 1: line state J (C written before read)
        mov   C, R0
        li    R0, #(16 + G_ACK) | ((16 + G_NAK) << 8)
        wcfg  BIND01, R0
        li    R0, #(16 + G_DAT) | (12 << 8)   ; p3 = D+ level (line state for NRZI)
        wcfg  BIND23, R0
        li    R0, #0x001F               ; SHCFG: 16 bits, LSB first
        wcfg  SHCFG, R0
        li    R0, #0x00EA               ; TAPCFG: ALT = stuff, LFSR step, run limit 6, ones only
        wcfg  TAPCFG, R0
        li    R0, #0x0E10               ; CC0: G_ACK | G_NAK (guard)
        wcfg  CC0, R0
        li    R0, #0x1A02               ; CC1: G_DAT, abort
        wcfg  CC1, R0
        li    R0, #dat
        wcfg  WV1, R0
        li    R2, #0xFFFF               ; CRC complement
        p2 <- 1                         ; attach
        watch #2
.entry tidle
.code
.org ORG_TX
dat:    li    R0, #BUF
        ld    R1, [R0]                  ; total bits
        ld    S, [R0+1]!
        wcfg  LFSR_LO, S                ; preset: 0xFFFF after SYNC | PID
        ld    S, [R0+1]!                ; SYNC | PID
        jmp   tgo
hack:   lih   S, #0xD2                  ; ACK
tgo:    sclr  #G_ACK
        sclr  #G_NAK
        sclr  #G_DAT
        sset  #G_TXB
.if TXJK
        xor   C, C                      ; line is J
.endif
        wrst
        tsync
        @tick : nop                     ; first K >= 2 bits after the host EOP
        jmp   tbit
thold:  @tick : nop                     ; 1: no transition
tnx:    jrun  tstf
        add   R1, #-1
        jz    teop
        cmp   R1, #16
        jz    tcrc
        jw    tbit
        jnc   tbit                      ; inside the CRC: no reload
        ld    S, [R0+1]!
tbit:   p3 <- out                       ; next bit -> B, RUN, LFSR
        jb    thold
.if TXJK
tdrv:   xor   C, R2                     ; 0: transition
        jz    tj
.else
tdrv:   @tock : jhi p3, tj              ; 0: transition; D+ read late (slow edges)
        .deadline TOCK - 1
        .deadline TOCK - 1, tj
.endif
        @tick : p0 <- 1, p1 <- 1        ; K
        jmp   tnx
tj:     @tick : p0 <- 0, p1 <- 0        ; J
        jmp   tnx
tstf:   p3 <- q                         ; stuff 0, booked
        jmp   tdrv
tcrc:   rcfg  S, LFSR_LO
        xor   S, R2
        jmp   tbit
teop:   @tick : p0 <- 0, p1 <- 1        ; SE0
        @tick : nop
        @tick : p0 <- z, p1 <- 0        ; J (D+ held low by the host pull-down)
        @tick : p1 <- z
        sclr  #G_TXB
tidle:  li    S, #0x80                  ; SYNC
        li    R1, #16
        @c0   : jhi p0, hack
        lih   S, #0x5A                  ; NAK
        jmp   tgo

; ---------------- receiver ----------------
.engine 1
.init
        li    R0, #BIT - 1
        mov   P, R0
        li    R0, #BIT - SAMPLE
        mov   C, R0                     ; tock SAMPLE clk after each D+ edge
        li    R0, #0x0C00               ; TCFG: hard sync on both edges of p0
        wcfg  TCFG, R0
        li    R0, #12 | (13 << 8)       ; p0 = D+, p1 = D-
        wcfg  BIND01, R0
        li    R0, #(16 + G_TXB) | (24 << 8)
        wcfg  BIND23, R0
        li    R0, #0x0197               ; SHCFG: 8 bits, LSB first, inv, diff (NRZI)
        wcfg  SHCFG, R0
        li    R0, #0x00E8               ; TAPCFG: LFSR step, run limit 6, ones only
        wcfg  TAPCFG, R0
        li    R0, #0x1110 + 0x1000 * (EOPCHK != 0)  ; CC0: ~D+ & ~D- (SE0), abort (EOPCHK: call)
        wcfg  CC0, R0
        li    R0, #eop
        wcfg  WV0, R0
        li    R0, #0xA001
        wcfg  POLY_LO, R0
        li    R0, #0
        wcfg  POLY_HI, R0
        wcfg  LFSR_HI, R0
        li    R0, #0xFFFF
        wcfg  LFSR_LO, R0               ; never a key: an EOP before the first packet matches nothing
        li    R0, #1
        wcfg  LFCFG, R0                 ; right form
        li    R2, #0                    ; R2: 0, token PID (expect DATA) or 2 (expect ACK)
        li    S, #0                     ; S is read at EOP (ACK test) before any packet shifted it
        watch #1
.entry hunt
.code
.org ORG_RX
ecrc:   evt   #EV_CRC
hunt:   li    R1, #0xFFFF               ; no packet yet; CRC init value
        smp                             ; LV = idle level
        wrst
        @rise p0 : nop                  ; first K
        loop { @tock : shin }
        cmp   S, #0x80                  ; SYNC
        jnz   hunt
rb:     @tock : shin                    ; no call: L belongs to the EOPCHK watcher
        jw    rs
        cmp   R2, #0x2D                 ; CY: DATA expected, push the packet
        jnc   rt
        push  S                         ; no taken jump since shin: done before an early EOP (crossover skew)
rt:     cmp   R1, #0xFF                 ; CY: R1 = 0xFFFF, this byte is the PID
        jnc   rs
        wcfg  LFSR_LO, R1
        mov   R1, S
rs:     jnrun rb
        @tock : smp                     ; stuff bit dropped
        jmp   rb

.if EOPCHK
eret:   ret                             ; crossover SE0: back to the preempted wait (events kept)
.endif
eop:                                    ; EOPCHK: no flag may change before the last check (preempted cmp / jcc)
.if EOPCHK > 2
        ld    R0, [R0]                  ; delay only
        jnc0  eret
.endif
.if EOPCHK > 1
        ld    R0, [R0]
        jnc0  eret
.endif
.if EOPCHK
        ld    R0, [R0]
.endif
        rcfg  R0, LFSR_LO               ; residual or token key
.if EOPCHK
        jnc0  eret
.endif
        cmp   R2, #0x2D
        jc    edat
        cmp   S, #0xD2                  ; ACK: S = last byte (R1 may miss a 1-byte PID; token b2 & 7 = 0 for EP0/1)
        li    S, #KEY0
        jz    eack
        li    R2, #0
        cmp   R1, #0x69
        jz    ein
.if PIDCHK
        cmp   R1, #0x2D
        jz    eout
        cmp   R1, #0xE1
        jnz   hunt                      ; corrupted PID, keep-alive
.endif
eout:   ld    R2, [S]                   ; SETUP / OUT (PIDCHK 0: also any other PID and keep-alive; no match)
        cmp   R2, R0
        li    R2, #0
        jnz   hunt
        st    R2, [S+1]!                ; disarm BUFKEY
        mov   R2, R1
        jmp   hunt

edat:   push  R2                        ; token PID closes the packet
        li    R2, #0
.if PIDCHK
        cmp   R1, #0xC3
        jz    edpid
        cmp   R1, #0x4B
        jnz   ecrc                      ; not a DATA PID: no ACK
edpid:
.endif
        li    R1, #0xB001
        cmp   R1, R0
        jnz   ecrc
        evt   #EV_OK
        @rise p1 : sset #G_ACK          ; SE0 -> J
txw:    watch #0                        ; own EOP is not a packet
        @fall p2 : watch #1
        jmp   hunt

eack:   cmp   R2, #2
        li    R2, #0
        jnz   tohunt
        st    R2, [S+1]!                ; disarm BUFKEY
        evt   #EV_INACK
tohunt: jmp   hunt

ein:    ld    R1, [S+1]!                ; BUFKEY
        cmp   R1, R0
        jz    indat
.if OWNCHK
        ld    R1, [S+1]!                ; KEY1
        cmp   R1, R0
        jz    inak
        li    S, #KEY0
        ld    R1, [S]
        cmp   R1, R0
        jnz   tohunt                    ; out of reach of hunt
.endif
inak:   @rise p1 : sset #G_NAK
        jmp   txw
indat:  li    R2, #2
        @rise p1 : sset #G_DAT
        jmp   txw

; ---------------- RP2350-written memory ----------------
.org BUF
inbuf:  .space 7                        ; IN buffer
keys:   .word 0x7C00, 0, 0x0860         ; KEY0, BUFKEY, KEY1 of address 0 (bus-reset state)
