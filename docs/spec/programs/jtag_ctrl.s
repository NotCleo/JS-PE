; jtag_ctrl.s - JTAG controller (IEEE 1149.1 probe side): TMS walks and IR/DR scans streamed from the host
; words: 23 | clk/TCK: BIT (12 as written) | max: 6.25 MHz (BIT 8, SMP 6: the TAP has (SMP - 3) clk - pad to drive TDO);
;        4.17 MHz symmetric (BIT 12, SMP 6) | engines: 1
; pins: p0 = TCK (uio[3]), p1 = TMS (uio[0]), p2 = TDI (uio[1]), p3 = TDO (uio[2], shift input). TRST: RP2350 GPIO
; host: [header, bits] per command, one byte back (TDO, first bit in bit 0). header < 0x20: [0] level held on the
;       other pin, [1] bits go to TMS (else TDI), [4:2] n - 1 (1..8 bits, LSB first). host/debug.py builds TAP walks
;       and scans (n - 1 bits with TMS 0, the last with TMS 1)
; timing: TCK idles low; TMS/TDI change with the falling edge (tick), TCK rises SMP clk later (tock) and TDO is
;         sampled there (synchronizer: TDO must be valid (SMP - 3) clk - 10 ns after the falling edge at the pad);
;         the bit timer restarts per command, so all edges inside a command are exact; first falling edge BIT after
;         `tsync` (armed <= 5 clk: BIT >= 6); tick/tock >= 2 clk apart inside the loop (held pair)
; gap: TCK low between commands for ~60 clk + host bytes (TCK may stop low, IEEE 1149.1)

.equ CLK_HZ, 50000000
.equ BIT,   12                  ; TCK period, clk (>= 6)
.equ SMP,   6                   ; rising edge after the falling edge, 2..BIT-2 (TDO reads need SMP >= 4)

.assert (BIT >= 6) & (SMP >= 2) & (BIT - SMP >= 2), "BIT >= 6, tick/tock >= 2 clk apart"

.equ ENG,    0                  ; engine and load address: override with -D
.equ ORG,    232
.engine ENG
.pad uio3, p0
.pad uio0, p1
.pad uio1, p2
.init
        li    R0, #BIT - 1
        mov   P, R0
        li    R0, #BIT - SMP
        mov   C, R0
        li    R0, #10 << 8              ; BIND23: p3 = uio_in[2] (TDO)
        wcfg  BIND23, R0
        li    R2, #0x70                 ; SHCFG base: LSB first, input p3
        p0 <- 0, p1 <- 1                ; TCK low, TMS high
        p2 <- 1
.entry cmd
.code
.org ORG
cmd:    @hrx  : pull R1                 ; header
        @hrx  : pull S                  ; bits
        shr   R1, R1                    ; CY = held level
        jc    h1
        p2 <- 0, p1 <- 0
        jmp   h
h1:     p2 <- 1, p1 <- 1
h:      shr   R1, R1                    ; CY = bits to TMS; R1 = n - 1
        or    R1, R2
        wcfg  SHCFG, R1                 ; n bits (and wrst)
        jc    tms
        tsync                           ; first falling edge one period from here
        .deadline 5                     ; loop armed <= 5 clk after tsync: BIT >= 6
        loop {
          @tick : p0 <- 0, p2 <- d      ; falling edge + TDI
          @tock : p0 <- 1, shin         ; rising edge + TDO
        }
        @tick : p0 <- 0
        @htx  : push S
        jmp   cmd
tms:    tsync
        .deadline 5
        loop {
          @tick : p0 <- 0, p1 <- d      ; falling edge + TMS
          @tock : p0 <- 1, shin
        }
        @tick : p0 <- 0
        @htx  : push S
        jmp   cmd
