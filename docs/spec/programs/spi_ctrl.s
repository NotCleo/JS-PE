; spi_ctrl.s - SPI controller, modes 0-3, MSB or LSB first, 1-16 bit words, n words with CS held
; words: 18 (CPHA 0), 17 (CPHA 1), +3 for WL > 8 | clk/bit: BIT (8 as written) | max: 6.25 MHz read/write (BIT 8, SMP 6, 50 ns answer window); write-only 8.3 MHz (BIT 6), 12.5 MHz in the solo slot map (BIT 4) | engines: 1
; pins: p0 = SCK (uio[3]), p1 = CS (uio[0]), p2 = MOSI (uio[1]), p3 = MISO (uio[2], shift input)
; host: [n] then n words (n = 1..255); n words come back. A word is one byte (WL <= 8: its low WL bits) or
;       two bytes, low first (WL > 8)
; modes: CPHA below; CPOL = 1 by `inv` on the SCK pad; LSB first by SHCFG bit 4; WL = SHCFG word length
; timing: launch edge at tick, sample edge SMP clk later at tock. The target has (SMP - 3) clk - 10 ns
;         to answer (50 ns at SMP 6; pads are one clock behind the pin state, D081). SMP = BIT/2 gives a symmetric clock for slow rates.
;         CS times from CSDLY (R2, changeable per transfer by EXEC; CSDLY >= 6 so that the tick finds its
;         instruction armed), measured at the pads (ISS, D091): setup (CS fall -> first SCK edge) =
;         CSDLY + BIT + SMP + 2 (CPHA 0) or CSDLY + BIT + 2 (CPHA 1), exact for CSDLY >= 9, up to 8 clk longer
;         below; hold (last SCK edge -> CS rise) = CSDLY + 11..16 clk (WL > 8: ..20); inactive (CS rise -> fall)
;         = max(CSDLY + 8, 20) clk (WL > 8: + 4) with the next transfer's host bytes waiting, longer otherwise
; grid: the bit timer is restarted for every word, so all edges inside a word are exact
; first bit of a word (default slots): launch armed <= 5 clk, sample armed <= 9 clk after `tsync`,
;         so BIT >= 6 and BIT + SMP >= 10
; spacing: tick/tock >= 2 clk apart inside the loop (held pair, no fetch); bound: 1 clk
; inter-word gap: first edge to first edge BIT + 26..32 clk (ISS; WL > 8: + 8)

.equ CLK_HZ, 50000000            ; clk the image is built for (.bin CLK record, D089)
.equ BIT,   8                   ; SCK period, clk
.equ SMP,   6                   ; sample point after the launch edge, 3..BIT-2 (reads need SMP >= 4)
.equ CPHA,  0
.equ CSDLY, 16
.equ CPOL,  0
.equ LSB,   0                   ; 1 = LSB first
.equ WL,    8                   ; bits per word, 1..16

.equ ENG,    0                  ; engine and load address: override with -D
.equ ORG,    32
.engine ENG
.if CPOL
.pad uio3, p0, inv
.else
.pad uio3, p0
.endif
.pad uio0, p1
.pad uio1, p2
.init
        li    R0, #BIT - 1
        mov   P, R0
        li    R0, #BIT - SMP
        mov   C, R0
        li    R0, #0x60 | (WL - 1) | (LSB << 4)   ; SHCFG: input p3
        wcfg  SHCFG, R0
        li    R0, #10 << 8              ; BIND23: p3 = uio_in[2]
        wcfg  BIND23, R0
        li    R2, #CSDLY - 1
        p0 <- 0, p1 <- 1                ; SCK idle, CS high
        p2 <- 0
.entry xfer
.code
.org ORG
xfer:   @hrx  : pull R1                 ; word count
        @hrx  : pull S                  ; first word
.if WL > 8
        @hrx  : pullh S
.endif
        tset  R2                        ; CS inactive time
        @tick : p1 <- 0                 ; CS low
        tset  R2                        ; CS setup
        @tick : nop
byte:   tsync                           ; bit clock: first launch one period from here
.if CPHA == 0
        loop {
          @tick : p0 <- 0, p2 <- d      ; trailing edge (none before the first bit) + launch
          @tock : p0 <- 1, shin         ; leading edge + sample
        }
        @tick : p0 <- 0                 ; last trailing edge
.else
        loop {
          @tick : p0 <- 1, p2 <- d      ; leading edge + launch
          @tock : p0 <- 0, shin         ; trailing edge + sample
        }
.endif
        @htx  : push S                  ; received word; SCK idle while the host FIFO is full
.if WL > 8
        @htx  : pushh S
.endif
        add   R1, #-1
        jz    done
        @hrx  : pull S                  ; next word; SCK idle meanwhile
.if WL > 8
        @hrx  : pullh S
.endif
        jmp   byte
done:   tset  R2                        ; CS hold
        @tick : p1 <- 1                 ; CS high
        jmp   xfer
