# meter.s and freq.s on the ISS (measure_design.md): accuracy, limits, glitches, wrap, prescale.
import bisect
import math
import random
import pytest
from harness import prog, boot, simulate
from models import UartSender


def wave(edges):
    ts = [e[0] for e in edges]

    def f(t, ch):
        k = bisect.bisect_right(ts, t)
        return (edges[k - 1][1] if k else 0), 0
    return f


def run_meter(phases, ph=0, frac=0.3, tail=100, skip=1, **defs):
    # phases: alternating high, low (clk) from a rise at 50 + frac; returns (phase readings, wraps per reading, chip)
    edges, t, lv = [], 50.0 + frac, 1
    for d in list(phases) + [300, 300]:          # two more edges: the last reading is pushed at the next fall
        edges.append((t, lv))
        t += d
        lv ^= 1
    edges.append((t, lv))
    t += 300
    c = boot(prog("meter", **defs), ph, trace=False)
    t0 = c.cycle
    simulate(c, int(t) + tail, wave([(t0 + a, v) for a, v in edges]))
    caps, ws, w, b = [], [], 0, []
    for _, v in c.host_out[0]:
        if v & 0x100:
            assert v == 0x101
            w += 1
            continue
        b.append(v)
        if len(b) == 2:
            caps.append(b[0] | b[1] << 8)
            ws.append(w)
            w, b = 0, []
    n = len(phases)
    return caps[skip:n + skip], ws[skip:n + skip], c   # skip: the phase before the first logical rise


def clk(v, w=0, pre=0):
    # host formula: phase in clk (None = over range); pre > 0: centre of the step
    if v == 0 and pre == 0 and w == 0:
        return None
    s = 65536 * w + 0x10000 - v if v else None
    if s is None:
        return None
    return s if pre == 0 else (s - 0.5) * 16 ** pre


def meter_errs(phases, ph=0, frac=0.3, pre=0, **defs):
    caps, ws, c = run_meter(phases, ph, frac, skip=1 - defs.get("INV", 0), PRE=pre, **defs)
    got = [clk(v, w, pre) for v, w in zip(caps, ws)]
    return [None if g is None else g - d for g, d in zip(got, phases)], len(got), c


@pytest.mark.parametrize("ext", [0, 1])
def test_meter_size(ext):
    assert len(prog("meter", EXT=ext).words) == (9 if ext else 7)


@pytest.mark.parametrize("ext", [0, 1])
def test_meter_random_phases(ext):
    r = random.Random(7 + ext)
    for ph in range(4):
        hs = [r.uniform(10 if ext else 1.5, 3000) for _ in range(12)]
        ls = [r.uniform(22, 3000) for _ in range(12)]
        phases = [x for p in zip(hs, ls) for x in p]
        e, n, c = meter_errs(phases, ph, r.random(), EXT=ext)
        assert n == len(phases) and c.eng[0].ovr == 0
        assert max(map(abs, e)) <= 1, (min(e), max(e))
        assert abs(sum(e)) <= 1                 # consecutive phases share their edge sample: the sum is exact too


def test_meter_duty_1_to_99():
    p = 2500                                    # 20 kHz: low >= 22 clk holds at 99 %
    phases = [x for d in (1, 5, 25, 50, 75, 95, 99) for x in (p * d / 100, p * (100 - d) / 100) * 2]
    for ph in range(4):
        e, n, c = meter_errs(phases, ph, 0.37 + ph * 0.13)
        assert n == len(phases) and max(map(abs, e)) <= 1


def ok_phases(phases, ext, rel, inv=0):
    for ph in range(4):
        for fr in (0.1, 0.5, 0.9):
            e, n, c = meter_errs(phases, ph, fr, EXT=ext, INV=inv)
            if n < len(phases) or any(abs(x) > 1 for x in e if x is not None) or None in e:
                return False
    return True


@pytest.mark.parametrize("ext,hmin,lmin", [(0, 1, 22), (1, 10, 14)])
def test_meter_shortest_phases(ext, hmin, lmin):
    assert ok_phases([hmin, 300] * 6, ext, 1) and ok_phases([300, lmin] * 6, ext, 1)
    assert not ok_phases([300, lmin - 1] * 6, ext, 1)
    if ext:
        assert not ok_phases([hmin - 1, 300] * 6, ext, 1)


def test_meter_inverted_short_lows():
    # INV = 1: the pin's high is the logical low; physical low >= 1 clk, physical high >= 22 clk
    assert ok_phases([300, 1] * 6, 0, 1, inv=1) and ok_phases([22, 300] * 6, 0, 1, inv=1)
    assert not ok_phases([21, 300] * 6, 0, 1, inv=1)
    assert not ok_phases([300, 1] * 6, 0, 1)


