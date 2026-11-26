; autobaud.s - UART receiver that locks onto a 0x55 sync character, then runs the uart_rx 8N1 loop (measure_design.md)
; words: 75 | engines: 1 | 300 baud - 1 Mbaud
; pins: p0 = RXD (ui_in[RXPIN]), as uart_rx
; method: 0x55 has falls at bits 0, 2, 4, 6, 8. The timer restarts in hardware on every fall (TCFG sedge), so each
;       capture reads one 2-bit interval: 0x10000 - T steps plus 65536 per wrap (tick call, R0 += 1; no tick can come
;       between a capture and its sum: the timer was just restarted). Sum of 4 intervals = 8 bits = D clk (32 bits).
;       PRE = 0 / 1 / 2 for D < 2^19 / 2^23 / more; Ds = D >> 4 PRE; BIT = Ds >> 3, FRAC = (Ds & 7) << 5: mean bit
;       = BIT + FRAC / 256 steps, the sender's own rate to +-1 clk over 8 bits (a +-2 % sender is measured, not an error).
;       Below 1526 baud (intervals > 65536 clk) a fall inside the wrap call (<= 14 clk) is captured late: D off by
;       <= 14 clk (< 2e-5 relative).
; host: lock record = the first 4 bytes after the start or an evt 3: BIT lo, hi, FRAC, PRE (TCFG), then one byte
;       per character as uart_rx (evt 1 = framing
;       error / break). evt 3 = BIT < 15 (faster than 3.3 Mbaud: not a 0x55 at a supported rate): measures again.
;       The 0x55 itself is not delivered. The record has no marker: re-lock = STOP, RST of the engine (empties its
;       FIFO), reload, RUN; host/measure.py resyncs if PRE > 2 or FRAC's low 5 bits are set.
; departures from the first design (D221 -> D223):
;       1. no validity check of the sync character (bit 8 low, stop high): at 1 Mbaud the rate is still being computed
;          while those bits pass. Checked only: 5 falls with a rise between each, BIT >= 15. A wrong sync character
;          gives a wrong rate, seen as framing errors (evt 1) or wrong bytes; the host then RUNs entry `ab` again.
;       2. timing rule below (the computation runs after the 5th fall, not in the stop bit).
; rule: the character after the 0x55 starts >= 140 clk after the 0x55's last fall (the rate is computed meanwhile):
;       idle >= 140 - 2 BIT clk after its stop bit, i.e. back to back for BIT >= 70 (<= 714 kbaud), 40 clk (0.8 bit)
;       at 1 Mbaud (ISS, 4 slot phases)
; tested (ISS): 9600 baud - 1 Mbaud at -2 / 0 / +2 %, 4 slot phases; 1200 (+2 %, wraps), 700 (-2 %, PRE 1),
;       300 (+2 %, PRE 1); a 5 Mbaud 0x55 refused (evt 3) and the next 0x55 locked

.equ RXPIN,  7                  ; ui_in pad of RXD
.equ ENG,    1                  ; engine and load address: override with -D
.equ ORG,    16
.engine ENG
.init
        li    R0, #RXPIN
        wcfg  BIND01, R0
        li    R0, #0x0017               ; SHCFG: 8 bits, LSB first, input p0
        wcfg  SHCFG, R0
        li    R0, #0x2A0C               ; CC0: tick, call
        wcfg  CC0, R0
        li    R0, #wrap
        wcfg  WV0, R0
.entry ab
.code
.org ORG
ab:     wrst                            ; a re-RUN may have stopped the engine mid-character (N != 0)
        li    R0, #0xFFFF
        mov   P, R0
        li    R0, #0x0800               ; TCFG: hard sync on falls of p0, periodic, /1
        wcfg  TCFG, R0
        li    R0, #0
        mov   C, R0                     ; C = 0: adc constant
        li    R2, #0
        li    S, #4
        watch #1
        @fall p0 : mov R0, C            ; start bit: wraps count from here
iv:     @rise p0 : nop
        @fall p0 : mov R1, T            ; 2 bits since the previous fall
        xor   R1, P                     ; 0xFFFF - T
        add   R2, R1
        adc   R0, C
        add   S, #-1
        jnz   iv
        watch #0
        add   R2, #4                    ; each interval is (0xFFFF - T) + 1
        adc   R0, C
        li    R1, #0                    ; R1 = PRE << 8, S = 4 PRE
        cmp   R0, #8
        jnc   fr                        ; D < 2^19: PRE 0
        li    S, #4
        lih   R1, #1
        cmp   R0, #128
        jnc   sh                        ; D < 2^23: PRE 1
        li    S, #8
        lih   R1, #2
sh:     shr   R0, R0                    ; D >>= 1
        rrc   R2, R2
        add   S, #-1
        jnz   sh
fr:     mov   C, R1
        li    R1, #7
        and   R1, R2
        add   R1, R1
        add   R1, R1
        add   R1, R1
        add   R1, R1
        add   R1, R1                    ; FRAC = (Ds & 7) << 5
        or    R1, C                     ; TCFG = FRAC | PRE << 8 (no sync)
        shr   R0, R0                    ; BIT = Ds >> 3 (fits 16 bits)
        rrc   R2, R2
        shr   R0, R0
        rrc   R2, R2
        shr   R0, R0
        rrc   R2, R2
        cmp   R2, #15
        jnc   bad
        wcfg  TCFG, R1
        push  R2                        ; lock record: 4 entries, fits the empty TX FIFO (no event marker)
        pushh R2
        push  R1
        pushh R1
        add   R2, #-1
        mov   P, R2                     ; P = BIT - 1
        shr   R0, R2
        sub   R2, R0
        mov   C, R2                     ; tock = middle of each bit
rx:     @fall p0 : tsync                ; uart_rx 8N1 from here
        @tock : jhi p0, rx
        loop { @tock : shin }
        push  S
        @tock : jlo p0, ferr
        .assume armed 9                 ; BIT >= 15 (checked above)
        .deadline 1
        jmp   rx
ferr:   evt   #1
        jmp   rx
bad:    evt   #3
        jmp   ab
wrap:   add   R0, #1                    ; watcher 0, tick: call, the preempted instruction repeats
        ret
