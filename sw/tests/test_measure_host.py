# host/measure.py against ISS runs of the measurement programs (same stimulus helpers as test_measure.py).
import os
import sys
from harness import ROOT, prog, boot, simulate
import test_measure as tm

sys.path.insert(0, os.path.join(ROOT, "host"))
import measure  # noqa: E402


def meter_run(phases, **defs):
    _, _, c = tm.run_meter(phases, 1, 0.4, **defs)
    n = len(phases) - defs.get("INV", 0)       # trailing stimulus edges add readings; INV: the dropped first is valid
    return measure.meter(measure.iss_entries(c, 0), defs.get("PRE", 0), defs.get("EXT", 0), defs.get("INV", 0))[:n]


def test_meter_decode_levels_and_stats():
    phases = [100, 300, 250, 150] * 3
    got = meter_run(phases)
    assert [p[0] for p in got] == [1, 0] * 6                    # pad levels: high, low, ...
    assert all(abs(g[1] - d) <= 1 and g[2] is None for g, d in zip(got, phases))
    st = measure.stats(got)
    assert abs(st["period"] - 400) <= 0.5 and abs(st["duty"] - 0.4375) < 0.002


def test_meter_decode_inverted_and_short():
    got = meter_run([300, 1, 300, 1, 300, 1], INV=1)
    want = [(0, 1), (1, 300), (0, 1), (1, 300), (0, 1)]
    assert [p[0] for p in got] == [w[0] for w in want] and all(abs(p[1] - w[1]) <= 1 for p, w in zip(got, want))
    got = meter_run([500, 500, 400, 5, 95, 500])                # 5-clk low: flagged short
    assert [p[2] for p in got] == [None, None, None, "short", None, None]
    got = meter_run([500, 500, 400, 1, 99, 500])                # 1-clk low reads 21-22 (swin window): flagged
    assert got[3][2] == "short" and got[2][2] is None and got[4][2] is None


def test_meter_decode_prescale_short_step():
    got = meter_run([3000, 3000, 10, 3000, 3000, 3000], PRE=1)  # 10 clk < 1 step: short, the next phase suspect
    assert [p[2] for p in got] == [None, None, "short", "suspect", None, None]


def test_meter_decode_ext_wraps():
    got = meter_run([70000, 300, 300, 300], EXT=1)
    assert abs(got[0][1] - 70000) <= 1 and got[0][2] is None


def test_freq_decode():
    out = measure.freq(measure.iss_entries(tm_freq_chip(50), 0))
    assert out and all(not lost and abs(measure.freq_hz(n, 5000) - 1e6) <= 1e4 for n, lost in out)
    assert measure.gate_for(1e-3) == (0, 50000) and measure.gate_for(10e-3) == (1, 31250)
    assert measure.gate_for(0.1) == (2, 19531)


def tm_freq_chip(period):
    c = boot(prog("freq", GATE=5000), 0, trace=False)
    n = 5300 * 4
    simulate(c, n, tm.wave(tm.square(period, period / 2, c.cycle, n, 0.3)))
    return c


def test_nec_decode():
    ent = [0x04, 0xFB, 0x08, 0xF7, ("ev", 1), 0x12, 0x34, 0x40, 0xBF, 0x04, 0xFB, 0x08, 0xF6, ("ev", 2)]
    assert measure.nec(ent) == [{"addr": 4, "cmd": 8, "ext": False, "ok": True}, {"repeat": True},
                                {"addr": 0x3412, "cmd": 0x40, "ext": True, "ok": True},
                                {"addr": 4, "cmd": 8, "ext": False, "ok": False}, {"broken": True}]
    got = tm.run_nec([("frame", 0x5A, 0xC3), ("repeat",)], 0, 0.1, seed=3)
    assert measure.nec([("ev", x[1]) if isinstance(x, tuple) else x for x in got]) == \
        [{"addr": 0x5A, "cmd": 0xC3, "ext": False, "ok": True}, {"repeat": True}]


def test_autobaud_decode():
    bit = 50e6 / 115200 * 1.02
    out = tm.run_ab(bit, [0x41, 0x42], 2, gap_after_sync=400)
    r = measure.autobaud([("ev", x[1]) if isinstance(x, tuple) else x for x in out])
    assert abs(r["bit"] - bit) < 0.2 and r["chars"] == [(0x41, False), (0x42, False)] and r["refused"] == 0


def test_choose():
    assert measure.choose(1e6) == ("freq", None, None)
    assert measure.choose(40e3) == ("meter", 0, 0) and measure.choose(60e3) == ("freq", None, None)
    assert measure.choose(60e3, b=4) == ("meter", 0, 0) and measure.choose(250e3, b=4) == ("freq", None, None)
    assert measure.choose(500) == ("meter", 1, 0) and measure.choose(10) == ("meter", 0, 1)
    assert measure.choose(1) == ("meter", 0, 1) and measure.choose(100) == ("meter", 0, 1)
    assert measure.step_up(0, 0) == (1, 0) and measure.step_up(2, 0) == (0, 1)


def test_autobaud_record_resync():
    ent = [0x41, 0x1A, 0x00, 0x20, 0x00, 0x42]               # stale byte before the record BIT 26, FRAC 32, PRE 0
    r = measure.autobaud(ent)
    assert r["bit"] == 26.125 and r["chars"] == [(0x42, False)]
