# On-chip bridges on the ISS (docs/spec/programs/bridge_design.md): UART sender model in, SPI controller checker out.
import bisect
import os
import re
import random
import pytest
from harness import PROG, prog, simulate, changes
from jspe_iss import Chip
from models import UartSender, spi_ctrl_check, uart_decode, I2cControllerModel, i2c_timing, i2c_violations
import test_i2c

N, RING, CODE = 32, 480, 47


def memmap():
    return {m[0]: int(m[1]) for m in re.findall(r"^\.equ\s+(\w+),\s*(\d+)", open(os.path.join(PROG, "cleo_mem.inc")).read(), re.M)}


def test_ring_matches_memmap():
    m = memmap()
    assert (m["BR_RING"], m["BR_RING_N"]) == (RING, N)
UMAX = 14                       # fastest UART bit time with every byte exact in all 4 phases, senders +-2 % (measured)


def rboot(img, phase=0, seed=0):
    # memory random before the image is loaded (ring region included): nothing may rely on zeroed memory
    c = Chip()
    r = random.Random(seed)
    for a in range(len(c.mem)):
        c.mem[a] = r.getrandbits(16)
    for e in c.eng:
        e.trace = True
    c.boot(img, phase=phase)
    return c


def us_run(data, ubit, sbit, phase=0, gap=0.0, bad_stop=(), f=1.0):
    # f: sender bit time = f * UBIT (0.98 = 2 % fast)
    c = rboot(prog("bridge_uart_spi", UBIT=ubit, BIT=sbit), phase, seed=ubit * 31 + sbit + phase)
    t0 = c.cycle
    s = UartSender(ubit * f, data, t0=t0 + 30, gap=gap, bad_stop=bad_stop)
    line = lambda t, ch: (s.level(t) << 7 | 8, 0)
    simulate(c, int(s.end) + 2000, line)
    for _ in range(400):
        if spi_idle(c):
            break
        simulate(c, 500, line)
    sck = changes(c, lambda uo, o, oe: o >> 3 & 1)
    cs = changes(c, lambda uo, o, oe: o & 1)
    mosi = changes(c, lambda uo, o, oe: o >> 1 & 1)
    win = spi_ctrl_check(sck, cs, mosi, 0, 8, sbit, sbit - 2, t0=t0)
    got = [w for x in win for w in x["mosi"]]
    assert all(len(x["mosi"]) == 1 for x in win)
    ev = c.host_out[1]
    assert not c.host_out[0] and c.eng[0].ovr == 0 and c.eng[1].ovr == 0
    return c, s, got, ev, [x["lo"] for x in win]


def spi_idle(c):
    # ring empty and CS high for the last 400 clk: every forwarded byte is out
    t, _, o, _ = c.pad_trace[-1]
    return all(c.mem[RING + k] >> 15 == 1 for k in range(N)) and o & 1 and c.cycle - t > 400


def rand_bytes(n, seed):
    r = random.Random(seed)
    return [r.getrandbits(8) for _ in range(n)]


def drops(s, ev, code):
    return [bisect.bisect_right(s.starts, t) - 1 for t, v in ev if v == 0x100 | code]


def test_us_size_and_park():
    img = prog("bridge_uart_spi")
    code = [a for a in img.words if a < RING]
    assert len(code) == CODE and sorted(a for a in img.words if a >= RING) == list(range(RING, 512))
    assert all(img.words[a] == 0x8000 for a in range(RING, 512))          # ring loaded "all free" by the image
    c = Chip()                                      # RUN with no EXEC after reset: every engine halts at word 0
    c.load_image(img)
    c.start([0, 1, 2, 3])
    simulate(c, 50)
    assert all(e.halted and not e.run for e in c.eng) and not any(c.host_out)


@pytest.mark.parametrize("ubit,sbit", [(UMAX, 6), (UMAX, 8), (50, 8), pytest.param(434, 30, marks=pytest.mark.slow)])
def test_us_rates(ubit, sbit):
    for ph in range(4):
        for f in ((0.98, 1.0, 1.02) if ubit == UMAX else (1.0,)):
            data = rand_bytes(40, ubit + sbit + ph)
            c, s, got, ev, _ = us_run(data, ubit, sbit, ph, f=f)
            assert got == data and ev == [], (ph, f, len(got))


