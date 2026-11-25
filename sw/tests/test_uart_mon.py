# uart_mon.s (monitor_design.md, MON1 at the ISS level): both directions of a UART on two engines, with timestamps.
import pytest
from harness import prog
from jspe_iss import Chip
from models import UartSender

E_A, E_B, PIN_A, PIN_B = 0, 1, 7, 6
EV = 0x100


def start(slot=0, **defs):
    defs.setdefault("TS", 1)
    img = prog("uart_mon", **defs)
    c = Chip()
    for e in c.eng:
        e.trace = True
    c.boot(img, run=False)
    idle = 0x08 | 1 << PIN_A | 1 << PIN_B
    for _ in range(8):
        c.step(idle, 0)
    while c.slot != slot:
        c.step(idle, 0)
    c.start(sorted(img.engines), slot)
    return c


def run(c, a, b, n, hook=None, block=None):
    for _ in range(n):
        t = c.cycle + 1 - 1e-6
        if hook:
            hook(c, t)
        if block:
            for e in (E_A, E_B):
                c.tx_block[e] = block(t, e)
        ui = 0x08 | a.level(t) << PIN_A | b.level(t) << PIN_B
        assert c.uio_oe == 0
        c.step(ui, 0)


def records(c, e, shift=None):
    # host rule (uart_mon.s): [evt 63] [evt 60] lo, hi, byte [evt 1]; evt 63 is flagged on the record before it
    out, w, pend, e60 = [], 0, [], False
    for _, v in c.host_out[e]:
        if v == EV | 62:
            w += 1
        elif v == EV | 60:
            e60 = True
        elif v == EV | 63:
            pend, e60 = [], False                       # resync: drop an unfinished record
            if out:
                out[-1][2].add(63)
        elif v & EV:
            if not pend and out:
                out[-1][2].add(v & 0x3F)
        else:
            pend.append(v)
            if len(pend) == 3:
                t = pend[0] | pend[1] << 8
                out.append(((w - e60) * 65536 + 0xFFFF - t, pend[2], set()))
                pend, e60 = [], False
    return out


def wrap_offsets(c, bit, gap, offs, slot=0):
    # one character per offset o: during the gap before it, T is set so that the stop sample lands o clk before the
    # wrap (o < 0: after it); returns per engine [(reconstructed - real start, byte, flags)]
    chars = [(i * 53 + 9) & 0xFF for i in range(len(offs))]
    a = UartSender(bit, chars, t0=c.cycle + 200.3 + slot, gap=gap)
    b = UartSender(bit, chars[::-1], t0=c.cycle + 200.3 + slot + bit / 3, gap=gap)
    shift = {E_A: 0, E_B: 0}
    plan = {}
    for s, e in ((a, E_A), (b, E_B)):
        for k, st in enumerate(s.starts):
            plan.setdefault(int(st - gap / 2), []).append((e, st + 9.5 * bit, offs[k]))
    seen = []

    def hook(ch, t):
        for e, stop, o in plan.pop(int(t), []):
            eng = ch.eng[e]
            new = int(stop - t + o) % 65536
            shift[e] += eng.T - new
            eng.T = new
            seen.append((e, shift[e]))
    run(c, a, b, int(max(a.end, b.end) - c.cycle) + 400, hook)
    out = {}
    for s, e in ((a, E_A), (b, E_B)):
        sh = [x for ee, x in seen if ee == e]
        r = records(c, e)
        out[e] = [(round(tt - st - d), by, fl) for (tt, by, fl), st, d in zip(r, s.starts, sh)]
        assert [x[1] for x in r] == [chars, chars[::-1]][e], (e, [x[1] for x in r])
    return out


GAP = 160                                    # uart_mon.s rule: stop sample -> next start edge (clk)


def check_dual(c, a, b, ca, cb, spread=0):
    for s, e, ch in ((a, E_A, ca), (b, E_B, cb)):
        r = records(c, e)
        assert [x[1] for x in r] == ch, e
        assert not any(x[2] for x in r)
        off = [tt - st for (tt, _, _), st in zip(r, s.starts)]
        assert not off or max(off) - min(off) <= spread + 1, (e, off)


@pytest.mark.parametrize("slot", [0, 1, 2, 3])
def test_both_directions_back_to_back_115200(slot):
    c = start(slot, BAUD=115200)
    bit = 50e6 / 115200
    ca = [(i * 37 + 5) & 0xFF for i in range(12)]
    cb = [0x1B, 0x00, 0xFF, 0x55] * 3
    a = UartSender(bit, ca, t0=c.cycle + 40.3 + slot)
    b = UartSender(bit, cb, t0=c.cycle + 40.3 + bit * 3.4)          # overlapping, other phase
    run(c, a, b, int(max(a.end, b.end) - c.cycle) + 600)
    check_dual(c, a, b, ca, cb)


