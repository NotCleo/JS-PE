; jtag_tap.s - JTAG TAP emulator (IEEE 1149.1 target): 16-state TAP, IDCODE, one user DR, BYPASS, TRST
; words: 94 (TRST = 0: 91) | clk/TCK: external | max TCK: 1.39 MHz (default slot map), 2.78 MHz solo (ISS) |
;        engines: 1 per TAP (chains: one engine per TAP)
; pins: p0 = TCK (uio_in[3]), p1 = TMS (uio_in[0]), p2 = TDO out (uio[2], TDOPAD = 7: uio[7]; released outside
;       Shift-xR) with its input bound to TRST (uio_in[4], active low), p3 = TDI (uio_in[1]; TDIPIN = bind source)
; registers: IR (IRLEN 2..16 bits, captures ..01; Test-Logic-Reset selects IDCODE); IDCODE (32 bits, opcode IDC);
;       user DR (ULEN 1..16 bits, opcode USR, initial UINIT, written at Update-DR); every other opcode = BYPASS
;       (1 bit, captures 0). Opcodes IDC, USR < 256. Lengths are exact shift registers: extra bits pass through
; host: after each Update-DR of the user DR its value (2 bytes, low first; 1 byte when ULEN <= 8)
; TAP: one `@rise p0 : jhi/jlo p1` per state (TMS read when the latched edge fires); Shift-xR =
;       `loop { @rise p0 : shin }` with TDO following S[0] (changes 4-5 clk + pad after each rising edge, >= 80 ns
;       hold); IDCODE swaps S with R2 at each 16-bit boundary. Exit (rise with TMS = 1) = watcher 0, whose handler
;       shifts the last bit; TRST falling = watcher 1 (abort to Test-Logic-Reset; held low keeps the TAP there)
; rule: TCK period >= 36 clk with TMS/TDI changing at the falling edge (IEEE 1149.1 timing), i.e. high and low
;       >= 18 clk (solo slot map: period >= 18 clk). The longest work between two edges is Update-IR (IR decode into
;       P = Capture-DR routine, 12 instructions) and must arm the next state's guard before TMS changes (1.5 periods);
;       the IDCODE swap puts the next TDO bit out <= 14 clk after the 16th edge. Measured: 32 clk fails (ISS)

.equ IRLEN,  4
.equ IDC,    0xE                ; IDCODE instruction
.equ USR,    0xA                ; user DR instruction
.equ ULEN,   8
.equ UINIT,  0x5A
.equ IDCODE, 0x1C1E0001         ; bit 0 = 1 (IEEE 1149.1)
.equ TRST,   1                  ; 0 = no TRST pin (watcher 1 unused)
.equ TDIPIN, 9                  ; TDI bind source (9 = uio_in[1]; 10 = uio_in[2] for the 2nd TAP of a chain)
.equ TDOPAD, 2                  ; 2 or 7
.equ WT,     TRST * 2           ; watch mask outside Shift-xR

.assert (IRLEN >= 2) & (IRLEN <= 16) & (ULEN >= 1) & (ULEN <= 16) & (IDC < 256) & (USR < 256), "lengths, opcodes"

.equ ENG,    0                  ; engine and load address: override with -D
.equ ORG,    96
.engine ENG
.if TDOPAD == 7
.pad uio7, p2
.else
.pad uio2, p2
.endif
.init
        li    R0, #11 | (8 << 8)        ; BIND01: p0 = uio_in[3] (TCK), p1 = uio_in[0] (TMS)
        wcfg  BIND01, R0
        li    R0, #12 | (TDIPIN << 8)   ; BIND23: p2 = uio_in[4] (TRST), p3 = TDI
        wcfg  BIND23, R0
        li    R0, #0x1814               ; CC0: rise p0 & TMS, abort (exit from Shift-xR)
        wcfg  CC0, R0
.if TRST
        li    R0, #0x1502               ; CC1: TRST low, abort
        wcfg  CC1, R0
        li    R0, #tlr
        wcfg  WV1, R0
.endif
        li    R1, #UINIT                ; user DR