@pytest.mark.slow
def test_us_sustained_bit6():
    # P = 140 clk = one character at UBIT 14: 400 back-to-back bytes, no drop (40 bytes would fit the ring anyway)
    for ph in range(4):
        data = rand_bytes(400, ph)
        c, s, got, ev, _ = us_run(data, UMAX, 6, ph)
        assert got == data and ev == [], ph


def test_us_max_rate_edge():
    bad = 0
    for ph in range(4):
        data = rand_bytes(40, 7 + ph)
        c, s, got, ev, _ = us_run(data, UMAX - 1, 6, ph, f=0.98)     # 2 % fast sender
        bad += got != data or ev != []
    assert bad


def ring_check(data, s, got, ev, lo, ubit):
    # reference model: a byte is dropped iff the ring is full when its start bit is checked;
    # occupancy estimate = stored before it - CS falls before then (a slot is freed just before its CS falls: +0/+1)
    d = drops(s, ev, 2)
    assert got == [x for k, x in enumerate(data) if k not in d]
    for k in range(len(data)):
        tk = s.starts[k] + 1.5 * ubit
        est = sum(1 for j in range(k) if j not in d) - bisect.bisect_left(lo, tk)
        assert (N <= est <= N + 1) if k in d else est <= N, (k, est)
    return d


@pytest.mark.parametrize("sbit,csdly", [(4, 16), (8, 16), (20, 16), (8, 30)])
def test_us_spi_byte_period(sbit, csdly):
    # ring preloaded with 32 bytes: SPI byte period = 9*BIT + 3*CSDLY + 36 (+-2 clk) in all phases
    for ph in range(4):
        c = rboot(prog("bridge_uart_spi", BIT=sbit, CSDLY=csdly), ph, seed=ph)
        for k in range(N):
            c.mem[RING + k] = k
        simulate(c, 34 * (10 * sbit + 3 * csdly + 60), lambda t, ch: (0x88, 0))
        lo = [t for t, v in changes(c, lambda uo, o, oe: o & 1) if v == 0]
        assert len(lo) == N
        assert all(abs(b - a - (9 * sbit + 3 * csdly + 36)) <= 2 for a, b in zip(lo[1:], lo[2:]))


@pytest.mark.parametrize("sbit,first", [(20, (68,)), pytest.param(12, (117,), marks=pytest.mark.slow),
                                         pytest.param(8, (306, 307), marks=pytest.mark.slow)])
def test_us_burst_before_first_drop(sbit, first):
    # back-to-back characters at UBIT 14: the first dropped byte is number `first` (~ 32 * P / (P - 140))
    for ph in range(4):
        data = rand_bytes(first[-1] + 12, sbit + ph)
        c, s, got, ev, lo = us_run(data, UMAX, sbit, ph)
        d = ring_check(data, s, got, ev, lo, UMAX)
        assert d[0] in first, (ph, d[:3])


def test_us_burst_absorbed():
    # SPI byte ~1 700 clk vs UART char 180 clk: a 32-byte burst fits the ring, nothing dropped
    data = rand_bytes(32, 1)
    c, s, got, ev, lo = us_run(data, UMAX, 200, 1)
    assert got == data and ev == []
    assert lo[-1] > s.end + 20 * 1700           # most bytes left the ring after the burst ended


@pytest.mark.parametrize("n,ph,ubit,sbit", [(40, 0, 20, 200), (100, 3, 20, 30), (100, 1, UMAX, 30)])
def test_us_overrun(n, ph, ubit, sbit):
    data = rand_bytes(n, n + ubit)
    c, s, got, ev, lo = us_run(data, ubit, sbit, ph)
    d = ring_check(data, s, got, ev, lo, ubit)
    assert d and len(ev) == len(d)


def test_us_framing_error():
    data = [0x11, 0x22, 0x33, 0x44]
    c, s, got, ev, _ = us_run(data, 50, 8, 2, gap=50, bad_stop=(1,))
    assert got == data and drops(s, ev, 1) == [1] and len(ev) == 1


