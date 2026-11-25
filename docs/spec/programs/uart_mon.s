; uart_mon.s - passive UART monitor with timestamps: two lines (both directions) on two engines, one shared code copy
;   (monitor_design.md; without timestamps use two uart_rx.s copies, which drive no pad either)
; words: 30 | clk/bit: BIT (integer) | max: back to back <= 156 kbaud (115200 tested); 1 Mbaud with >= 2.7 idle bits
;        per character; simulation (ISS) | engines: 2
; pins (inputs only, no .pad: every pad stays en = 0): p0 = line (ui_in[PIN_A] on ENG_A, ui_in[PIN_B] on ENG_B)
; format: 8N1, LSB first; INV = 1 for an inverted line
; host, per engine stream, one record per character: [evt 63] [evt 60] lo, hi, byte [evt 1]. lo, hi = timer value T
;       captured 1 clk after the start edge is seen (T counts down from 0xFFFF, 1 clk per step); evt 62 = timer wrap.
;       time = W * 65536 + 0xFFFF - T, W = evt 62 count before the hi byte, minus 1 when evt 60 leads the record
;       (a wrap came between the start edge and the record). evt 1 = framing error (stop bit low; a break is 0x00 +
;       evt 1), evt 63 = LOST (OVR: an entry dropped at a full FIFO or an edge missed since the previous record): the
;       host drops the bytes of an unfinished record there. Lost entries may include evt 62/60: after evt 63 the
;       time base restarts (the wrap count may be short); records keep their order and data.
;       The host never sends data to a monitor engine.
; timing: samples at T = capture - BIT/2 - k * BIT by tock (C), not by a restarted timer, so T keeps counting.
;       The record is pushed after the stop sample; near a wrap (T < TSW or > 0xFFFF - TSH) it first waits until
;       that wrap's evt 62 is out (watcher 1 call), so the record order is exact.
; rule: next start edge >= 160 clk (3.2 us) after the stop sample (record push + wrap wait <= 80 clk); back to back
;       (one stop bit) that is BIT >= 320 clk. ISS sweep over every wrap offset: data wrong at 105, timestamps off by a
;       wrap at 130, exact from 150. The wrap call delays one sample per 65536 clk by <= 12 clk: BIT / 2 >= 12 + drift.
;       A character must be shorter than 65536 clk (>= 7.7 kbaud)

.equ CLK_HZ, 50000000
.equ BAUD,   115200
.equ BIT,    CLK_HZ / BAUD          ; clk per bit (integer; error <= 0.5 clk per bit)
.equ INV,    0
.equ PIN_A,  7                      ; ui_in pad of line A (uart_rx default RXD)
.equ PIN_B,  6                      ; ui_in pad of line B (free in every default map)
.equ ENG_A,  0                      ; engines and load address: override with -D
.equ ENG_B,  1
.equ ORG,    400
.equ TSW,    64                     ; read-to-record latency guard before a wrap (clk; path <= 44 in the ISS)
.equ TSH,    16                     ; wrap call latency guard after a wrap (clk)

.engine ENG_A
.init
        li    R0, #PIN_A | (INV << 5)
        wcfg  BIND01, R0
        li    R0, #0x17                 ; SHCFG: 8 bits, LSB first, input p0
        wcfg  SHCFG, R0
        li    R0, #0xFFFF
        mov   P, R0
        mov   C, R0
        li    R0, #0x2A0C               ; CC1: tick, call
        wcfg  CC1, R0
        li    R0, #wrap
        wcfg  WV1, R0
        li    R2, #BIT
        tsync
        watch #2
.entry rx

.engine ENG_B
.init
        li    R0, #PIN_B | (INV << 5)
        wcfg  BIND01, R0
        li    R0, #0x17
        wcfg  SHCFG, R0
        li    R0, #0xFFFF
        mov   P, R0
        mov   C, R0
        li    R0, #0x2A0C
        wcfg  CC1, R0
        li    R0, #wrap
        wcfg  WV1, R0
        li    R2, #BIT
        tsync
        watch #2
.entry rx

.code
.org ORG
rx:     @fall p0 : mov R0, T            ; start edge: capture
        shr   R1, R2
        mov   C, R0
        sub   C, R1                     ; middle of the start bit
        @tock : jhi p0, rx              ; high: glitch
bits:   sub   C, R2
        @tock : shin                    ; data bit
        jw    bits
        sub   C, R2
        @tock : smp                     ; stop bit into B
        jnovr tw
        ovclr
        evt   #63                       ; before the record: the host drops a partial record and resyncs here
tw:     mov   R1, T                     ; one read decides the wait and evt 60; the record is out <= TSW clk later
        cmp   R1, #TSW                  ; CY = T >= TSW
        jnc   tw                        ; a wrap within TSW clk: wait until it and its evt 62 are past
        add   R1, #TSH                  ; CY = T > 0xFFFF - TSH: evt 62 of that wrap may not be out yet
        jc    tw
        add   R1, #-TSH
        cmp   R1, R0                    ; CY = T >= capture: a wrap since the start edge
        jnc   rec
        evt   #60
rec:    push  R0
        pushh R0
        push  S
        jb    rx
        evt   #1                        ; stop bit low
        jmp   rx
wrap:   evt   #62                       ; watcher 1, tick: call, the preempted instruction repeats
        ret
