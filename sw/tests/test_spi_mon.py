# spi_mon.s (monitor_design.md, MON1 at the ISS level): passive decode of both SPI data lines on two engines.
import pytest
from harness import prog, simulate
from jspe_iss import Chip
from models import SpiController
from test_first import Seq

E_MOSI, E_MISO = 0, 1
EV = 0x100
CSH, CSH_SLOW, TS_SETUP, TS_CSH, TS_T = 25, 40, 160, 240, 28      # spi_mon.s header rules (TS_CSH: CS high with TS)


def bus(T, mode, frames, t0=60.0, gap=40.0, phase=0.0, **kw):
    # frames: list of (mosi bytes, miso bytes[, cs_cuts]); MISO launched by the target on the controller's launch edges
    xs, ys, t = [], [], t0
    for f in frames:
        cut = f[2] if len(f) > 2 else None
        x = SpiController(T, mode, f[0], phase=phase, t0=t, cs_cuts=cut, **kw)
        y = SpiController(T, mode, f[1], phase=phase, t0=t, cs_cuts=cut, **kw)
        xs.append(x)
        ys.append(y)
        t = x.cs_hi + gap
    return xs, ys


def start(slot=0, cpol=0, img=None, **defs):
    # load with the bus idle (CS high, SCK at rest) as on the board, then run in slot phase `slot`
    img = img or prog("spi_mon", **defs)
    c = Chip()
    for e in c.eng:
        e.trace = True
    c.boot(img, run=False)
    for _ in range(8):
        c.step(8, 1 | cpol << 3)
    while c.slot != slot:
        c.step(8, 1 | cpol << 3)
    c.start(sorted(img.engines), slot)
    return c


def run(c, xs, ys, tail=60, block=None, noise=()):
    sx, sy = Seq(xs), Seq(ys)
    cpol = xs[0].cpol

    def inputs(t, ch):
        if block:
            for e in (E_MOSI, E_MISO):
                ch.tx_block[e] = block(t, e)
        if any(a <= t < b for a, b in noise):
            return 8, (int(t) & 1 ^ cpol) << 3 | 1 | 6
        sck = sx.sck(t) if t >= xs[0].cs_lo else cpol
        return 8, sck << 3 | sx.cs(t) | sx.mosi(t) << 1 | sy.mosi(t) << 2
    simulate(c, int(xs[-1].cs_hi) + tail - c.cycle, inputs)
    return c


def stream(c, e, ts=False):
    # ts: drop evt 61/62 and the two timestamp bytes after evt 1 / evt 2 (a wrap event may come between them)
    out, skip = [], 0
    for _, v in c.host_out[e]:
        if ts and v in (EV | 61, EV | 62):
            continue
        if skip and not v & EV:
            skip -= 1
        elif v & EV:
            out.append("e%d" % (v & 0x3F))
            skip = 2 if ts and v in (EV | 1, EV | 2) else 0
        else:
            out.append(v)
    return out


