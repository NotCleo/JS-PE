; swd_host.s - SWD host (ADIv5 wire protocol, probe side): transactions with ACK handling, raw bit sequences
; words: 68 | clk/SWCLK: BIT (10 as written) | max: 8.3 MHz (BIT 6, SMP 3); a target driving SWDIO BIT - 4.5 clk
;        after the rising edge at its pin is read, BIT - 1.8 clk is not (tested; BIT 10: 110 ns) | engines: 1
; pins: p0 = SWCLK (uio[3]), p1 = SWDIO (uio[1], driven or released; also the shift input, uio_in[1]); nRESET:
;       RP2350 GPIO. SWDIO needs a pull-up (Pmod) or a target that keeps it defined
; host: request byte (bit 0 = 1, as on the wire: start, APnDP, RnW, A2, A3, parity, stop, park; parity by the host)
;         read  -> ACK byte (1 OK, 2 WAIT, 4 FAULT, 7 no reply); OK: 4 data bytes (low first) + parity byte (bit 0)
;         write: + 4 data bytes + parity byte -> ACK byte; not OK: the 5 bytes are dropped (no data phase)
;       raw byte (bit 0 = 0): [1] release (sample), [4:2] n - 1, + 1 data byte -> 1 byte (the line, first bit in
;         bit 0): line reset, JTAG-to-SWD, idle cycles, TARGETSEL. WAIT retries, parities and sequences: host/debug.py
; timing: SWCLK idles high; host bits change with the falling edge (tick), SWCLK rises SMP clk later (tock) and the
;         line is sampled there (target bits: driven after the previous rising edge); turnaround = one released
;         cycle; the clock restarts per word (tsync), so all edges inside a word are exact; between words SWCLK
;         stays high longer (SWD is static)

.equ CLK_HZ, 50000000
.equ BIT,   10                  ; SWCLK period, clk (>= 6)
.equ SMP,   5                   ; rising edge after the falling edge, 2..BIT-2
.equ SHC,   0x30                ; SHCFG: LSB first, input p1
.assert (BIT >= 6) & (SMP >= 2) & (BIT - SMP >= 2), "BIT >= 6, tick/tock >= 2 clk apart"

.equ ENG,    0                  ; engine and load address: override with -D
.equ ORG,    288
.engine ENG
.pad uio3, p0
.pad uio1, p1
.init
        li    R0, #BIT - 1
        mov   P, R0
        li    R0, #BIT - SMP
        mov   C, R0
        li    R0, #9 << 8               ; BIND01: p1 = uio_in[1]
        wcfg  BIND01, R0
        p0 <- 1                         ; SWCLK high, SWDIO released
        p1 <- z
.entry cmd
.code
.org ORG
cmd:    @hrx  : pull S
        shr   R1, S                     ; CY = start bit
        jnc   raw
        mov   R1, S                     ; request
        li    R0, #SHC | 7
        call  tx                        ; 8 request bits
        li    R0, #SHC | 3
        call  rx                        ; turnaround + 3 ACK bits
        shr   S, S
        @htx  : push S                  ; ACK
        li    R0, #4
        cmp   S, #1
        jnz   nok
        tst   R1, R0                    ; RnW
        jnz   rd
        li    R0, #SHC
        call  rx                        ; turnaround
        @hrx  : pull S
        @hrx  : pullh S
        li    R0, #SHC | 15
        call  tx                        ; data 0..15
        @hrx  : pull S
        @hrx  : pullh S
        call  tx                        ; data 16..31
        @hrx  : pull S
        li    R0, #SHC
        call  tx                        ; parity
        jmp   cmd
rd:     li    R0, #SHC | 15
        call  rx
        @htx  : push S
        @htx  : pushh S
        call  rx
        @htx  : push S
        @htx  : pushh S
        li    R0, #SHC | 1
        call  rx                        ; parity + turnaround
        @htx  : push S
        jmp   cmd
nok:    tst   R1, R0                    ; WAIT, FAULT, no reply: turnaround, no data phase
        li    R0, #SHC
        call  rx
        jnz   cmd                       ; read: done
        li    R2, #5
drop:   @hrx  : pull S                  ; write: drop its data bytes
        add   R2, #-1
        jnz   drop
        jmp   cmd
raw:    @hrx  : pull S                  ; data
        shr   R1, R1                    ; CY = release; R1 = n - 1
        li    R0, #SHC
        or    R0, R1
        jc    rr
        call  tx
        jmp   rdone
rr:     call  rx
rdone:  @htx  : push S
        jmp   cmd

tx:     wcfg  SHCFG, R0                 ; drive WL bits of S
        tsync
        .deadline 5                     ; first falling edge BIT after tsync: BIT >= 6
        loop {
          @tick : p0 <- 0, p1 <- d
          @tock : p0 <- 1, shin
        }
        ret
rx:     wcfg  SHCFG, R0                 ; sample WL bits, SWDIO released
        tsync
        .deadline 5
        loop {
          @tick : p0 <- 0, p1 <- z
          @tock : p0 <- 1, shin
        }
        ret