# ---- I2C target writes -> mailbox -> UART TX (bridge_i2c_uart.s)
IU_CODE = 30


def iu_run(script, ubit, phase=0, thigh=60, tlow=65, ncyc=400000):
    c = rboot(prog("bridge_i2c_uart", UBIT=ubit), phase, seed=ubit + phase)
    m = I2cControllerModel(script, thigh, tlow)
    t0 = c.cycle
    mon, hist, drv = test_i2c.i2c_run(c, m, ncyc, 0, done=m.done)
    t1 = c.cycle
    assert m.done()
    simulate(c, 25 * ubit, lambda t, ch: (0x08, 0x60))         # bus idle; mailbox + shifter drain (2 characters)
    txd = changes(c, lambda uo, o, oe: uo & 1, init=1)
    frames = uart_decode(txd, ubit)
    for st, v, pok, sok in frames:                               # edges of a frame exactly on its bit grid
        assert sok and all((t - st) % ubit == 0 for t, _ in txd if st <= t <= st + 9 * ubit)
    assert not any(c.host_out)
    return m, mon, hist, [f[1] for f in frames], t1 - t0


def writes(data, addr=0x84):
    return [("S",), ("W", addr)] + [("W", d) for d in data] + [("P",)]


def scl_low_max(hist):
    lo, best = None, 0
    for t, scl, sda in hist:
        if not scl and lo is None:
            lo = t
        elif scl and lo is not None:
            best, lo = max(best, t - lo), None
    return best


def test_iu_size_and_park():
    img = prog("bridge_i2c_uart")
    mbox = img.label("mbox")
    assert mbox == memmap()["BR_MBOX"] and len([a for a in img.words if a != mbox]) == IU_CODE
    c = Chip()
    c.load_image(img)
    c.start([0, 1, 2, 3])
    simulate(c, 50, lambda t, ch: (0x08, 0x60))
    assert all(e.halted and not e.run for e in c.eng)


@pytest.mark.parametrize("thigh,tlow,rate", [(60, 65, 0.0388), (250, 250, 0.00996)])
def test_iu_rate_i2c_limited(thigh, tlow, rate):
    # UART 1 Mbaud is faster than I2C: no stretch beyond the ACK, rate set by the bus (kB per us)
    for ph in range(4):
        data = rand_bytes(12, 3 + ph)
        m, mon, hist, got, dt = iu_run(writes(data), 50, ph, thigh, tlow)
        assert got == data and m.acks == [0] * 13
        assert len(data) / (dt / 50.0) == pytest.approx(rate, abs=0.0005)
        assert not i2c_violations(i2c_timing(hist), "Fm" if thigh < 100 else "Sm")


@pytest.mark.parametrize("ubit", [434, pytest.param(1736, marks=pytest.mark.slow)])
def test_iu_stretch_while_uart_slower(ubit):
    # UART slower than I2C: SCL held low until the mailbox empties, every byte delivered at the UART pace
    data = rand_bytes(10, ubit)
    m, mon, hist, got, dt = iu_run(writes(data), ubit, 1)
    assert got == data and m.acks == [0] * 11
    assert dt >= (len(data) - 2) * 10 * ubit                       # paced by the UART
    assert 10 * ubit - 9 * 125 <= scl_low_max(hist) <= 10 * ubit       # stretch = one character - one I2C byte
    assert not i2c_violations(i2c_timing(hist, low_max=200), "Fm")


def test_iu_nack_read_and_other_address():
    data = [0x5A, 0xA5]
    script = [("S",), ("W", 0x85), ("P",), ("S",), ("W", 0x86), ("W", 0x11), ("P",)] + writes(data)
    m, mon, hist, got, dt = iu_run(script, 50, 2)
    assert m.acks == [1, 1, 1, 0, 0, 0] and got == data


def test_iu_repeated_start_and_stop():
    a, b, c_, d = 0x10, 0x20, 0x30, 0x40
    script = [("S",), ("W", 0x84), ("W", a), ("W", b), ("S",), ("W", 0x84), ("W", c_), ("P",)] + writes([d])
    m, mon, hist, got, dt = iu_run(script, 434, 3)
    assert got == [a, b, c_, d] and m.acks == [0] * 7