def expect(xs, ys):
    # per engine: evt 1, the whole bytes sampled before CS rose, evt 3 if bits are left over, evt 2
    want = {E_MOSI: [], E_MISO: []}
    for x, y in zip(xs, ys):
        nb = sum(t < x.cs_hi for t in x.samp)
        for e, c in ((E_MOSI, x), (E_MISO, y)):
            want[e] += ["e1"] + c.tx[:nb // 8] + (["e3"] if nb % 8 else []) + ["e2"]
    return want


def no_drive(c):
    assert all(uoe == 0 for _, _, _, uoe in c.pad_trace)
    assert len({uo for _, uo, _, _ in c.pad_trace}) <= 1
    assert all(p & 0x40 == 0 for p in c.pad)


FR = [([0x9C, 0x01, 0xFF], [0x00, 0xA5, 0x5A]), ([0x55], [0xAA]), ([0x00, 0x80, 0x7F, 0x1B, 0xC3], [0xFE, 0x01, 0x1B, 0x3C, 0x00])]


@pytest.mark.parametrize("mode", [0, 1, 2, 3])
@pytest.mark.parametrize("slot", [0, 1, 2, 3])
def test_modes_both_lines(mode, slot):
    c = start(slot, mode >> 1, MODE12=int(mode in (1, 2)))
    xs, ys = bus(12.5, mode, FR, t0=c.cycle + 40, phase=slot * 0.25)
    run(c, xs, ys)
    want = expect(xs, ys)
    assert stream(c, E_MOSI) == want[E_MOSI] and stream(c, E_MISO) == want[E_MISO]
    assert c.eng[E_MOSI].ovr == 0 and c.eng[E_MISO].ovr == 0
    no_drive(c)


@pytest.mark.parametrize("mode", [0, 3])
def test_cs_cuts_mid_byte(mode):
    # CS rises after 1..23 bits: whole bytes delivered, evt 3 for a partial byte, the next frame starts clean
    frames = []
    for k in range(1, 24):
        frames += [([k, 0x5A, 0xC3], [0xF0 ^ k, 0x0F, 0x99], k), ([0x12, 0x34], [0x56, 0x78])]
    c = start(2, mode >> 1)
    xs, ys = bus(12.5, mode, frames, t0=c.cycle + 40, gap=CSH_SLOW)
    run(c, xs, ys)
    want = expect(xs, ys)
    assert stream(c, E_MOSI) == want[E_MOSI] and stream(c, E_MISO) == want[E_MISO]
    no_drive(c)


@pytest.mark.parametrize("T", [4, 5, 6])
def test_max_rate(T):
    # sampling edges every T clk in all slot phases and sub-clock phases, 16-byte frames, CS high CSH
    data = [(i * 37 + 11) & 0xFF for i in range(16)]
    fr = [(data, data[::-1]), (data[::-1], data)]
    for slot in range(4):
        for ph in (0.0, 0.3, 0.6, 0.9):
            c = start(slot)
            xs, ys = bus(T, 0, fr, t0=c.cycle + 40, phase=ph, gap=CSH, cs_setup=1, cs_hold=1)
            run(c, xs, ys)
            want = expect(xs, ys)
            assert stream(c, E_MOSI) == want[E_MOSI], (slot, ph)
            assert stream(c, E_MISO) == want[E_MISO], (slot, ph)
            assert c.eng[E_MOSI].ovr == 0 and c.eng[E_MISO].ovr == 0


@pytest.mark.parametrize("ts", [0, 1])
@pytest.mark.parametrize("cut", [None, 13])
def test_cs_high_min(ts, cut):
    # CS high at the rule (whole bytes CSH, after a partial byte CSH_SLOW; + TS_CSH with TS) with SCK toggling
    # the whole time CS is high (another target's traffic): ignored
    csh = TS_CSH if ts else CSH if cut is None else CSH_SLOW
    fr = [([0xA5, 0x3C], [0x11, 0x22]) + ((cut,) if cut else ())] * 6
    for slot in range(4):
        for ph in (0.0, 0.5):
            c = start(slot, TS=ts)
            xs, ys = bus(8, 0, fr, t0=c.cycle + 40, phase=ph, gap=csh, cs_setup=1 + (TS_SETUP - 1) * ts, cs_hold=1)
            noise = [(x.cs_hi + 0.5, y.cs_lo - 0.5) for x, y in zip(xs, xs[1:])]
            run(c, xs, ys, noise=noise)
            want = expect(xs, ys)
            assert stream(c, E_MOSI, ts) == want[E_MOSI] and stream(c, E_MISO, ts) == want[E_MISO], (slot, ph)


def test_overrun_reported_in_band():
    # host drains nothing during frame 2 (12 bytes > 4 FIFO entries): LOST before its evt 2, frame 3 clean
    fr = [([1, 2], [3, 4]), (list(range(12)), list(range(100, 112))), ([7, 8], [9, 10])]
    c = start(1)
    xs, ys = bus(12.5, 0, fr, t0=c.cycle + 40, gap=200)
    a, b = xs[1].cs_lo, xs[1].cs_hi + 10
    run(c, xs, ys, block=lambda t, e: a <= t < b)
    for e, d in ((E_MOSI, fr[1][0]), (E_MISO, fr[1][1])):
        s = stream(c, e)
        assert s[:4] == ["e1"] + list(fr[0][e]) + ["e2"]
        i2 = s.index("e1", 4)
        mid = s[i2 + 1:s.index("e2", i2)]
        assert mid[-1] == "e63" and mid[:-1] == d[:len(mid) - 1]          # delivered bytes are a correct prefix
        assert s[-4:] == ["e1"] + list(fr[2][e]) + ["e2"]
        assert c.eng[e].ovr == 0


def ts_frames(c, e, tsp=0xFFFF, tsw=41):
    # host rule (spi_mon.s): wraps counted up to the timestamp's hi byte, one fewer for T < TSW;
    # time = wraps * (TSP + 1) + TSP - T. evt 61 before a CS-rise TS: if that end precedes the frame's start,
    # the CS rise cut a wrap handler: one wrap more from here on. A TS cut short by the next event is missing.
    out, wraps, pend, cut, start = [], 0, None, False, None
    for _, v in c.host_out[e]:
        if v == EV | 62:
            wraps += 1
        elif v == EV | 61:
            cut = True
        elif v & EV:
            pend = [v & 3] if v in (EV | 1, EV | 2) else None
        elif pend:
            pend.append(v)
            if len(pend) == 3:
                t = pend[1] | pend[2] << 8
                tt = (wraps - (t < tsw)) * (tsp + 1) + tsp - t
                if pend[0] == 2 and cut and start is not None and tt < start:
                    wraps, tt = wraps + 1, tt + tsp + 1
                start = tt if pend[0] == 1 else start
                out.append((pend[0], tt))
                pend, cut = None, False
    return out, wraps


@pytest.mark.parametrize("slot", [0, 1, 2, 3])
@pytest.mark.parametrize("tsp", [999, pytest.param(0xFFFF, marks=pytest.mark.slow)])
def test_timestamps_and_wrap(slot, tsp):
    # gaps from 500 clk to 3 wraps (period tsp + 1): CS edge times reproduced to a constant offset per edge kind
    fr = [([0x10 + i], [0x20 + i]) for i in range(9)]
    k = (tsp + 1) / 65536
    c = start(slot, TS=1, TSP=tsp)
    xs, ys, t = [], [], c.cycle + 100.0
    for i, f in enumerate(fr):
        x, y = bus(TS_T, 0, [f], t0=t, cs_setup=TS_SETUP)
        xs += x
        ys += y
        t = x[0].cs_hi + max(200, int([500, 70000, 200000, 65536, 65535 - 40][i % 5] * k)) + slot * 7
    run(c, xs, ys, tail=300)
    for e in (E_MOSI, E_MISO):
        ev, wraps = ts_frames(c, e, tsp)
        real = sorted([(1, x.cs_lo) for x in xs] + [(2, x.cs_hi) for x in xs], key=lambda r: r[1])
        assert [k for k, _ in ev] == [k for k, _ in real]
        for kind in (1, 2):
            off = [tt - r for (k, tt), (_, r) in zip(ev, real) if k == kind]
            assert max(off) - min(off) <= 24, (kind, off)
        assert wraps >= 9
        assert stream(c, e, True) == expect(xs, ys)[e]
    no_drive(c)


@pytest.mark.parametrize("ph", [0.0, 0.3, 0.6, 0.9])
def test_timestamps_wrap_inside_frame(ph, T=TS_T):
    # TS max SCK: wraps every ~1000 clk (TSP) inside 60-byte frames, the wrap call preempts the micro-loop;
    # data held T/2 = 14 clk after each sampling edge: no bit lost or flipped
    for slot in range(4):
        c = start(slot, TS=1, TSP=997 + slot)
        d = [(i * 7 + slot) & 0xFF for i in range(60)]
        xs, ys = bus(T, 0, [(d, d[::-1])], t0=c.cycle + 40 + slot * 3, phase=ph, cs_setup=TS_SETUP)
        run(c, xs, ys, tail=300)
        for e in (E_MOSI, E_MISO):
            assert stream(c, e, True) == expect(xs, ys)[e], slot
            assert sum(v == EV | 62 for _, v in c.host_out[e]) >= 10


def test_fifo_full_drops_rest_of_frame():
    # host stalled: 4 entries delivered, the rest of the frame dropped (no late, wrong bits), LOST, next frame clean
    fr = [(list(range(10, 30)), list(range(50, 70))), ([1, 2], [3, 4])]
    for slot in range(4):
        c = start(slot)
        xs, ys = bus(6, 0, fr, t0=c.cycle + 40, gap=CSH_SLOW, cs_setup=1, cs_hold=1)
        a, b = xs[0].cs_lo, xs[0].cs_hi - 30
        run(c, xs, ys, block=lambda t, e: a <= t < b)
        for e in (E_MOSI, E_MISO):
            s = stream(c, e)
            assert s == ["e1"] + [xs, ys][e][0].tx[:3] + ["e63", "e2"] + ["e1"] + [xs, ys][e][1].tx + ["e2"], (slot, s)


@pytest.mark.parametrize("slot", [0, pytest.param(1, marks=pytest.mark.slow), pytest.param(2, marks=pytest.mark.slow),
                                  pytest.param(3, marks=pytest.mark.slow)])
def test_timestamps_every_wrap_offset(slot, tsp=511):
    # 180 frames at the TS rules (first edge TS_SETUP + 0..36 clk, CS high TS_CSH): CS edges land at every offset
    # to a wrap, so captures hit both wait windows, cut wrap handlers (evt 61) and wraps between evt and TS bytes;
    # every edge time is reproduced within 24 clk of a constant offset, all data correct
    c = start(slot, TS=1, TSP=tsp)
    xs, ys, t = [], [], c.cycle + 100.0
    for i in range(180):
        x, y = bus(TS_T, 0, [([i & 0xFF], [~i & 0xFF])], t0=t, cs_setup=TS_SETUP + (i * 3) % 37, cs_hold=1)
        xs += x
        ys += y
        t = x[0].cs_hi + TS_CSH + (i % 2) * 0.5
    run(c, xs, ys, tail=300)
    cut = 0
    for e in (E_MOSI, E_MISO):
        ev, wraps = ts_frames(c, e, tsp)
        real = sorted([(1, x.cs_lo) for x in xs] + [(2, x.cs_hi) for x in xs], key=lambda r: r[1])
        assert [k for k, _ in ev] == [k for k, _ in real]
        for kind in (1, 2):
            off = [tt - r for (k, tt), (_, r) in zip(ev, real) if k == kind]
            assert max(off) - min(off) <= 24, (kind, min(off), max(off))
        assert stream(c, e, True) == expect(xs, ys)[e]
        cut += sum(v == EV | 61 for _, v in c.host_out[e])
    assert cut > 0
    no_drive(c)


def test_default_has_no_timestamp_words():
    assert len(prog("spi_mon").words) == 14 and len(prog("spi_mon", TS=1).words) == 40
