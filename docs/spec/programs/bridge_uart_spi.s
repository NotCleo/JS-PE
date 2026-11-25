; bridge_uart_spi.s - on-chip bridge: UART RX (E1) -> ring -> SPI controller TX (E0); no host in the data path
; words: 47 code (park 2, E1 22, E0 23) + 32 ring | EXEC init 31 | engines: 2 | flag: G0
; max: UBIT 14 = 3.57 Mbaud with senders at -2 %, 0, +2 %, every byte exact in all 4 slot phases (sw/tests/test_bridge.py)
; pins: E1 p0 = RXD (ui_in[RXPIN], no pad); E0 p0 = SCK uio3, p1 = CS uio0, p2 = MOSI uio1 (MISO not used)
; UART 8N1, UBIT clk/bit; SPI mode 0, MSB first, one byte per CS, BIT clk/bit, CS high >= CSDLY clk between bytes
; ring: N slots at BR_RING..511 (MEMMAP.md), initialised free (bit 15 = 1); a byte (bit 15 = 0) marks its slot full;
;       overrun when slot[WP] is still full at the start bit
; host: E1 event 1 = framing error (byte still forwarded), event 2 = overrun (byte dropped); nothing else
; rate: SPI byte period P = 9*BIT + 3*CSDLY + 36 clk (+-2; measured BIT 4-30, asserted at BIT 4/8/20, 4 phases);
;       sustained iff P <= 10*UBIT: at UBIT 14 (3.57 Mbaud, CSDLY 16) SPI BIT <= 6 (400 bytes, no drop). Slower SPI: back-to-back characters, first drop at byte
;       ~ 32*P/(P - 10*UBIT): at UBIT 14 byte 306-307 (BIT 8), 117 (BIT 12), 68 (BIT 20); the ring is loaded free by the image

.equ UBIT,   50                 ; UART bit time, clk
.equ RXPIN,  7
.equ EARLY,  3                  ; sample 3 clk before the bit middle: start edges are answered late at high rates
.equ BIT,    8                  ; SPI bit time, clk
.equ SMP,    BIT - 2            ; MOSI launch to SCK rise, clk (>= 3)
.equ CSDLY,  16
.include "cleo_mem.inc"
.equ N,      BR_RING_N          ; ring slots (the ring below is written out for 32)
.assert N == 32, "ring is written out for 32 slots"
.equ FREE,   0x8000             ; slot free = bit 15 set; a stored byte has bit 15 = 0
.equ RING,   BR_RING
.assert RING + N == 512, "ring must end at 511: the pointer wraps on overflow"
.equ PTR0,   0x10000 - N        ; pointer value of slot 0; bits [8:0] = RING

.engine 1
.init
        li    R0, #UBIT - 1
        mov   P, R0
        li    R0, #UBIT - UBIT / 2 + EARLY
        mov   C, R0                     ; tock = EARLY clk before the middle of each bit
        li    R0, #0x17                 ; SHCFG: 8 bits, LSB first, input p0
        wcfg  SHCFG, R0
        li    R0, #RXPIN
        wcfg  BIND01, R0
.assert N == 32, "ring initialised for N = 32"
        li    R2, #PTR0 - 1             ; WP, advanced before use
        li    R0, #PTR0                 ; wrap mask
.entry rx
.engine 0
.pad uio3, p0
.pad uio0, p1
.pad uio1, p2
.init
        li    R0, #BIT - 1
        mov   P, R0
        li    R0, #BIT - SMP
        mov   C, R0
        li    R0, #0x67                 ; SHCFG: 8 bits, MSB first, input p3
        wcfg  SHCFG, R0
        li    R0, #16 << 8              ; p1 input = G0, p1 output stays CS
        wcfg  BIND01, R0
        li    R0, #0xA01                ; CC0 = level of p1 = G0
        wcfg  CC0, R0
        li    R1, #PTR0                 ; RP
        li    R2, #CSDLY - 1
        li    R0, #PTR0                 ; wrap mask
        p0 <- 0, p1 <- 1
        p2 <- 0
.entry xfer
.code
.org 0
park:   halt                            ; reset PC = 0: a RUN without EXEC stops here
        jmp   park
.engine 1
.code
rx:     @fall p0 : tsync
        @tock : jhi p0, rx              ; middle of the start bit: high = glitch
        @tock : shin                    ; ring work between the first data samples: keeps the stop window short
        add   R2, #1                    ; WP to this character's slot
        @tock : shin
        or    R2, R0                    ; 0 after 0xFFFF -> PTR0: wrap without a branch
        @tock : shin
        ld    R1, [R2]
        @tock : shin
        shl   T, R1                     ; CY = bit 15 = slot free
        jnc   ovr
        loop { @tock : shin }           ; bits 4..7; S keeps 8 bits: bit 15 = 0 marks the slot full
        st    S, [R2]
        sset  #0
stop:   @tock : jlo p0, ferr
        jmp   rx
ferr:   evt   #1
        jmp   rx
ovr:    loop { @tock : shin }
        evt   #2
        add   R2, #-1                   ; slot unused: the next character tries it again (PTR0 - 1 is never dereferenced)
        jmp   stop
.engine 0
.code
xfer:   ld    S, [R1]
        shl   T, S
        jnc   take
        sclr  #0
        ld    S, [R1]                   ; reload after the clear: a same-clock sset is not lost
        shl   T, S
        jnc   take
        @c0   : nop
        jmp   xfer
take:   st    R0, [R1]                  ; free the slot: bit 15 of PTR0 is 1
        add   R1, #1
        or    R1, R0
        tset  R2
        @tick : p1 <- 0
        tset  R2
        @tick : nop
        tsync
        loop {
          @tick : p0 <- 0, p2 <- d
          @tock : p0 <- 1, shin
        }
        @tick : p0 <- 0
        tset  R2
        @tick : p1 <- 1
        jmp   xfer
cend:
.reserve RING, N
.org RING
ring:   .word FREE, FREE, FREE, FREE, FREE, FREE, FREE, FREE
        .word FREE, FREE, FREE, FREE, FREE, FREE, FREE, FREE
        .word FREE, FREE, FREE, FREE, FREE, FREE, FREE, FREE
        .word FREE, FREE, FREE, FREE, FREE, FREE, FREE, FREE
.assert cend <= RING, "code overlaps the ring"
