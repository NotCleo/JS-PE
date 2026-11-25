# host/monitor.py timestamp decoding against the reference parsers of the monitor tests (same ISS runs).
import os
import sys
from harness import ROOT
import test_spi_mon as sm
import test_uart_mon as um
from models import UartSender

sys.path.insert(0, os.path.join(ROOT, "host"))
import monitor  # noqa: E402


def test_spi_timestamps_and_lost():
    c = sm.start(1, TS=1, TSP=0xFFFF)
    fr = [([0x10 + i, i], [0x20 + i, ~i & 0xFF]) for i in range(4)]
    xs, ys, t = [], [], c.cycle + 100.0
    for i, f in enumerate(fr):
        x, y = sm.bus(sm.TS_T, 0, [f], t0=t, cs_setup=sm.TS_SETUP)
        xs += x
        ys += y
        t = x[0].cs_hi + [500, 70000, 3000, 140000][i]
    sm.run(c, xs, ys, tail=300)
    got = monitor.spi(monitor.iss_entries(c, 0), monitor.iss_entries(c, 1), ts=True)
    ref, _ = sm.ts_frames(c, 0)
    assert [(f["start"], f["end"]) for f in got] == [(ref[k][1], ref[k + 1][1]) for k in range(0, len(ref), 2)]
    assert [f["mosi"] for f in got] == [f[0] for f in fr] and [f["miso"] for f in got] == [f[1] for f in fr]


def test_uart_ts1_ts2_match_reference():
    for ts in (1, 2):
        c = um.start(0, TS=ts, BAUD=1000000)
        a = UartSender(50, [1, 2, 3, 4, 5, 6], t0=c.cycle + 60, gap=200 if ts == 1 else 0)
        b = UartSender(50, [9, 8], t0=c.cycle + 2000, gap=200)
        um.run(c, a, b, int(max(a.end, b.end) - c.cycle) + 600)
        got = monitor.uart(monitor.iss_entries(c, um.E_A), ts=ts)
        if ts == 1:
            assert [(x[0], x[1]) for x in got] == [(x[0], x[1]) for x in um.records(c, um.E_A)]
        else:
            ref = um.stream0(c, um.E_A)
            assert got[0][0] == ref[0][1] and [x[1] for x in got] == [1, 2, 3, 4, 5, 6]
            assert all(x[0] is None for x in got[1:])
        bb = monitor.uart(monitor.iss_entries(c, um.E_B), ts=ts)
        lines = monitor.log_uart(got, bb) if ts == 1 else monitor.log_uart(got, [])
        assert len(lines) == len(got) + (len(bb) if ts == 1 else 0)


E = lambda c: (monitor.EV, c)                       # noqa: E731


def test_spi_dropped_frame_events_keep_pairing():
    f = monitor.spi([E(1), 1, 2, E(2), 3, 4, E(63), E(2), E(1), 5, E(2)], [E(1), 9, 8, E(2), E(1), 7, E(2), E(1), 6, E(2)])
    assert [x["mosi"] for x in f] == [[1, 2], [3, 4], [5]] and [x["lost"] for x in f] == [False, True, False]
    assert f[0] is not f[1]
    g = monitor.spi([E(1), 1, E(1), 2, E(2)])                     # evt 2 of the first frame lost
    assert [(x["mosi"], x["lost"]) for x in g] == [([1], True), ([2], False)]
    assert monitor.spi([3, E(2), E(1), 4, E(2)])[0]["mosi"] == [4]   # started inside a frame


def test_i2c_cut_stretch_record():
    assert monitor.i2c([E(1), 0x84, E(5), 0x10, E(3)]) == [("S",), ("A", 0x42, 0, 1), ("P",)]
    assert monitor.i2c([E(1), 0x84, E(5), 0x10, 0x00, 0x22, E(3)])[2:] == [("STRETCH", 0x10), ("D", 0x22, 1), ("P",)]


