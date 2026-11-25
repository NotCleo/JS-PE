# docs/spec/BACKLOG.md §3.1, 3.3, 3.4 measurements on the ISS: software DMA, timer capture, on-chip bridges.
import os
import random
import pytest
from harness import PROG, assemble, boot, simulate, changes
from jspe_asm import assemble_text
from models import UartSender, uart_decode, I2cControllerModel
import test_i2c

MHZ = 50.0
MAPS = {"default": None, "two": "0, 1, 0, 2", "solo": "0, 0, 0, 0"}

# ---- 3.1 software DMA
FILL = """
.engine 0
.init
        li    R0, #0x0FF
.entry  fill
.code
.org 480
fill:   @hrx : pull  R1
        @hrx : pullh R1
        st    R1, [R0+1]!
        jmp   fill
"""
DRAIN = """
.engine 0
.init
        li    R0, #0x0FF
.entry  drain
.code
drain:  ld    R1, [R0+1]!
        @htx : push  R1
        @htx : pushh R1
        jmp   drain
"""
SHIFT = """
.equ BIT, 12
.equ ARM, 6
.engine 0
.pad uo0, p0
.init
        li    R0, #BIT - 1
        mov   P, R0
        li    R0, #0x0F
        wcfg  SHCFG, R0
        li    R0, #0x0FF
        p0 <- 0
.entry  go
.code
go:     tsync
word:   ld    S, [R0+1]!
        loop { @tick : p0 <- out }
        .assume armed ARM
        jmp   word
"""


def slots(src, m):
    return (".slots %s\n" % MAPS[m] if MAPS[m] else "") + src


def fill_word(k):
    return k & 0xFF | ((k >> 8) ^ 0x5A) << 8


def fill_rate(m, words=200):                 # buffer 0x100.. stays below the code at 480
    c = boot(assemble_text(slots(FILL, m)))
    for k in range(words):
        c.host_in[0] += [k & 0xFF, (k >> 8) ^ 0x5A]
    t0, last = c.cycle, 0x0FF + words
    simulate(c, 20000, until=lambda ch: ch.mem[last] == fill_word(words - 1))
    assert [c.mem[0x100 + k] for k in range(words)] == [fill_word(k) for k in range(words)]
    return words / ((c.cycle - t0) / MHZ)


def drain_rate(m, words=200):
    c = boot(assemble_text(slots(DRAIN, m)), trace=False)
    for k in range(words):
        c.mem[0x100 + k] = k * 0x0101 & 0xFFFF
    t0 = c.cycle
    simulate(c, 20000, until=lambda ch: len(ch.host_out[0]) >= 2 * words)
    assert [v for _, v in c.host_out[0]] == [k & 0xFF for k in range(words) for _ in (0, 1)]
    return words / ((c.cycle - t0) / MHZ)


