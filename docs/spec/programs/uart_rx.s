; uart_rx.s - UART receiver (8N1 default; 5-9 data bits, parity check, framing error, break)
; words: 8 (8N1), 12 with parity | clk/bit: BIT, sampled BIT/2 after the start edge | max: 3.3 Mbaud (8N1; 2.6 with parity or 9 data bits, 2.1 with both; D100) | engines: 1
; pins: p0 = RXD (ui_in[7]; RXPIN selects another ui_in pad)
; options: INV (inverted line), MSB (MSB first), low baud: BAUD < 763 selects timer prescale 16 (256 below 48);
;          with prescale the sample point and start-edge timing are quantized to one timer step
; host: one byte per character (two for 9 data bits). Event 1 = framing error, event 2 = parity error;
;       both refer to the character just before them in the stream. A break arrives as 0x00 + event 1.
; spacing: start edge >= BIT/2 - 0.2 BIT after the stop sample (2 % fast sender)
; bound: start-edge guard armed 1 clk after the stop sample for BIT >= 15 (19 with parity or 9 data bits, 23 with both):
;        the stop guard then waits >= 9 clk, so the folded jmp's target is prefetched (D100)
; input latency cancels: the edge and the samples pass the same synchronizer

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
.equ RXPIN,  7                  ; ui_in pad of RXD (1 for the RP2350's UART0 TX on GPIO18, PINS.md 3.1)

.equ ENG,    1                  ; engine and load address: override with -D
.equ ORG,    16
.engine ENG
.init
        li    R0, #BIT - 1
        mov   P, R0
        li    R0, #BIT - BIT / 2
        mov   C, R0                     ; tock = middle of each bit
.if FRAC | PRE
        li    R0, #FRAC | (PRE << 8)    ; TCFG: mean bit time = BIT + FRAC/256 steps, prescale
        wcfg  TCFG, R0
.endif
        li    R0, #(DBITS - 1) | ((MSB == 0) << 4) ; SHCFG: word length, bit order, input p0
        wcfg  SHCFG, R0
        li    R0, #(PARITY == 2) << 2   ; parity start value
        wcfg  TAPCFG, R0
        li    R0, #RXPIN | (INV << 5)   ; p0 = ui_in[RXPIN], inverted line: BIND invert
        wcfg  BIND01, R0
.entry rx
.code
.org ORG
rx:     @fall p0 : tsync                ; start edge restarts the bit timer
        @tock : jhi p0, rx              ; middle of the start bit: high = glitch
        loop { @tock : shin }           ; data bits, LSB first
.if PARITY
        @tock : smp                     ; parity bit: into PAR, not into the word
.endif
        push  S                         ; never blocks: a full FIFO sets OVR
.if DBITS > 8
        pushh S
.endif
.if PARITY
        jpar  perr                      ; PAR over data + parity bit must be 0
.endif
stop:   @tock : jlo p0, ferr            ; stop bit must be high
        .assume armed BIT * DIV - 6 - 4 * (PARITY != 0) - 4 * (DBITS > 8)   ; one bit after the last data sample
        .deadline 1                     ; start-edge guard armed 1 clk after the stop sample (jmp target prefetched)
        jmp   rx                        ; folded during the stop wait
ferr:   evt   #1
        jmp   rx
.if PARITY
perr:   evt   #2
        jmp   stop
.endif
