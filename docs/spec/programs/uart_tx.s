; uart_tx.s - UART transmitter (8N1 default; 5-9 data bits, even/odd parity, 1, 1.5 or 2 stop bits)
; words: 8 (8N1), 12 max | clk/bit: BIT (50 at 1 Mbaud) | max: 4.5 Mbaud (BIT >= 11) | engines: 1
; pins: p0 = TXD (uo_out[0], push-pull; INV = 1 inverts the pad)
; options: INV (inverted line), MSB (MSB first), low baud: BAUD < 763 selects timer prescale 16 (256 below 48)
; host: one byte per character (9 data bits: low byte, then a byte holding bit 8)
; grid: after idle the bit timer is restarted, so the start bit is exact; while characters are
;       waiting, frames follow back to back on the same grid (stop bit exactly one bit)
; spacing: one bit time between events; bound: next start-bit instruction armed <= 10 clk after
;          the stop tick (needs BIT >= 11)
; 1.5 stop bits: the grid is restarted half a bit into the stop bit (tock = mid-bit), so the next
;          start bit begins exactly 1.5 bits after the stop edge and the frames stay back to back
; 9 data bits: the grid restarts for every character (stop bit up to 15 clk longer)
; above 3.1 Mbaud the host link must run at 8 clk per bit to keep characters back to back

.equ CLK_HZ, 50000000
.equ BAUD,   1000000
.equ PRE,    (CLK_HZ / BAUD > 65536) + (CLK_HZ / BAUD > 16 * 65536)  ; timer prescale 1, 16, 256 (low baud)
.equ DIV,    1 << (4 * PRE)
.equ BIT,    CLK_HZ / (BAUD * DIV)          ; bit time in timer steps (clk when PRE = 0), integer part
.equ FRAC,   (CLK_HZ % (BAUD * DIV)) * 256 / (BAUD * DIV)   ; fraction in 1/256 step (0 at 1 Mbaud)
.equ INV,    0                  ; 1 = inverted line (idle low)
.equ MSB,    0                  ; 1 = MSB first
.equ DBITS,  8                  ; 5..9
.equ PARITY, 0                  ; 0 none, 1 even, 2 odd
.equ STOP2,  0                  ; 1 = two stop bits
.equ STOP15, 0                  ; 1 = 1.5 stop bits
.assert STOP2 + STOP15 < 2, "one stop-bit option"

.equ ENG,    0                  ; engine and load address: override with -D
.equ ORG,    0
.engine ENG
.if INV
.pad uo0, p0, inv                ; inverted line: the pad inverts idle, start, data and stop
.else
.pad uo0, p0
.endif
.init
        li    R0, #BIT - 1
        mov   P, R0                     ; tick every BIT clk
.if FRAC | PRE
        li    R0, #FRAC | (PRE << 8)    ; TCFG: mean bit time = BIT + FRAC/256 steps, prescale
        wcfg  TCFG, R0
.endif
.if STOP15
        li    R0, #BIT - BIT / 2
        mov   C, R0                     ; tock = middle of a bit
.endif
        li    R0, #(DBITS - 1) | ((MSB == 0) << 4) ; SHCFG: word length, bit order
        wcfg  SHCFG, R0
        li    R0, #(PARITY == 2) << 2   ; TAPCFG: q = parity, start value 1 for odd
        wcfg  TAPCFG, R0
        p0 <- 1                         ; idle level before the pad is enabled
.entry idle
.code
.org ORG
idle:   @hrx  : nop                     ; line idle: wait for the host
        tsync                           ; fresh bit grid
tx:     pull  S                         ; next character (present: checked by @hrx or jrx)
.if DBITS > 8
        @hrx  : pullh S                 ; ninth data bit
        tsync
.endif
        @tick : p0 <- 0                 ; start bit
        loop { @tick : p0 <- out }      ; data bits, LSB first
.if PARITY
        @tick : p0 <- q                 ; parity bit
.endif
        @tick : p0 <- 1                 ; stop bit
.if STOP2
        @tick : nop                     ; second stop bit
.endif
.if STOP15
        @tock : tsync                   ; mid stop bit: new grid, next tick 1.5 bits after the stop edge
.endif
        .assume armed (BIT * DIV - 5) * (1 - STOP15) + ((BIT / 2) * DIV - 1) * STOP15  ; one bit (STOP15: half a bit) after the previous tick
        .deadline 10, tx
        jrx   tx                        ; another character is waiting: back to back
        jmp   idle
