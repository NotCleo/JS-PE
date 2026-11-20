; ws2812.s - WS2812/SK6812 LED stream. Not designed in: three timer ticks per bit, data on the middle one.
; words: 9 | clk/bit: 63 (3 ticks of 21 clk) | rate: 794 kbit/s (set by the LED), any number of LEDs streamed | engines: 1
; pins: p0 = DOUT (uo_out[2], push-pull)
; host: 3 bytes per LED (G, R, B), MSB first, sent as one burst; a pause >= 280 us latches the LEDs
; timing: T0H 21 clk (0.42 us), T1H 42 clk (0.84 us), T0L 42 clk (0.84 us), T1L 21 clk (0.42 us):
;         each within 40 ns of nominal (allowed +-150 ns); edges are fused to ticks, no jitter
; grid: restarted after idle; kept while bytes are waiting, so bytes follow without a gap
; spacing: ticks 21 clk apart; bound: next bit armed <= 6 clk, next byte <= 14 clk after the third tick

.equ THIRD, 21

.equ ENG,    0                  ; engine and load address: override with -D
.equ ORG,    176
.engine ENG
.pad uo2, p0
.init
        li    R0, #THIRD - 1
        mov   P, R0
        li    R0, #0x07                 ; SHCFG: 8 bits, MSB first
        wcfg  SHCFG, R0
        p0 <- 0
.entry idle
.code
.org ORG
idle:   @hrx  : nop                     ; line low while waiting
        tsync                           ; fresh tick grid
byte:   pull  S
bit:    @tick : p0 <- 1                 ; every bit starts high
        @tick : p0 <- out               ; a 0 ends here, a 1 stays high
        @tick : p0 <- 0                 ; a 1 ends here
        .assume armed THIRD - 6         ; ticks THIRD apart, this guard armed <= 5 clk after the previous one
        .deadline 6, bit
        .deadline 14, byte
        jw    bit                       ; eight bits per byte
        jrx   byte                      ; next byte is waiting: no gap
        jmp   idle