@pytest.mark.parametrize("slot", [0, 1, 2, 3])
def test_1mbaud_at_gap_rule(slot):
    c = start(slot, BAUD=1000000)
    ca = [(i * 11 + 3) & 0xFF for i in range(16)]
    cb = ca[::-1]
    gap = GAP - 25                                                  # stop sample is 25 clk before the stop bit ends
    a = UartSender(50, ca, t0=c.cycle + 40.6, gap=gap)
    b = UartSender(50, cb, t0=c.cycle + 40.6 + 17.3 * (slot + 1), gap=gap)
    run(c, a, b, int(max(a.end, b.end) - c.cycle) + 600)
    check_dual(c, a, b, ca, cb)


@pytest.mark.nolint
@pytest.mark.parametrize("slot", [0] + [pytest.param(k, marks=pytest.mark.slow) for k in (1, 2, 3)])
def test_every_wrap_offset(slot):
    # T preset in each idle gap so the stop sample lands -580..+158 clk around a wrap (the start edge is 475 clk
    # before it): wraps at the start edge, in the data bits and at the record; data exact, each engine's
    # reconstructed start times at one constant offset, evt 60 used
    c = start(slot, BAUD=1000000)
    out = wrap_offsets(c, 50, GAP - 25, list(range(-580, 160, 3 if slot else 5)), slot)
    for e in (E_A, E_B):
        d = [x[0] for x in out[e]]
        assert max(d) - min(d) <= 14, (e, d)                     # an edge during the wrap call: <= 14 clk late
        assert not any(x[2] for x in out[e])
    assert any(v == EV | 60 for e in (E_A, E_B) for _, v in c.host_out[e])
    assert any(v == EV | 62 for e in (E_A, E_B) for _, v in c.host_out[e])


def test_framing_error_and_break():
    c = start(0, BAUD=115200)
    bit = 50e6 / 115200
    a = UartSender(bit, [0x41, 0x42, 0x43, 0x44], t0=c.cycle + 40, gap=bit, bad_stop=(1,), breaks=(3,))
    b = UartSender(bit, [0x61], t0=c.cycle + 40)
    run(c, a, b, int(a.end - c.cycle) + 600)
    r = records(c, E_A)
    assert [(x[1], sorted(x[2])) for x in r] == [(0x41, []), (0x42, [1]), (0x43, []), (0x00, [1])]


def test_host_stall_lost():
    c = start(1, BAUD=115200)
    bit = 50e6 / 115200
    ch = list(range(10))
    a = UartSender(bit, ch, t0=c.cycle + 40)
    b = UartSender(bit, [], t0=c.cycle + 40)
    stall = (a.starts[1], a.starts[6])
    run(c, a, b, int(a.end - c.cycle) + 600, block=lambda t, e: stall[0] <= t < stall[1])
    r = records(c, E_A)
    assert sum(63 in x[2] for x in r) >= 1
    got = [x[1] for x in r]
    assert got[0] == 0 and got[-4:] == ch[-4:]                        # clean again after the stall
    assert all(x in ch for x in got) and got == sorted(got)           # no misaligned record, order kept


@pytest.mark.slow
def test_real_wraps_long_idle():
    # no presets: idle gaps of 0.4 .. 4 wraps between characters, times reproduced exactly
    c = start(2, BAUD=1000000)
    gaps = [30000, 70000, 140000, 65536, 262000]
    ca = [0x10, 0x20, 0x30, 0x40, 0x50, 0x60]
    a = UartSender(50, ca[:1], t0=c.cycle + 40)
    edges, starts, t = list(a.edges), list(a.starts), a.end
    for g, ch in zip(gaps, ca[1:]):
        x = UartSender(50, [ch], t0=t + g)
        edges += x.edges
        starts += x.starts
        t = x.end
    a.edges, a.starts, a.end = edges, starts, t
    b = UartSender(50, [], t0=c.cycle + 40)
    run(c, a, b, int(a.end - c.cycle) + 600)
    check_dual(c, a, b, ca, [])


def test_words():
    assert [len(prog("uart_mon", TS=t).words) for t in (0, 1, 2)] == [12, 31, 50]


def stream0(c, e):
    # TS = 0 / 2 host view: data bytes, events as "eN"; TS = 2: ("ts", time) for evt 58/60 + lo, hi
    out, w, pend, adj = [], 0, None, 0
    for _, v in c.host_out[e]:
        if v == EV | 62:
            w += 1
        elif v in (EV | 58, EV | 60):
            pend, adj = [], (v == EV | 60)
        elif v & EV:
            pend = None if v == EV | 63 else pend
            out.append("e%d" % (v & 0x3F))
        elif pend is not None:
            pend.append(v)
            if len(pend) == 2:
                out.append(("ts", (w - adj) * 65536 + 0xFFFF - (pend[0] | pend[1] << 8)))
                pend = None
        else:
            out.append(v)
    return out