def test_meter_glitches():
    # a 3-clk high glitch is a pulse of its own; a 5-clk low glitch (< 22) reads below 22 (invalid by rule);
    # every other phase stays exact (the timer restarts at the true edge)
    phases = [500, 500, 500, 200, 3, 297, 400, 5, 95, 500, 500, 500]
    for ph in range(4):
        e, n, c = meter_errs(phases, ph, 0.21 + 0.2 * ph)
        caps, _, _ = run_meter(phases, ph, 0.21 + 0.2 * ph)
        assert n == len(phases)
        assert all(abs(x) <= 1 for k, x in enumerate(e) if k != 7)
        assert 1 <= 0x10000 - caps[7] < 22


def test_meter_over_range():
    e, n, c = meter_errs([65000, 70000, 300, 300])
    assert abs(e[0]) <= 1 and e[1] is None and abs(e[2]) <= 1 and abs(e[3]) <= 1


@pytest.mark.parametrize("pre,lo,hi", [(1, 300, 20000), (2, 3000, 30000)])
def test_meter_prescale(pre, lo, hi):
    r = random.Random(pre)
    div = 16 ** pre
    phases = [r.uniform(lo, hi) for _ in range(6)]
    for ph in (0, 2):
        e, n, c = meter_errs(phases, ph, r.random(), pre=pre)
        assert n == len(phases) and max(map(abs, e)) <= div / 2 + 1, e


@pytest.mark.slow
def test_meter_ext_long_phases():
    r = random.Random(11)
    phases = [r.uniform(70000, 200000), r.uniform(300, 3000), r.uniform(300, 3000), r.uniform(70000, 200000), 500]
    for ph in range(4):
        e, n, c = meter_errs(phases, ph, r.random(), EXT=1)
        assert n == len(phases) and max(map(abs, e)) <= 1, e


@pytest.mark.slow
def test_meter_ext_wrap_window():
    # an edge 0..13 clk after a wrap is captured after the wrap call: error -12 .. +13.5 clk; outside it exact
    worst = {}
    for d in (-1, 0, 0.5, 4, 8, 12, 14, 20):
        for ph in range(4):
            for w in "hl":
                L = 65536 + d
                phases = [L, 300, 300, 300] if w == "h" else [300, L, 300, 300]
                e, n, c = meter_errs(phases, ph, 0.2, EXT=1)
                assert n == len(phases) and None not in e
                worst.setdefault(d, []).append(max(e, key=abs))
    for d, v in worst.items():
        if d < 0 or d >= 14:
            assert max(map(abs, v)) <= 1
        else:
            assert -12 <= min(v) and max(v) <= 13.5


# ---- freq.s

def square(period, high, t0, n, fr):
    edges, t = [], t0 + 50 + fr
    while t < t0 + n:
        edges += [(t, 1), (t + high, 0)]
        t += period
    return edges


def run_freq(period, high, ph=0, fr=0.3, gate=5000, windows=3, pre=0):
    c = boot(prog("freq", GATE=gate, PRE=pre), ph, trace=False)
    n = (gate * 16 ** pre + 300) * (windows + 1) + 200
    simulate(c, n, wave(square(period, high, c.cycle, n, fr)))
    out, b, ev = [], [], 0
    for _, v in c.host_out[0]:
        if v & 0x100:
            assert v == 0x102
            ev += 1
            continue
        b.append(v)
        if len(b) == 4:
            s, r0 = b[0] | b[1] << 8, b[2] | b[3] << 8
            out.append((bin(s).count("1") + 16 * r0, ev))
            b, ev = [], 0
    return out[1:]                              # the first window follows the RUN


def in_bound(n, x):
    # one rise latched in the pass overhead when the tick aborts is not counted: -1 .. +1 edge around W / period
    return math.floor(x) - 1 <= n <= math.ceil(x)


def test_freq_size():
    assert len(prog("freq").words) == 17


@pytest.mark.parametrize("period", [7, 9.3, 50, 1000])
def test_freq_exact(period):
    for ph in range(4):
        for fr in (0.1, 0.6):
            for n, ev in run_freq(period, period / 2, ph, fr):
                assert ev == 0 and in_bound(n, 5000 / period)


def test_freq_tick_between_add_and_adc():
    # reviewer D224: a gate tick between `add S, #1` and `adc R0, R2` lost 16 edges unreported; gate lengths swept
    # so the tick lands on every point of the pass
    for g in range(300, 700, 7):
        for n, ev in run_freq(7, 3.5, g % 4, 0.3, gate=g, windows=4):
            assert ev == 0 and in_bound(n, g / 7), (g, n)