def shift_run(m, bit, words=24):
    c = boot(assemble_text(slots(SHIFT, m), {"BIT": str(bit), "ARM": str(max(bit - 6, 0))}))
    pat = [(0xA5C3 + 0x1111 * k) & 0xFFFF for k in range(words)]
    for k, w in enumerate(pat):
        c.mem[0x100 + k] = w
    simulate(c, bit * 16 * words + 400)
    ch = changes(c, lambda uo, a, b: uo & 1)
    lv = lambda t: ([v for tt, v in ch if tt <= t] or [0])[-1]
    want = [w >> (15 - i) & 1 for w in pat[:words - 2] for i in range(16)]
    got = [lv(ch[0][0] + bit // 2 + k * bit) for k in range(len(want))]
    return got == want, c.eng[0].late == 0 and c.eng[0].ovr == 0


@pytest.mark.parametrize("m,fill,drain", [("default", 2.50, 2.50), ("two", 5.00, 5.00), ("solo", 5.56, 5.00)])
def test_dma_fifo_rates(m, fill, drain):
    assert fill_rate(m) == pytest.approx(fill, abs=0.03)
    assert drain_rate(m) == pytest.approx(drain, abs=0.03)


@pytest.mark.parametrize("m,bmin", [("default", 12), ("two", 6), ("solo", 6)])
def test_dma_mem_to_pin_min_bit(m, bmin):
    assert shift_run(m, bmin) == (True, True)
    data, exact = shift_run(m, bmin - 1)
    assert data and not exact                  # still correct one clk faster, but with a late edge


def neighbour_trace(m, eng, phase, dma):
    img = assemble([os.path.join(PROG, "uart_tx.s")], {"uart_tx.s:ENG": str(eng)})
    if dma:
        d = assemble_text(slots(FILL, m))
        img.words.update(d.words)
        img.engines.update(d.engines)
    if MAPS[m]:
        img.slotmap = assemble_text(".slots %s\n.engine 0\n.code\nnop\n" % MAPS[m]).slotmap
    c = boot(img, phase)
    if dma:
        for k in range(200):
            c.host_in[0] += [k & 0xFF, 0x33]
    data = [random.Random(5).getrandbits(8) for _ in range(20)]
    c.host_in[eng] += data
    simulate(c, 50 * 10 * 22)
    if dma:
        assert c.mem[0x100 + 199] == 199 | 0x33 << 8
    tr = [(t - c.t_run, uo & 1) for t, uo, _, _ in c.pad_trace if t >= c.t_run]
    edges = [x for x, y in zip(tr, [(0, 1)] + tr) if x[1] != y[1]]
    assert [d[1] for d in uart_decode(edges, 50)] == data
    return tr


@pytest.mark.parametrize("m,eng", [("default", 1), ("default", 3), ("two", 1), ("two", 2)])
def test_dma_no_effect_on_neighbour(m, eng):
    for ph in range(4):
        a = neighbour_trace(m, eng, ph, True)
        assert a == neighbour_trace(m, eng, ph, False)


# ---- 3.3 timer capture
CAP = """
.equ PRE, 0
.engine 0
FILT
.init
        li    R0, #0xFFFF
        mov   P, R0
        li    R0, #PRE << 8
        wcfg  TCFG, R0
        tsync
.entry  m
.code
m:      @rise p0 : mov R1, T
        @fall p0 : mov R2, T
        @htx : push  R1
        @htx : pushh R1
        @htx : push  R2
        @htx : pushh R2
        jmp   m
"""
CAPMEM = """
.engine 0
.init
        li    R0, #0xFFFF
        mov   P, R0
        tsync
        li    R0, #0x0FF
.entry  m
.code
m:      @rise p0 : mov R1, T
        st    R1, [R0+1]!
        @fall p0 : mov R1, T
        st    R1, [R0+1]!
        jmp   m
"""


def wave(edges):
    def f(t, ch):
        lv = 0
        for te, v in edges:
            if te > t:
                break
            lv = v
        return 0x08 | lv, 0
    return f


def run_edges(src, edges, n, phase, defs=None):
    c = boot(assemble_text(src, defs), phase)
    t0 = c.cycle
    simulate(c, n, wave([(t0 + a, v) for a, v in edges]))
    return c


def capture(highs, lows, pre=0, filt=False, phase=0, frac=0.3):
    edges, want, t = [], [], 50.0 + frac
    for h, l in zip(highs, lows):
        edges += [(t, 1), (t + h, 0)]
        want.append((t, t + h))
        t += h + l
    src = CAP.replace("FILT", ".filter ui0" if filt else "")
    c = run_edges(src, edges, int(t) + 300, phase, {"PRE": str(pre)})
    b = [v for _, v in c.host_out[0]]
    caps = [(b[k] | b[k + 1] << 8, b[k + 2] | b[k + 3] << 8) for k in range(0, len(b) - 3, 4)]
    div = (1, 16, 256)[pre]
    werr = [((r - f) & 0xFFFF) * div - (tf - tr) for (r, f), (tr, tf) in zip(caps, want)]
    perr = [((caps[k][0] - caps[k + 1][0]) & 0xFFFF) * div - (want[k + 1][0] - want[k][0]) for k in range(len(caps) - 1)]
    ok = len(caps) == len(want) and c.eng[0].ovr == 0 and c.eng[0].late == 0
    return ok, werr, perr


@pytest.mark.parametrize("pre,filt,n", [(0, False, 20), pytest.param(1, False, 5, marks=pytest.mark.slow),
                                        pytest.param(1, True, 5, marks=pytest.mark.slow)])
def test_capture_width_period(pre, filt, n):
    r = random.Random(2)
    k = (1, 16)[pre]
    hs = [r.uniform(5, 3000) * k for _ in range(n)]
    ls = [r.uniform(200, 3000) * k for _ in range(n)]
    for ph in range(4):
        ok, werr, perr = capture(hs, ls, pre, filt, ph, r.random())
        assert ok
        assert max(map(abs, werr + perr)) <= k, (min(werr), max(werr), min(perr), max(perr))


def test_capture_shortest_phases():
    for ph in range(4):
        assert capture([1.5] * 10, [400] * 10, phase=ph)[0]
        assert capture([300] * 10, [22.5] * 10, phase=ph)[0]
    assert not all(capture([300] * 10, [20.5] * 10, phase=ph)[0] for ph in range(4))


def mem_timestamps_exact(sp):
    for ph in range(4):
        edges = [(50.4 + k * sp, 1 - (k & 1)) for k in range(30)]
        c = run_edges(CAPMEM, edges, int(50.4 + 30 * sp) + 100, ph)
        ts = [c.mem[0x100 + k] for k in range(30)]
        if None in ts or c.eng[0].late or any((ts[k] - ts[k + 1]) & 0xFFFF != sp for k in range(29)):
            return False
    return True


def test_capture_to_memory_min_spacing():
    assert mem_timestamps_exact(14) and not mem_timestamps_exact(13)


# ---- 3.4 on-chip bridges (prototypes; the programs proper are BACKLOG rank 1)
BRIDGE_US = """
; UART RX (E1, ui_in[7]) -> mailbox + G0 -> SPI controller TX (E0, mode 0, MSB first, one byte per CS)
.equ UBIT, 50
.equ BIT, 8
.equ SMP, 6
.equ CSDLY, 16
.equ MB, 500
.engine 1
.init
        li    R0, #UBIT - 1
        mov   P, R0
        li    R0, #UBIT - UBIT / 2
        mov   C, R0
        li    R0, #0x17
        wcfg  SHCFG, R0
        li    R0, #7
        wcfg  BIND01, R0
        li    R2, #MB
.entry rx
.code
.org 300
rx:     @fall p0 : tsync
        @tock : jhi p0, rx
        loop { @tock : shin }
        st    S, [R2]
        sset  #0
stop:   @tock : jlo p0, ferr
        jmp   rx
ferr:   evt   #1
        jmp   rx
.engine 0
.pad uio3, p0
.pad uio0, p1
.pad uio1, p2
.init
        li    R0, #BIT - 1
        mov   P, R0
        li    R0, #BIT - SMP
        mov   C, R0
        li    R0, #0x67
        wcfg  SHCFG, R0
        li    R0, #16 << 8              ; p1 input = G0, p1 output stays CS
        wcfg  BIND01, R0
        li    R0, #0xA01                ; CC0 = level of p1 = G0
        wcfg  CC0, R0
        li    R1, #MB
        li    R2, #CSDLY - 1
        p0 <- 0, p1 <- 1
        p2 <- 0
.entry xfer
.code
.org 340
xfer:   @c0   : nop
        ld    S, [R1]
        sclr  #0
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
"""
BRIDGE_IU = """
; I2C target write (E0, uio6 SCL / uio5 SDA) -> mailbox + G0, SCL stretched while full -> UART TX (E1, uo0)
.equ ADDR, 0x42
.equ THD, 13
.equ TSU, 20
.equ UBIT, 50
.equ MB, 500
.engine 0
.pad uio6, p0, od
.pad uio5, p1, od
.init
        li    R0, #14 | (13 << 8)
        wcfg  BIND01, R0
        li    R0, #16                   ; p2 = G0
        wcfg  BIND23, R0
        li    R0, #0x27
        wcfg  SHCFG, R0
        li    R0, #0x4800
        wcfg  TCFG, R0
        li    R0, #THD - 1
        mov   P, R0
        li    R0, #255
        mov   C, R0
        li    R0, #0x1809
        wcfg  CC0, R0
        li    R0, #start
        wcfg  WV0, R0
        li    R0, #0x1805
        wcfg  CC1, R0
        li    R0, #idle
        wcfg  WV1, R0
        li    R0, #ADDR
        li    R2, #TSU - 1
        watch #3
.entry idle
.code
.org 144
idle:   p1 <- 1, p0 <- 1
        @never : nop
start:  p1 <- 1, p0 <- 1
        wrst
        loop { @rise p0 : shin }
        shr   R1, S
        cmp   R1, R0
        jnz   idle
        li    R1, #MB
        @fall p0 : p0 <- 0
        @tick : p1 <- 0
        tset  R2
        jb    idle                      ; reads not bridged
wr:     @tick : p0 <- 1
        @tick : p1 <- 1
        loop { @rise p0 : shin }
        @fall p0 : p0 <- 0
        @tick : p1 <- 0
full:   jhi   p2, full                  ; mailbox still full: SCL stays low
        st    S, [R1]
        sset  #0
        tset  R2
        jmp   wr
.engine 1
.pad uo0, p0
.init
        li    R0, #UBIT - 1
        mov   P, R0
        li    R0, #0x17
        wcfg  SHCFG, R0
        li    R0, #16 << 8              ; p1 input = G0
        wcfg  BIND01, R0
        li    R0, #0xA01
        wcfg  CC0, R0
        li    R1, #MB
        p0 <- 1
.entry uidle
.code
.org 300
uidle:  @c0   : nop
        tsync
        ld    S, [R1]
        sclr  #0
        @tick : p0 <- 0
        loop { @tick : p0 <- out }
        @tick : p0 <- 1
        jmp   uidle
"""


def us_image(ubit, sbit):
    return assemble_text(BRIDGE_US, {"UBIT": str(ubit), "BIT": str(sbit), "SMP": str(max(3, sbit - 2))})


def spi_trace(c):
    out, cur, cs, last = [], [], [], 0
    for t, uo, o, oe in c.pad_trace:
        sck, mosi = o >> 3 & 1, o >> 1 & 1
        cs.append((t, o & 1))
        if o & 1 == 0 and sck and not last:
            cur.append(mosi)
            if len(cur) == 8:
                out.append(int("".join(map(str, cur)), 2))
                cur = []
        last = sck
    return out, cs


def uart_to_spi(ubit, sbit, phase, n=40):
    c = boot(us_image(ubit, sbit), phase)
    data = [random.Random(ubit * 7 + sbit + phase).getrandbits(8) for _ in range(n)]
    s = UartSender(ubit, data, t0=c.cycle + 30)
    simulate(c, int(s.end) + 3000, lambda t, ch: (s.level(t) << 7 | 8, 0))
    return spi_trace(c)[0] == data and c.eng[0].ovr == 0 and c.eng[1].ovr == 0


def test_bridge_uart_spi_size():
    img = us_image(14, 8)
    assert len([a for a in img.words if a < 500]) == 23


@pytest.mark.parametrize("sbit", [8, 6])
def test_bridge_uart_spi_max_rate(sbit):
    assert all(uart_to_spi(14, sbit, ph) for ph in range(4))     # 3.57 Mbaud = 357 kB/s
    assert all(uart_to_spi(50, sbit, ph) for ph in range(4))     # 1 Mbaud
    assert not all(uart_to_spi(13, sbit, ph) for ph in range(4))


@pytest.mark.parametrize("sbit,extra", [(6, 44), (8, 44), (20, 56)])
def test_bridge_spi_cs_low(sbit, extra):
    c = boot(us_image(50, sbit))
    s = UartSender(50, [0x5A, 0xC3], t0=c.cycle + 30)
    simulate(c, int(s.end) + 2000, lambda t, ch: (s.level(t) << 7 | 8, 0))
    got, cs = spi_trace(c)
    fe = [t for (t, v), (_, v0) in zip(cs[1:], cs) if v == 0 and v0 == 1]
    re = [t for (t, v), (_, v0) in zip(cs[1:], cs) if v == 1 and v0 == 0]
    assert got == [0x5A, 0xC3] and re[0] - fe[0] == 8 * sbit + extra


def i2c_to_uart(ubit, phase, n=12, thigh=60, tlow=65):
    img = assemble_text(BRIDGE_IU, {"UBIT": str(ubit)})
    c = boot(img, phase)
    data = [random.Random(3 + phase).getrandbits(8) for _ in range(n)]
    m = I2cControllerModel([("S",), ("W", 0x84)] + [("W", d) for d in data] + [("P",)], thigh, tlow)
    t0 = c.cycle
    test_i2c.i2c_run(c, m, 400000, 0, done=m.done)
    t1 = c.cycle
    simulate(c, 25 * ubit)
    txd, last = [], 1
    for t, uo, _, _ in c.pad_trace:
        if uo & 1 != last:
            last = uo & 1
            txd.append((t, last))
    ok = [d[1] for d in uart_decode(txd, ubit)] == data and m.acks == [0] * (n + 1)
    return ok, n / ((t1 - t0) / MHZ), img


@pytest.mark.parametrize("ubit", [50, 434, pytest.param(1736, marks=pytest.mark.slow)])
def test_bridge_i2c_uart(ubit):
    for ph in range(2):
        ok, rate, img = i2c_to_uart(ubit, ph)
        assert ok
        assert len([a for a in img.words if a < 500]) == 32
        if ubit == 50:
            assert rate == pytest.approx(0.0388, abs=0.0005)          # 38.8 kB/s, I2C side limits
        else:
            assert rate < 1.25 * MHZ / (10 * ubit)                    # paced by the UART: SCL stretched
