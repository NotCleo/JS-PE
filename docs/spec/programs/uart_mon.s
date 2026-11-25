; uart_mon.s - passive UART monitor: two lines (both directions) on two engines, one shared code copy (monitor_design.md)
; words: 12 (TS = 0), 30 (TS = 1), 49 (TS = 2) | engines: 2 | all rates simulation (ISS), senders at -2 %, 0, +2 %
; max: TS = 0: 2.63 Mbaud back to back on both lines (BIT >= 19 clk, static bound; 1, 2, 2.63 Mbaud tested)
;      TS = 1: back to back <= 156 kbaud (115200 tested); 1 Mbaud with >= 2.7 idle bits per character
;      TS = 2: 1 Mbaud back to back (1.25 Mbaud fails with a -2 % sender); timestamp on the first character after a
;              span >= 2^IDLE bits from the previous stop sample (IDLE = 1: >= 1.5 idle bits)
; pins (inputs only, no .pad: every pad stays en = 0): p0 = line (ui_in[PIN_A] on ENG_A, ui_in[PIN_B] on ENG_B)
; format: 8N1, LSB first; INV = 1 for an inverted line. The host never sends data to a monitor engine.
; TS = 0 (uart_rx timing: bit timer restarted at each start edge): per character: byte [evt 63] [evt 1];
;       evt 63 = LOST (OVR: a byte dropped at a full FIFO or an edge missed, up to this byte), evt 1 = framing error
;       (stop bit low; a break is 0x00 + evt 1)
; TS = 1, 2: timer free-running (T counts down from 0xFFFF, 1 clk per step), samples at T = capture - BIT/2 - k * BIT
;       by tock (C); evt 62 = timer wrap (watcher 1 call). Timestamp = T captured 1 clk after the start edge is seen,
;       up to 14 clk late when the edge comes during a wrap call. time = W * 65536 + 0xFFFF - T, W = evt 62 count
;       before the hi byte, minus 1 when evt 60 marks it (a wrap came between the start edge and the push).
;       Lost entries may include evt 62/60: after evt 63 the time base restarts (the wrap count may be short).
; TS = 1 record per character: [evt 63] [evt 60] lo, hi, byte [evt 1], pushed after the stop sample; near a wrap
;       (T < TSW or > 0xFFFF - TSH) it first waits until that wrap's evt 62 is out. evt 63 before the record: the
;       host drops the bytes of an unfinished record there.
;       rule: next start edge >= 160 clk (3.2 us) after the stop sample; back to back that is BIT >= 320 clk. ISS
;       sweep over every wrap offset: data wrong at 105, timestamps off by a wrap at 130, exact from 150.
; TS = 2 per character: [evt 63] [evt 58 | evt 60, lo, hi] byte [evt 1]: the timestamp (evt 58, or evt 60 = before
;       the last wrap) is pushed in a data-bit gap of a character that starts a burst, at the first gap outside the
;       wrap window; the byte goes out during the stop bit as in uart_rx. A long idle (>= 1 wrap) is seen through L
;       (set by the wrap call, cleared at each stop). rule: BIT >= 50 (the timestamp path fits one bit gap).
; all TS: the start path is two instructions after the capture (S = BIT/2 and the previous stop in R1 are prepared
;       before the edge), so a wrap call there still lets the start-middle compare be written in time:
;       BIT / 2 >= 8 + 14 clk. A character must be shorter than 65536 clk (>= 7.7 kbaud)

.equ CLK_HZ, 50000000
.equ BAUD,   115200
.equ TS,     0                      ; 0 = no timestamps (uart_rx timing), 1 = every character, 2 = first after idle
.equ IDLE,   1                      ; TS = 2: timestamp when the span from the last stop sample is >= 2^IDLE bits
.equ BIT,    CLK_HZ / BAUD          ; TS = 1, 2: clk per bit (integer; error <= 0.5 clk per bit)
.equ PRE,    (CLK_HZ / BAUD > 65536) + (CLK_HZ / BAUD > 16 * 65536)  ; TS = 0: timer prescale as uart_rx
.equ DIV,    1 << (4 * PRE)
.equ BIT0,   CLK_HZ / (BAUD * DIV)
.equ FRAC,   (CLK_HZ % (BAUD * DIV)) * 256 / (BAUD * DIV)
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
.if TS
        li    R0, #0xFFFF
        mov   P, R0
        mov   C, R0
        li    R0, #0x2A0C               ; CC1: tick, call
        wcfg  CC1, R0
        li    R0, #wrap
        wcfg  WV1, R0
        li    R2, #BIT
        li    R1, #1
        mov   L, R1                     ; TS = 2: the first character is timestamped
        mov   R1, C
        shr   S, R2                     ; S = BIT/2 (start path)
        tsync
        watch #2
