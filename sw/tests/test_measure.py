# meter.s and freq.s on the ISS (measure_design.md): accuracy, limits, glitches, wrap, prescale.
import bisect
import math
import random
import pytest
from harness import prog, boot, simulate


def wave(edges):
    ts = [e[0] for e in edges]

    def f(t, ch):
        k = bisect.bisect_right(ts, t)
        return (edges[k - 1][1] if k else 0), 0
    return f


def run_meter(phases, ph=0, frac=0.3, tail=100, **defs):
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
    return caps[1:n + 1], ws[1:n + 1], c          # the first reading is the phase before the first rise


def clk(v, w=0, pre=0):
    # host formula: phase in clk (None = over range); pre > 0: centre of the step
    if v == 0 and pre == 0 and w == 0:
        return None
    s = 65536 * w + 0x10000 - v if v else None
    if s is None:
        return None
    return s if pre == 0 else (s - 0.5) * 16 ** pre


def meter_errs(phases, ph=0, frac=0.3, pre=0, **defs):
    caps, ws, c = run_meter(phases, ph, frac, PRE=pre, **defs)
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


def ok_phases(phases, ext, rel):
    for ph in range(4):
        for fr in (0.1, 0.5, 0.9):
            e, n, c = meter_errs(phases, ph, fr, EXT=ext)
            if n < len(phases) or any(abs(x) > 1 for x in e if x is not None) or None in e:
                return False
    return True


@pytest.mark.parametrize("ext,hmin,lmin", [(0, 1, 22), (1, 10, 14)])
def test_meter_shortest_phases(ext, hmin, lmin):
    assert ok_phases([hmin, 300] * 6, ext, 1) and ok_phases([300, lmin] * 6, ext, 1)
    assert not ok_phases([300, lmin - 1] * 6, ext, 1)
    if ext:
        assert not ok_phases([hmin - 1, 300] * 6, ext, 1)


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
        if len(b) == 5:
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
