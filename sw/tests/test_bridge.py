# On-chip bridges on the ISS (docs/spec/programs/bridge_design.md): UART sender model in, SPI controller checker out.
import bisect
import random
import pytest
from harness import prog, boot, simulate, changes
from jspe_iss import Chip
from models import UartSender, spi_ctrl_check

N, RING, CODE = 32, 480, 47
UMAX = 14                       # fastest UART bit time with every byte exact in all 4 phases (measured)


def us_run(data, ubit, sbit, phase=0, gap=0.0, bad_stop=()):
    c = boot(prog("bridge_uart_spi", UBIT=ubit, BIT=sbit), phase)
    t0 = c.cycle
    s = UartSender(ubit, data, t0=t0 + 30, gap=gap, bad_stop=bad_stop)
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
    c = Chip()                                      # RUN with no EXEC after reset: every engine halts at word 0
    c.load_image(img)
    c.start([0, 1, 2, 3])
    simulate(c, 50)
    assert all(e.halted and not e.run for e in c.eng) and not any(c.host_out)


@pytest.mark.parametrize("ubit,sbit", [(UMAX, 6), (UMAX, 8), (50, 8), pytest.param(434, 30, marks=pytest.mark.slow)])
def test_us_rates(ubit, sbit):
    for ph in range(4):
        data = rand_bytes(40, ubit + sbit + ph)
        c, s, got, ev, _ = us_run(data, ubit, sbit, ph)
        assert got == data and ev == [], (ph, len(got))


def test_us_max_rate_edge():
    bad = 0
    for ph in range(4):
        data = rand_bytes(40, 7 + ph)
        c, s, got, ev, _ = us_run(data, UMAX - 1, 6, ph)
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
