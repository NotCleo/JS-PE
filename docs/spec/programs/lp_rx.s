; lp_rx.s - 10BASE-T link pulse receiver: NLP link integrity (link up / link fail) and FLP burst decode into the
;           16-bit link code word (802.3 clause 14 / 28); arbitration and ACK handshake on the host (host/eth.py)
; words: 57 | resolution: one timer step (PRE 2: 256 clk = 5.12 us) | max: pulses >= 0.6 TP apart inside a burst
;        (any spacing >= bound + 1 step is measured) | engines: 1
; pins: p0 = RXD (uio_in[2], comparator output, idles 0, a link pulse = 1), p1 = LED (uio[3], 1 = link up)
; host: evt 1 UP (LC-th NLP in a row spaced TMIN..LOSS), evt 2 DOWN (no pulse for LOSS while up), evt 3 FLP + 2 bytes
;       (link code word, low first), evt 4 BAD (a pulse group that is neither one NLP nor a whole FLP burst)
; timing: timer one-shot LOSS (100 ms), restarted by every pulse; T read before the restart = spacing to the previous
;       pulse. tock TEND (205 us) after the last pulse = group end (watcher 0), tick = LOSS of silence (watcher 1).
;       In a group, a pulse after a clock pulse is short (<= TH = 94 us: data bit 1, the next pulse must be a short
;       clock pulse) or long (data bit 0, this pulse is the clock). A pulse < 1 step after the previous one
;       (ringing) is ignored. Any pulse (also FLP) restarts the loss timer; only NLPs count towards link up
; limits: NLP window TMIN (4 ms) .. LOSS (802.3 link_test_min 2-7 ms, link_test_max 25-150 ms, link_loss 50-150 ms),
;       lc_max = LC (3); a too-early NLP restarts the count; FLP bursts never bring the link up (the host decides)

.equ CLK_HZ, 50000000
.equ PRE,   2                   ; timer prescaler: 0 /1, 1 /16, 2 /256
.equ STEP,  (PRE == 0) + 16 * (PRE == 1) + 256 * (PRE == 2)
.equ LOSS,  CLK_HZ / 10 / STEP          ; 100 ms without a pulse: link fail
.equ TMIN,  CLK_HZ / 250 / STEP         ; 4 ms: earliest counted NLP
.equ TH,    CLK_HZ / 10638 / STEP       ; 94 us: short (data) / long (clock) spacing
.equ TEND,  CLK_HZ / 4878 / STEP        ; 205 us without a pulse: group end
.equ LC,    3                   ; NLPs in a row for link up
.equ E_UP,  1
.equ E_DOWN, 2
.equ E_FLP, 3
.equ E_BAD, 4
.assert (PRE <= 2) & (LOSS <= 65535) & (TEND - TH <= 255) & (TH >= 2) & (TEND > TH) & (TMIN > TEND), "constants"
.assert (LC >= 1) & (LC <= 255), "LC 1..255"

.equ ENG,    1                  ; engine and load address: override with -D
.equ ORG,    192
.engine ENG
.pad uio3, p1
.init
        li    R0, #10                   ; BIND01: p0 = uio_in[2]
        wcfg  BIND01, R0
        li    R0, #PRE << 8             ; TCFG: prescaler
        wcfg  TCFG, R0
        li    R0, #0x1A0D               ; CC0: tock, abort (group end)
        wcfg  CC0, R0
        li    R0, #gend
        wcfg  WV0, R0
        li    R0, #0x1A0C               ; CC1: tick, abort (link loss)
        wcfg  CC1, R0
        li    R0, #lost
        wcfg  WV1, R0
        li    R0, #LOSS - TEND
        mov   C, R0
        li    R1, #0
        mov   L, R1                     ; NLPs in a row
        tset  R1                        ; T defined; its tick reaches `lost` with the link down: no effect
        li    R2, #0
        li    R0, #LOSS
        watch #3
        p1 <- 0
.entry iw
.code
.org ORG
iw:     @rise p0 : mov R1, T            ; first pulse of a group
        tset  R0
        mov   P, R1                     ; LOSS - spacing to the previous group
        li    S, #0                     ; bit count (+64: group ends inside a data pulse)
clk:    @rise p0 : mov R1, T            ; pulse after a clock pulse
        cmp   R1, R0
        jz    clk                       ; < 1 step after the last pulse: ringing
        tset  R0
        add   S, #1
        sub   R1, C                     ; TEND - spacing
        cmp   R1, #TEND - TH            ; CY = 1: spacing <= TH
        rrc   R2, R2                    ; bit, D0 first
        shl   R1, R2                    ; CY = the bit again
        jnc   clk                       ; long: bit 0, this pulse is the clock
        add   S, #64
dat:    @rise p0 : mov R1, T            ; the clock pulse after a data pulse: must be short
        cmp   R1, R0
        jz    dat
        tset  R0
        sub   R1, C
        cmp   R1, #TEND - TH
        jnc   clk                       ; long: the mark stays, BAD at the group end
        add   S, #-64
        jmp   clk

gend:   cmp   S, #0                     ; watcher 0: TEND without a pulse
        jz    nlp
        cmp   S, #16
        jnz   bad
        @htx  : evt #E_FLP
        @htx  : push R2
        @htx  : pushh R2
        jmp   iw
bad:    @htx  : evt #E_BAD
        jmp   iw
nlp:    mov   R1, L
        cmp   R1, #LC
        jz    iw                        ; link up: the pulse only restarted the loss timer
        li    R2, #LOSS - TMIN
        cmp   P, R2                     ; CY = 1: spacing < TMIN: count again from this pulse
        jnc   ncnt
        li    R1, #0
ncnt:   add   R1, #1
        mov   L, R1
        cmp   R1, #LC
        jnz   iw
        p1 <- 1                         ; link up
        @htx  : evt #E_UP
        jmp   iw

lost:   mov   R1, L                     ; watcher 1: LOSS without a pulse
        cmp   R1, #LC
        li    R1, #0
        mov   L, R1
        jnz   iw
        p1 <- 0                         ; link fail
        @htx  : evt #E_DOWN
        jmp   iw