def back_to_back(ts, baud, rate, slot, n=24, gap=0.0):
    c = start(slot, TS=ts, BAUD=baud)
    bit = 50e6 / baud * (1 + rate)
    ca = [(i * 37 + 5) & 0xFF for i in range(n)]
    cb = ca[::-1]
    a = UartSender(bit, ca, t0=c.cycle + 40.3 + slot * 0.3, gap=gap)
    b = UartSender(bit, cb, t0=c.cycle + 40.3 + bit * 0.37, gap=gap)
    run(c, a, b, int(max(a.end, b.end) - c.cycle) + 400)
    return c, a, b, ca, cb


@pytest.mark.parametrize("baud", [1000000, 2000000, 2630000])
@pytest.mark.parametrize("rate", [-0.02, 0.0, 0.02])
def test_ts0_both_directions_back_to_back(baud, rate):
    # high-speed mode: uart_rx timing on both engines (BIT >= 19 clk)
    for slot in range(4):
        c, a, b, ca, cb = back_to_back(0, baud, rate, slot)
        assert stream0(c, E_A) == ca and stream0(c, E_B) == cb, slot


@pytest.mark.parametrize("rate", [-0.02, 0.0, 0.02])
def test_ts2_back_to_back_1mbaud(rate):
    # one burst per line: only its first character is timestamped
    for slot in range(4):
        c, a, b, ca, cb = back_to_back(2, 1000000, rate, slot)
        for e, ch in ((E_A, ca), (E_B, cb)):
            s = stream0(c, e)
            assert isinstance(s[0], tuple) and s[1:] == ch, (slot, e, s[:4])


def test_ts2_bursts_timestamped_at_their_start():
    # bursts of 1..5 back-to-back characters, idle 2.5 .. 40 bits between them: a timestamp on each burst's first
    # character, at one constant offset from its start edge
    c = start(1, TS=2, BAUD=1000000)
    edges, starts, firsts, chars, t = [], [], [], [], c.cycle + 60.0
    for k in range(12):
        n = k % 5 + 1
        ch = [(k * 17 + i) & 0xFF for i in range(n)]
        x = UartSender(50, ch, t0=t)
        edges += x.edges
        starts += x.starts
        firsts.append(x.starts[0])
        chars += [("ts",)] + ch
        t = x.end + [125, 400, 2000][k % 3]
    a = UartSender(50, [], t0=c.cycle + 40)
    a.edges, a.end = edges, t
    b = UartSender(50, [], t0=c.cycle + 40)
    run(c, a, b, int(t - c.cycle) + 400)
    s = stream0(c, E_A)
    assert [("ts",) if isinstance(x, tuple) else x for x in s] == chars
    off = [x[1] - f for x, f in zip([x for x in s if isinstance(x, tuple)], firsts)]
    assert max(off) - min(off) <= 1, off


@pytest.mark.nolint
@pytest.mark.parametrize("slot", [0, 1, 2, 3])
def test_ts2_every_wrap_offset(slot):
    # each character after >= 2.5 idle bits: T preset in the gap so its timestamp attempt lands -120..+200 clk
    # around a wrap (attempt moves to the next bit gap inside the window); all exact
    c = start(slot, TS=2, BAUD=1000000)
    offs = list(range(-120, 200, 4))
    chars = [(i * 53 + 9) & 0xFF for i in range(len(offs))]
    a = UartSender(50, chars, t0=c.cycle + 200.3 + slot, gap=150)
    b = UartSender(50, [], t0=c.cycle + 40)
    plan = {int(st - 75): (st, o) for st, o in zip(a.starts, offs)}
    shift, seen = [0], []

    def hook(ch, t):
        if int(t) in plan:
            st, o = plan.pop(int(t))
            eng = ch.eng[E_A]
            new = int(st + 80 - t + o) % 65536
            shift[0] += eng.T - new
            eng.T = new
            seen.append(shift[0])
    run(c, a, b, int(a.end - c.cycle) + 400, hook)
    s = stream0(c, E_A)
    assert [x for x in s if not isinstance(x, tuple)] == chars
    ts = [x[1] for x in s if isinstance(x, tuple)]
    assert len(ts) == len(chars)
    d = [round(t - st - sh) for t, st, sh in zip(ts, a.starts, seen)]
    assert max(d) - min(d) <= 14, d                                     # an edge during the wrap call