@pytest.mark.parametrize("high", [1.0, 19.0])
def test_freq_shortest_pulse(high):
    for ph in range(4):
        for fr in (0.1, 0.4, 0.7):
            assert all(ev == 0 and in_bound(n, 250) for n, ev in run_freq(20, high, ph, fr))


def test_freq_subclock_pulses_vanish_silently():
    res = [x for ph in range(4) for fr in (0.1, 0.4, 0.7) for x in run_freq(20, 0.5, ph, fr)]
    assert all(ev == 0 for _, ev in res) and min(n for n, _ in res) < 250 - 1


@pytest.mark.parametrize("period", [5, 6])
def test_freq_overload_reported(period):
    # above 7.1 MHz edges merge; every window that lost edges carries evt 2
    for ph in range(4):
        for n, ev in run_freq(period, period / 2, ph, 0.4):
            assert ev == 1 or in_bound(n, 5000 / period)


@pytest.mark.parametrize("pre,gate", [(1, 500), (2, 40)])
def test_freq_prescaled_window(pre, gate):
    for ph in (0, 3):
        for n, ev in run_freq(50, 25, ph, 0.3, gate, 2, pre):
            assert ev == 0 and in_bound(n, gate * 16 ** pre / 50)


# ---- nec.s (fast tests at CLK_HZ 781.25 kHz, PRE 1: same code, constants scaled; one slow test at 50 MHz)

NEC_LO = {"CLK_HZ": 781250, "PRE": 1}
U = 562.5e-6


def nec_edges(items, hz, t0, r, jit=0.0, scale=1.0):
    # items: ("frame", addr, cmd[, nbits]) | ("repeat",) | ("lead", mark_s, space_s) | ("gap", s); line active low
    edges, t = [], t0

    def seg(lv, d):
        nonlocal t
        edges.append((t, lv))
        t += d * scale * (1 + r.uniform(-jit, jit)) * hz
    for it in items:
        if it[0] == "frame":
            bits = [(it[1] | (~it[1] & 0xFF) << 8 | it[2] << 16 | (~it[2] & 0xFF) << 24) >> k & 1 for k in range(32)]
            seg(0, 16 * U)
            seg(1, 8 * U)
            for b in bits[:it[3] if len(it) > 3 else 32]:
                seg(0, U)
                seg(1, 3 * U if b else U)
            if len(it) <= 3:
                seg(0, U)
            seg(1, 12e-3)
        elif it[0] == "repeat":
            seg(0, 16 * U)
            seg(1, 4 * U)
            seg(0, U)
            seg(1, 12e-3)
        elif it[0] == "lead":
            seg(0, it[1] / scale)
            seg(1, it[2] / scale)
            seg(0, U)
            seg(1, 12e-3)
        else:
            seg(1, it[1])
    return edges, t


def run_nec(items, ph=0, jit=0.0, scale=1.0, seed=0, defs=NEC_LO):
    hz = defs.get("CLK_HZ", 50000000)
    c = boot(prog("nec", **defs), ph, trace=False)
    edges, end = nec_edges(items, hz, c.cycle + 20.3, random.Random(seed), jit, scale)
    ts = [e[0] for e in edges]

    def f(t, ch):
        k = bisect.bisect_right(ts, t)
        return (edges[k - 1][1] if k else 1), 0
    simulate(c, int(end - c.cycle) + 10, f)
    return [("evt", v & 0xFF) if v & 0x100 else v for _, v in c.host_out[0]]


def nec_bytes(a, cmd):
    return [a, ~a & 0xFF, cmd, ~cmd & 0xFF]


def test_nec_size():
    assert len(prog("nec").words) == 30


@pytest.mark.parametrize("jit", [0.0, 0.1])
def test_nec_frames_jitter(jit):
    for ph in range(4):
        r = random.Random(ph)
        a, cm = r.randrange(256), r.randrange(256)
        assert run_nec([("frame", a, cm)], ph, jit, seed=ph) == nec_bytes(a, cm)


@pytest.mark.parametrize("scale", [0.8, 1.2])
def test_nec_timing_scale(scale):
    for ph in (0, 3):
        assert run_nec([("frame", 0x5A, 0xC3)], ph, scale=scale) == nec_bytes(0x5A, 0xC3)


def test_nec_repeat_codes():
    got = run_nec([("frame", 0x10, 0x20), ("repeat",), ("repeat",)], 1, 0.1, seed=5)
    assert got == nec_bytes(0x10, 0x20) + [("evt", 1), ("evt", 1)]