.entry tlr
.code
.org ORG
tlr:    p2 <- z                         ; Test-Logic-Reset: TDO released, IR = IDCODE
        watch #WT
        li    R0, #IDC
        li    R2, #capi
        mov   P, R2                     ; Capture-DR routine of the IR (decoded at Update-IR)
        li    R2, #shxd
        wcfg  WV0, R2                   ; exit handler of Shift-DR
tl:     @rise p0 : jhi p1, tl
.if TRST
        jlo   p2, tl                    ; TRST held low
.endif
rti:    @rise p0 : jlo p1, rti          ; Run-Test/Idle
sdr:    @rise p0 : jlo p1, cdr          ; Select-DR-Scan
sir:    @rise p0 : jlo p1, cir          ; Select-IR-Scan
        jmp   tlr

cdr:    mov   L, P                      ; Capture-DR
        ret
capb:   li    S, #0                     ; BYPASS: 1 bit, captures 0
        li    R2, #0x70
        wcfg  SHCFG, R2                 ; LSB first, input p3
        @rise p0 : jhi p1, x1dr         ; the capture edge
        jmp   shp
capu:   mov   S, R1                     ; user DR
        li    R2, #0x70 | (ULEN - 1)
        wcfg  SHCFG, R2
        @rise p0 : jhi p1, x1dr
shp:    p2 <- f                         ; Shift-DR: TDO follows S[0]
        watch #WT | 1
spl:    loop { @rise p0 : shin }
        jmp   spl
capi:   li    S, #IDCODE & 0xFFFF       ; IDCODE: 16 bits, swapped with R2 at each boundary
        li    R2, #0x7F
        wcfg  SHCFG, R2
        li    R2, #IDCODE >> 16
        @rise p0 : jhi p1, x1dr
sid:    p2 <- f
        watch #WT | 1
sil:    loop { @rise p0 : shin }
        mov   C, S                      ; 32-bit register = S (low) + R2 (high)
        mov   S, R2
        mov   R2, C
        jmp   sil
shxd:   shin                            ; exit edge: last bit
.if TRST
        jlo   p2, tlr                   ; TRST fell with the exit edge (watcher 0 won)
.endif
        p2 <- z
        watch #WT
        jw    x1dr                      ; the exit edge completed a 16-bit word:
        cmp   R0, #IDC
        jnz   x1dr
        mov   C, S                      ; IDCODE halves swapped here (Pause/Exit2 resume)
        mov   S, R2
        mov   R2, C
x1dr:   @rise p0 : jhi p1, udr          ; Exit1-DR
pdr:    @rise p0 : jlo p1, pdr          ; Pause-DR
x2dr:   @rise p0 : jhi p1, udr          ; Exit2-DR
        cmp   R0, #IDC                  ; back to Shift-DR
        jz    sid
        jmp   shp
udr:    cmp   R0, #USR                  ; Update-DR
        jnz   ud1
        mov   R1, S
        push  S
.if ULEN > 8
        pushh S
.endif
ud1:    @rise p0 : jlo p1, rti
        jmp   sdr

trl:    p2 <- z                         ; in reach of shxi (not a bare jmp: jump targets that are jmps fire)
        jmp   tlr
cir:    li    R2, #shxi                 ; Capture-IR: ..01
        wcfg  WV0, R2
        li    S, #1
        li    R2, #0x70 | (IRLEN - 1)
        wcfg  SHCFG, R2
        @rise p0 : jhi p1, x1ir
shir:   p2 <- f                         ; Shift-IR
        watch #WT | 1
sil2:   loop { @rise p0 : shin }
        jmp   sil2
shxi:   shin
.if TRST
        jlo   p2, trl
.endif
        p2 <- z
        watch #WT
x1ir:   @rise p0 : jhi p1, uir          ; Exit1-IR
pir:    @rise p0 : jlo p1, pir          ; Pause-IR
x2ir:   @rise p0 : jlo p1, shir         ; Exit2-IR
uir:    mov   R0, S                     ; Update-IR: P = Capture-DR routine
        li    R2, #capb
        cmp   R0, #IDC
        jnz   u1
        li    R2, #capi
u1:     cmp   R0, #USR
        jnz   u2
        li    R2, #capu
u2:     mov   P, R2
        li    R2, #shxd
        wcfg  WV0, R2
        jmp   ud1
