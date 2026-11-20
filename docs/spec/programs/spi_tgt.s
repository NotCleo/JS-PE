; spi_tgt.s - SPI target, register device: byte 1 = register number, then table[reg], table[reg+1], ...
; words: 12 + table | clk/bit: external | max SCK: 4 MHz (default slots; 2 MHz is the MUST), about 6 MHz solo | engines: 1
; pins: p0 = SCK (uio[3]), p1 = CS (uio[0]), p2 = MISO (uio[2]), p3 = MOSI (uio[1], shift input)
; modes: 0 and 3 as written; 1 and 2 with the invert bit of p0 in BIND01 (the sampling edge is the
;        logical rising edge). MISO follows the shifter: it changes 4-5 clk + pad after each sampling
;        edge (>= 60 ns hold for the controller) and is released while CS is high.
; abort: watcher 0 = CS rising edge -> idle, at any bit position; the next CS starts on a byte boundary
; rule (D101): CS high >= 25 clk (500 ns), first sampling edge >= 7 clk (140 ns) after CS falls, SCK <= 4 MHz
;        (2 MHz MUST; about 6 MHz solo). Then MISO is driven <= 4 clk + pad after CS falls, so a controller
;        sampling MISO at its first edge has >= 3 clk - pad of setup, and every byte is correct.
;        Outside the rule (shared bus) nothing is lost while the first sampling edge comes >= 32 clk after the
;        CS rise and >= 11 clk after CS falls; MISO may then be driven up to 25 clk + pad after the CS rise (D093,
;        D100). SCK and MOSI activity while CS is high is ignored: the @tick wait after CS low drops edges
;        latched before it (D055)
; bound (default slots): table word in S <= 6 clk after the 8th edge -> MISO changes 9-10 clk + pad after
;        that edge at the pad (edge t + 8, D081): valid 25 ns before the next edge for T >= 11.25 clk (4.4 MHz),
;        50 ns at 4 MHz, 300 ns at 2 MHz (pad delay not counted); sampler armed <= 10 clk (first response byte), <= 14 clk
;        (later bytes: fully armed up to 3.5 MHz, at 4 MHz sampled <= 2 clk late inside the 6-clk
;        data-valid window). Solo slot map: <= 3 and <= 7 clk.

.equ MODE12, 0                  ; 1 for SPI modes 1 and 2
.equ HOLD,   5                  ; >= G + 1 of the slot map (5 default): the .deadline after tset

.equ ENG,    0                  ; engine and load address: override with -D
.equ ORG,    64
.engine ENG
.pad uio2, p2
.init
        li    R0, #(11 | (MODE12 << 5)) | (8 << 8)   ; BIND01: p0 = uio_in[3], p1 = uio_in[0]
        wcfg  BIND01, R0
        li    R0, #9 << 8               ; BIND23: p3 = uio_in[1]
        wcfg  BIND23, R0
        li    R0, #0x67                 ; SHCFG: 8 bits, MSB first, input p3
        wcfg  SHCFG, R0
        li    R0, #0x1A05               ; CC0: rise p1, abort
        wcfg  CC0, R0
        li    R0, #idle
        wcfg  WV0, R0
        li    R0, #table                ; R0 = table base
        li    R2, #HOLD                 ; one-shot after CS low (D055)
        watch #1
.entry idle
.code
.org ORG
idle:   p2 <- z                         ; MISO released
        wrst                            ; drop a partial byte
        mov   R1, R0                    ; table pointer
        li    S, #0                     ; the byte shifted out during the command is 0x00
        @fall p1 : p2 <- f              ; CS low: MISO driven, following the shifter
        tset  R2                        ; tick HOLD + 1 clk later
        .deadline HOLD                  ; @tick armed before its tick: >= 1 waiting clock
        @tick : nop                     ; the waiting clock drops SCK edges latched while CS was high (D055)
cmd:    loop { @rise p0 : shin }        ; register number
        .assume armed 7 * 8             ; 8 sampling edges span >= 7 SCK periods (>= 8 clk at the 6 MHz maximum)
        .deadline 10                    ; sampler armed again <= 10 clk after the 8th edge
        ld    S, [R1+S]!                ; first response byte
next:   loop { @rise p0 : shin }        ; byte out, next byte in (ignored)
        .assume armed 7 * 8
        .deadline 14
        ld    S, [R1+1]!                ; following register
        jmp   next
table:  .word 0x00A5, 0x005A, 0x0001, 0x0002, 0x0003, 0x0004, 0x0005, 0x0006