def test_nec_bad_frames():
    # short leader and short leader space: ignored; frame broken off after 10 bits: evt 2; the next frame is clean
    items = [("lead", 4e-3, 4.5e-3), ("lead", 9e-3, 1.0e-3), ("frame", 0xAA, 0x55, 10), ("frame", 0x01, 0xFE)]
    for ph in (0, 2):
        assert run_nec(items, ph, 0.05, seed=ph) == [("evt", 2)] + nec_bytes(0x01, 0xFE)


@pytest.mark.slow
def test_nec_50mhz():
    assert run_nec([("frame", 0x04, 0x08)], 2, 0.1, seed=9, defs={}) == nec_bytes(0x04, 0x08)


# ---- autobaud.s

def run_ab(bit, chars, ph=0, gap_after_sync=0.0, pre_chars=(), t0=40.3):
    c = boot(prog("autobaud", ENG=0, RXPIN=0), ph, trace=False)
    lead = UartSender(bit, list(pre_chars) + [0x55], t0=c.cycle + t0) if True else None
    s = UartSender(bit, chars, t0=lead.end + gap_after_sync)
    edges = lead.edges + s.edges
    ts = [e[0] for e in edges]

    def f(t, ch):
        k = bisect.bisect_right(ts, t)
        return (edges[k - 1][1] if k else 1), 0
    simulate(c, int(s.end - c.cycle) + int(2 * bit) + 20, f)
    out = [("evt", v & 0xFF) if v & 0x100 else v for _, v in c.host_out[0]]
    return out


def ab_check(out, bit, chars):
    b = out[:4]                                 # lock record: BIT lo, hi, FRAC, PRE
    if any(isinstance(x, tuple) for x in b) or len(b) < 4:
        return False, None
    tc = b[2] | b[3] << 8
    mean = ((b[0] | b[1] << 8) + (tc & 0xFF) / 256) * 16 ** (tc >> 8)
    return out[4:] == chars and abs(mean - bit) <= 1 / 8 * 16 ** (tc >> 8) + 0.13, mean


def test_autobaud_size():
    assert len(prog("autobaud").words) == 75


@pytest.mark.parametrize("baud", [1000000, 921600, 460800, 115200, 57600, 9600])
def test_autobaud_rates(baud):
    data = [0x00, 0xFF, 0xA3, 0x3C] if baud > 100000 else [0xA3]
    for ph in (range(4) if baud > 100000 else (1,)):
        for dev in (-0.02, 0.0, 0.02) if baud > 100000 else (-0.02, 0.02):
            bit = 50e6 / baud * (1 + dev)
            ok, mean = ab_check(run_ab(bit, data, ph, gap_after_sync=400), bit, data)
            assert ok, (baud, dev, ph, mean)


@pytest.mark.slow
@pytest.mark.parametrize("baud,dev", [(1200, 0.02), (700, -0.02), (300, 0.02)])
def test_autobaud_low_rates(baud, dev):
    # 1200: wraps inside each interval; 700, 300: PRE 1
    bit = 50e6 / baud * (1 + dev)
    ok, mean = ab_check(run_ab(bit, [0xA5], 1, gap_after_sync=0), bit, [0xA5])
    assert ok, mean


def test_autobaud_too_fast_remeasures():
    # a 0x55 at BIT 10 (5 Mbaud) is refused (evt 3); the next 0x55 at 1 Mbaud locks
    c = boot(prog("autobaud", ENG=0, RXPIN=0), 0, trace=False)
    a = UartSender(10, [0x55], t0=c.cycle + 40.3)
    b = UartSender(50, [0x55], t0=a.end + 500)
    d = UartSender(50, [0x3C], t0=b.end + 400)
    edges = a.edges + b.edges + d.edges
    ts = [e[0] for e in edges]
    simulate(c, int(d.end - c.cycle) + 120, lambda t, ch: (edges[bisect.bisect_right(ts, t) - 1][1] if t >= ts[0] else 1, 0))
    out = [("evt", v & 0xFF) if v & 0x100 else v for _, v in c.host_out[0]]
    assert out[0] == ("evt", 3) and len(out) == 6 and out[-1] == 0x3C


def ab_gap_ok(bit, g):
    return all(ab_check(run_ab(bit, [0x00, 0xFF], ph, gap_after_sync=g, t0=40.3 + 0.27 * ph), bit, [0x00, 0xFF])[0]
               for ph in range(4))


def test_autobaud_gap_after_sync():
    # the rate is computed after the 5th fall: the next start must come >= 140 clk after it,
    # i.e. idle >= 140 - 2 BIT after the 0x55 stop bit (back to back from BIT 70; 40 clk at 1 Mbaud)
    assert ab_gap_ok(50, 40) and not ab_gap_ok(50, 20)
    assert ab_gap_ok(70, 0)