.else
        li    R0, #BIT0 - 1
        mov   P, R0
        li    R0, #BIT0 - BIT0 / 2
        mov   C, R0                     ; tock = middle of each bit
.if FRAC | PRE
        li    R0, #FRAC | (PRE << 8)    ; TCFG: mean bit time = BIT0 + FRAC/256 steps, prescale
        wcfg  TCFG, R0
.endif
.endif
.entry rx

.engine ENG_B
.init
        li    R0, #PIN_B | (INV << 5)
        wcfg  BIND01, R0
        li    R0, #0x17                 ; SHCFG: 8 bits, LSB first, input p0
        wcfg  SHCFG, R0
.if TS
        li    R0, #0xFFFF
        mov   P, R0
        mov   C, R0
        li    R0, #0x2A0C               ; CC1: tick, call
        wcfg  CC1, R0
        li    R0, #wrap
        wcfg  WV1, R0
        li    R2, #BIT
        li    R1, #1
        mov   L, R1                     ; TS = 2: the first character is timestamped
        mov   R1, C
        shr   S, R2                     ; S = BIT/2 (start path)
        tsync
        watch #2
.else
        li    R0, #BIT0 - 1
        mov   P, R0
        li    R0, #BIT0 - BIT0 / 2
        mov   C, R0                     ; tock = middle of each bit
.if FRAC | PRE
        li    R0, #FRAC | (PRE << 8)    ; TCFG: mean bit time = BIT0 + FRAC/256 steps, prescale
        wcfg  TCFG, R0
.endif
.endif
.entry rx

.code
.org ORG
.if TS == 0
rx:     @fall p0 : tsync                ; start edge restarts the bit timer
        @tock : jhi p0, rx              ; middle of the start bit: high = glitch
        loop { @tock : shin }           ; data bits
        push  S                         ; full FIFO: dropped, OVR
        jovr  lost
stop:   @tock : jlo p0, ferr            ; stop bit must be high
        .assume armed BIT0 * DIV - 10
        .deadline 1
        jmp   rx
ferr:   evt   #1
        jmp   rx
lost:   ovclr
        evt   #63                       ; after the byte it was found at
        jmp   stop
.endif
.if TS == 1
rx:     @fall p0 : mov R0, T            ; start edge: capture
        mov   C, R0
        sub   C, S                      ; middle of the start bit (S = BIT/2: two instructions, a wrap call fits)
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
        shr   S, R2                     ; S = BIT/2 for the next start
        jb    rx
        evt   #1                        ; stop bit low
        jmp   rx
.endif
.if TS == 2
rx:     @fall p0 : mov R0, T            ; start edge: capture (R1 = previous stop sample, S = BIT/2)
        mov   C, R0
        sub   C, S                      ; middle of the start bit: two instructions, a wrap call fits
        @tock : jhi p0, rx              ; high: glitch
        sub   C, R2                     ; bit 1
        sub   R1, R0                    ; span since that stop sample (mod 65536)
        mov   S, R2
.if IDLE > 0
        shl   S, S
.endif
.if IDLE > 1
        shl   S, S
.endif
.if IDLE > 2
        shl   S, S
.endif
.if IDLE > 3
        shl   S, S
.endif
        cmp   R1, S                     ; CY = span >= 2^IDLE bits
        jc    tsb
        mov   S, L
        tst   S, S
        jnz   tsb                       ; a wrap call since the last stop: the span is longer than it reads
bl:     @tock : shin                    ; data bit
        sub   C, R2
        jw    bl
stp:    jnovr st                        ; byte out during the stop bit, as uart_rx
        ovclr
        evt   #63                       ; before the byte: the host drops an unfinished timestamp
st:     push  S
        li    R1, #0
        mov   L, R1                     ; L != 0 later = a wrap call came
        mov   R1, C                     ; this stop sample
        shr   S, R2                     ; S = BIT/2 for the next start
        @tock : jlo p0, ferr            ; stop bit must be high
        jmp   rx
ferr:   evt   #1
        jmp   rx
tsb:    @tock : shin                    ; a data bit, then try the timestamp in its gap
        sub   C, R2
        mov   R1, T
        cmp   R1, #TSW
        jnc   tsn                       ; a wrap within TSW clk: try after the next bit
        add   R1, #TSH
        jc    tsn                       ; just after a wrap: its evt 62 may not be out yet
        add   R1, #-TSH
        cmp   R1, R0                    ; CY = T >= capture: a wrap since the start edge
        jc    t60
        evt   #58                       ; timestamp follows
        jmp   tpush
t60:    evt   #60                       ; timestamp follows, captured before the last wrap
tpush:  push  R0
        pushh R0
        jw    bl
        jmp   stp
tsn:    jw    tsb
        jmp   stp                       ; 8 bits all inside the window (BIT < 10): no timestamp
.endif
.if TS
wrap:   evt   #62                       ; watcher 1, tick: call, the preempted instruction repeats
        ret
.endif