def test_uart_lost_flags():
    r = monitor.uart([0x10, 0x20, E(63), 0x41], ts=2)              # evt 58 + lo dropped: fragments flagged
    assert [(x[1], 63 in x[2]) for x in r] == [(0x10, True), (0x20, True), (0x41, True)]
    r = monitor.uart([1, 2, 3, E(63), 4, 5, 6], ts=1)              # evt 63 leads the next record
    assert [(x[1], 63 in x[2]) for x in r] == [(3, False), (6, True)] and r[1][3] == 1
    r = monitor.uart([0x31, E(63), 0x32], ts=0)                    # ts 0: evt 63 follows its byte
    assert [(x[1], 63 in x[2]) for x in r] == [(0x31, True), (0x32, False)]


def test_spi_host_matches_reference_near_one_wrap():
    tsp = 1023
    c = sm.start(2, TS=1, TSP=tsp)
    xs, ys, t = [], [], c.cycle + 100.0
    for i in range(40):
        x, y = sm.bus(sm.TS_T, 0, [([i, 0x5A, 0xC3], [~i & 0xFF, 1, 2])], t0=t, cs_setup=sm.TS_SETUP + (i * 7) % 90,
                      cs_hold=sm.TS_HOLD + (i * 5) % 60)
        xs += x
        ys += y
        t = x[0].cs_hi + sm.TS_CSH + (i * 11) % 97
    sm.run(c, xs, ys, tail=400)
    got = monitor.spi(monitor.iss_entries(c, 0), ts=True, tsp=tsp)
    ref, _ = sm.ts_frames(c, 0, tsp)
    assert [(f["start"], f["end"]) for f in got] == [(ref[k][1], ref[k + 1][1]) for k in range(0, len(ref), 2)]


def test_uart_stall_ts0_ts2_never_unflagged():
    # host stalled during a burst: every decoded character is either in order and correct, or flagged LOST
    for ts in (0, 2):
        for slot in (0, 3):
            c = um.start(slot, TS=ts, BAUD=1000000)
            ch = [(i * 7 + 1) & 0xFF for i in range(40)]
            a = UartSender(50, ch, t0=c.cycle + 60)
            b = UartSender(50, [], t0=c.cycle + 60)
            stall = (a.starts[5] + slot * 13, a.starts[25])
            um.run(c, a, b, int(a.end - c.cycle) + 600, block=lambda t, e: stall[0] <= t < stall[1])
            r = monitor.uart(monitor.iss_entries(c, um.E_A), ts=ts)
            k = 0
            for x in r:
                if 63 in x[2]:
                    continue
                k = ch.index(x[1], k) + 1 if x[1] in ch[k:] else None
                assert k is not None, (ts, slot, x)
            assert any(63 in x[2] for x in r) and r[-1][1] == ch[-1]


def test_uart_ts_engines_share_a_time_base():
    # both timers start in the same RUN: A and B timestamps differ from the real start edges by the same offset
    for ts in (1, 2):
        c = um.start(1, TS=ts, BAUD=115200)
        bit = 50e6 / 115200
        a = UartSender(bit, [1, 2, 3], t0=c.cycle + 100.3, gap=3 * bit)
        b = UartSender(bit, [4, 5, 6], t0=c.cycle + 100.3 + 1.5 * bit, gap=3 * bit)
        um.run(c, a, b, int(max(a.end, b.end) - c.cycle) + 1500)
        ra = monitor.uart(monitor.iss_entries(c, um.E_A), ts=ts)
        rb = monitor.uart(monitor.iss_entries(c, um.E_B), ts=ts)
        offs = [x[0] - s for x, s in zip(ra, a.starts)] + [x[0] - s for x, s in zip(rb, b.starts)]
        assert max(offs) - min(offs) <= 4, (ts, offs)
        lines = monitor.log_uart(ra, rb)
        assert [ln.split()[2] for ln in lines] == ["A", "B", "A", "B", "A", "B"]
