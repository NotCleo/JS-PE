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
