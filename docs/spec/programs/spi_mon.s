; spi_mon.s - passive SPI monitor: MOSI on one engine, MISO on another, one shared code copy (monitor_design.md)
; words: 14 (TS = 1: 40) | clk/bit: external | max SCK: 12.5 MHz (sampling edges >= 4 clk apart; ISS passes at 2);
;        TS = 1: data held >= 14 clk after each sampling edge (1.78 MHz at 50 % duty) | engines: 2
; pins (both engines, inputs only, no .pad: every pad stays en = 0): p0 = SCK (uio[3]), p1 = CS (uio[0]),
;       p3 = data: MOSI (uio[1]) on ENG_MOSI, MISO (uio[2]) on ENG_MISO
; modes: 0 and 3 as written; 1 and 2 with MODE12 = 1 (SCK inverted in BIND01: the sampling edge is the logical rise)
; host, per engine stream: evt 1 = CS fall, data bytes (MSB first; the n-th MOSI byte pairs with the n-th MISO byte),
;       evt 3 = CS rose mid-byte (partial bits dropped), evt 63 = LOST (OVR seen: a byte or event since the previous
;       evt 2 was dropped, or an SCK edge was missed), evt 2 = CS rise. A monitor started while CS is low reports evt 2
;       without evt 1 first (the host drops that partial frame). The host never sends data to a monitor engine.
; TS = 1: evt 1 and evt 2 are followed by the captured timer value lo, hi (T counts down from TSP); evt 62 = timer
;       wrap; evt 62 may come between an event and its two bytes; an event before the two bytes means that TS is
;       missing (CS edge inside the rule's minimum). Host: time = W * (TSP + 1) + TSP - T, W = evt 62 count before
;       the hi byte, minus 1 if T < TSW (captured just before a wrap: the program waits for its evt 62 first).
;       evt 61 before a CS-rise TS: that CS rise came <= 32 clk after a wrap and may have cut the wrap call
;       (watcher 0 wins, ISA 6): if the frame's end time precedes its start, add one wrap from there on; frames longer
;       than one wrap (1.31 ms at TSPRE 0, 21 ms at 1) cannot be repaired and the host marks them +-1 wrap. A TS is up
;       to 24 clk late near a wrap (wrap call or window wait between the edge and the capture)
; rule: CS high >= 25 clk (500 ns) after a frame of whole bytes, >= 40 clk (800 ns) after a partial byte or a loss.
;       TS = 1: CS high >= 240 clk (4.8 us), first sampling edge >= 160 clk (3.2 us) after CS falls (capture + wrap
;       window wait); the ISS offset sweep fails at 190 / 80 (sw/tests/test_spi_mon.py).
;       In the rule the CS-fall guard waits >= 1 clk, which drops SCK edges latched while CS was high (another
;       target's traffic, D055). Outside it a frame may lose its first bits: reported LOST when two SCK edges merge,
;       but a single late-answered edge samples late and can flip that bit unreported (D055 rule, as spi_tgt).
; bound: bytes are pushed at the 8th edge by the auto-transfer, so the micro-loop never leaves the engine while the
;       FIFO has room; a full FIFO ends the loop: OVR via the empty RX FIFO, the rest of the frame is dropped (a late
;       re-arm would sample late), LOST at CS rise. TS = 1: the wrap call (watcher 1) preempts the loop for <= 12 clk
;       once per wrap; an edge in that window is answered late, hence the 14-clk data hold

.equ MODE12,   0                ; 1 for SPI modes 1 and 2
.equ TS,       0                ; 1 = timestamps (monitor_design.md)
.equ TSPRE,    0                ; TS: timer prescale 0, 1, 2 = /1, /16, /256
.equ TSP,      0xFFFF           ; TS: timer period (host: time = wraps * (TSP + 1) + TSP - T); smaller only for tests
.equ TSTEP,    1 << (4 * TSPRE)  ; TS: clk per timer step
.equ TSW,      (40 + TSTEP - 1) / TSTEP + 1  ; TS: capture with T < TSW (<= 40 clk before a wrap): waits for that wrap;
                                ;     the host counts it one wrap fewer
.equ TSH,      (32 + TSTEP - 1) / TSTEP + 1  ; TS: capture < 32 clk after a wrap: waits until its evt 62 is out
.equ ENG_MOSI, 0                ; engines and load address: override with -D
.equ ENG_MISO, 1
.equ ORG,      112
.equ SRC_SCK,  11               ; uio_in[3]
.equ SRC_CS,   8                ; uio_in[0]
.equ SRC_MOSI, 9                ; uio_in[1]
.equ SRC_MISO, 10               ; uio_in[2]
.equ SHC,      0x267            ; SHCFG: 8 bits, MSB first, input p3, apush

.engine ENG_MOSI
.init
        li    R0, #(SRC_SCK | (MODE12 << 5)) | (SRC_CS << 8)
        wcfg  BIND01, R0
        li    R0, #SRC_MOSI << 8
        wcfg  BIND23, R0
        li    R0, #SHC
        wcfg  SHCFG, R0
        li    R0, #0x1A05               ; CC0: rise p1 (CS), abort
        wcfg  CC0, R0
        li    R0, #fin
        wcfg  WV0, R0
.if TS
        li    R0, #TSP
        mov   P, R0
        li    R0, #TSPRE << 8
        wcfg  TCFG, R0
        li    R0, #0x2A0C               ; CC1: tick, call
        wcfg  CC1, R0
        li    R0, #wrap
        wcfg  WV1, R0
        li    R2, #0xFFFF - TSP + TSH   ; R2 + T carries for T > TSP - TSH (just after a wrap)
        tsync
        watch #3
.else
        watch #1
.endif
.entry idle

.engine ENG_MISO
.init
        li    R0, #(SRC_SCK | (MODE12 << 5)) | (SRC_CS << 8)
        wcfg  BIND01, R0
        li    R0, #SRC_MISO << 8
        wcfg  BIND23, R0
        li    R0, #SHC
        wcfg  SHCFG, R0
        li    R0, #0x1A05
        wcfg  CC0, R0
        li    R0, #fin
        wcfg  WV0, R0
.if TS
        li    R0, #TSP
        mov   P, R0
        li    R0, #TSPRE << 8
        wcfg  TCFG, R0
        li    R0, #0x2A0C
        wcfg  CC1, R0
        li    R0, #wrap
        wcfg  WV1, R0
        li    R2, #0xFFFF - TSP + TSH   ; R2 + T carries for T > TSP - TSH (just after a wrap)
        tsync
        watch #3
.else
        watch #1
.endif
.entry idle

.code
.org ORG
idle:   @fall p1 : evt #1               ; CS low
.if TS
        mov   R0, T                     ; capture
        mov   R1, R0
tw1:    cmp   R1, #TSW                  ; CY = T >= TSW
        jnc   tq1                       ; just before a wrap: wait until its evt 62 is out
        add   R1, R2                    ; CY = T > TSP - TSH: its evt 62 may not be out yet
        jnc   tp1                       ; outside the window: TS bytes leave before the next wrap
tq1:    mov   R1, T
        jmp   tw1
tp1:    push  R0
        pushh R0
.endif
byte:   loop { @rise p0 : shin }        ; bytes pushed at wend; ends only at a full FIFO
        pull  R1                        ; FIFO full: OVR via the empty RX FIFO (the host never sends to a monitor);
        @never : nop                    ; the rest of the frame is dropped (a late re-arm would sample late), CS rise aborts
fin:    jw    part                      ; watcher 0, CS rise: abort from anywhere; mid-byte?
.if TS
        .deadline 65, tp2               ; whole bytes, no loss, outside the wrap window
.else
        .deadline 17                    ; whole bytes, no loss: idle armed <= 17 clk after jw
        .deadline 29, lost              ; loss
.endif
        jovr  lost
end:    evt   #2
.if TS
        mov   R0, T                     ; capture
        mov   R1, R0
        add   R1, R2
        jnc   tf
        evt   #61                       ; just after a wrap: this CS rise may have cut its handler (watcher 0 wins)
tf:     mov   R1, R0
tw2:    cmp   R1, #TSW                  ; CY = T >= TSW
        jnc   tq2                       ; just before a wrap: wait until its evt 62 is out
        add   R1, R2                    ; CY = T > TSP - TSH: its evt 62 may not be out yet
        jnc   tp2                       ; outside the window: TS bytes leave before the next wrap
tq2:    mov   R1, T
        jmp   tw2
tp2:    push  R0
        pushh R0
.endif
        jmp   idle
part:   evt   #3                        ; partial bits dropped
        wrst
        jnovr end
lost:   ovclr
        evt   #63                       ; full again: OVR set again, reported at the next CS rise
        jmp   end
.if TS
wrap:   evt   #62                       ; watcher 1, tick: call, the preempted instruction repeats
        ret
.endif
