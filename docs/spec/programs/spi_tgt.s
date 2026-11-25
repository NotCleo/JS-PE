; spi_tgt.s - SPI target, register device: byte 1 = register number, then table[reg], table[reg+1], ...
; words: 12 + table (FAULTS = 1: 69 + table + fault word) | clk/bit: external | max SCK: 4 MHz (default slots; 2 MHz is the MUST), about 6 MHz solo | engines: 1
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
;  FAULTS = 1 (fault_design.md): after CS falls the fault word FAULT_W + ENG is read and decoded (handler in L,
;        value in R2, POS counted down in R0); at response byte POS: TYPE 1 byte ^= ARG (ARG = mask, bit k = 1 << k),
;        TYPE 2 / 3 that word is 8 - ARG / 8 + ARG bits (later bytes slip ARG bits), SHCFG restored at the next
;        boundary. Cleared when read (fires in that CS frame, if it reaches byte POS). Trade-off: first sampling edge
;        >= 160 clk (3.2 us) after CS falls (static setup bound 141 + HOLD) and SCK <= 1 MHz (the fault lands ~30 clk
;        after the 8th edge; tested down to 1.67 MHz); 77 + fault word words; >= 4096 response bytes in one frame repeat it

.equ MODE12, 0                  ; 1 for SPI modes 1 and 2
.equ FAULTS, 0                  ; 1 = fault-injection variant
.equ FX,     FAULTS * 8         ; FAULTS: extra clk on the per-byte path (add + jnc)
.equ FSET,   141                ; FAULTS: HOLD tick -> command sampler armed (fault word read and decoded)
.equ HOLD,   5                  ; >= G + 1 of the slot map (5 default): the .deadline after tset

.equ ENG,    0                  ; engine and load address: override with -D
.equ ORG,    64
.include "cleo_mem.inc"
.equ FW,     FAULT_W + ENG
.equ SHC,    0x67               ; SHCFG: 8 bits, MSB first, input p3
.engine ENG
.pad uio2, p2
.init
        li    R0, #(11 | (MODE12 << 5)) | (8 << 8)   ; BIND01: p0 = uio_in[3], p1 = uio_in[0]
        wcfg  BIND01, R0
        li    R0, #9 << 8               ; BIND23: p3 = uio_in[1]
        wcfg  BIND23, R0
        li    R0, #SHC                  ; SHCFG: 8 bits, MSB first, input p3
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
.if FAULTS
        li    R2, #SHC
        wcfg  SHCFG, R2                 ; 8 bits (a short/long word cut by CS) and wrst
        li    R1, #table                ; table pointer (R0, R2 are fault scratch during a transfer)
        li    R2, #HOLD
.else
        wrst                            ; drop a partial byte
        mov   R1, R0                    ; table pointer
.endif
        li    S, #0                     ; the byte shifted out during the command is 0x00
        @fall p1 : p2 <- f              ; CS low: MISO driven, following the shifter
        tset  R2                        ; tick HOLD + 1 clk later
        .deadline HOLD                  ; @tick armed before its tick: >= 1 waiting clock
        @tick : nop                     ; the waiting clock drops SCK edges latched while CS was high (D055)
.if FAULTS
        .deadline FSET, fnone           ; unarmed
        .deadline FSET, fs23            ; armed, TYPE 2/3 (TYPE 1 is shorter)
        li    R2, #FW
        ld    R0, [R2]                  ; fault word
        shl   T, R0                     ; CY = ARMED
        jnc   fnone
        st    S, [R2]                   ; consumed (S = 0): fires in this frame at response byte POS
        mov   R2, R0
        li    R0, #0x0F00
        and   R0, R2
        shr   R0, R0
        shr   R0, R0
        shr   R0, R0
        shr   R0, R0                    ; R0 = POS << 4: counted down by 16 per byte, CY = 0 at byte POS
        mov   R1, R2
        shl   R1, R1
        shl   R1, R1
        shl   R1, R1                    ; CY = TYPE[1]
        jc    fs23
        li    R1, #f1                   ; TYPE 1: R2 = word (ARG = XOR mask)
        jmp   fset
fs23:   shl   R1, R1                    ; CY = TYPE[0]: 1 = long
        li    R1, #0x0F
        and   R2, R1                    ; ARG
        li    R1, #SHC
        jc    flong
        sub   R1, R2                    ; TYPE 2: 8 - ARG bits
        jmp   fs3
flong:  add   R1, R2                    ; TYPE 3: 8 + ARG bits
fs3:    mov   R2, R1                    ; R2 = SHCFG for that word
        li    R1, #fsl
fset:   mov   L, R1                     ; handler, entered by ret at byte POS
        li    R1, #table
        jmp   cmd
fnone:  li    R0, #0xFFFF               ; never reaches byte POS (< 4096 bytes)
        li    R2, #frst
        mov   L, R2
.endif
cmd:    loop { @rise p0 : shin }        ; register number
        .assume armed 7 * 8             ; 8 sampling edges span >= 7 SCK periods (>= 8 clk at the 6 MHz maximum)
        .deadline 10 + FX               ; sampler armed again <= 10 clk after the 8th edge
        ld    S, [R1+S]!                ; first response byte
.if FAULTS
        add   R0, #-16                  ; CY = 0: this is byte POS
        jnc   fire
.endif
next:   loop { @rise p0 : shin }        ; byte out, next byte in (ignored)
        .assume armed 7 * 8
        .deadline 14 + FX
        ld    S, [R1+1]!                ; following register
.if FAULTS
        add   R0, #-16
        jnc   fire
.endif
        jmp   next
.if FAULTS
fire:   ret                             ; to the handler set up after CS fell
f1:     xor   S, R2                     ; TYPE 1: response byte ^= ARG (bits above 7 are not shifted out)
        jmp   next
fsl:    wcfg  SHCFG, R2                 ; TYPE 2/3: this word 8 -/+ ARG bits
        li    R2, #frst
        mov   L, R2
        li    R0, #0                    ; the next boundary comes back to restore
        jmp   next
frst:   li    R2, #SHC
        wcfg  SHCFG, R2                 ; 8 bits again
        jmp   next
.reserve FW, 1
.org FW
        .word 0
.endif
table:  .word 0x00A5, 0x005A, 0x0001, 0x0002, 0x0003, 0x0004, 0x0005, 0x0006
