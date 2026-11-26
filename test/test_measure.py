# SPDX-FileCopyrightText: © 2026 NotCleo
# SPDX-License-Identifier: Apache-2.0
# Measurement (docs/spec/programs/measure_design.md), RTL == ISS every clock. MEAS-R: meter.s on E0 and freq.s on E1
# share one input (ui_in[0]): 2000-clk periods with high 1 / 1976 / 1000 / 500 clk (high 1 = the minimum; low 24 = the
# shortest low whose reading is above the host's ambiguity flag, <= 22);
# falls >= 1024 clk apart: a meter record (<= 400 clk at B = 4 with escapes) may wait for a freq record.

import bisect
import os
import sys

import cocotb

from test_uart import Bench, img_of                                  # noqa: E402 (sets sys.path)
from test_ws2812 import nbench                                       # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "host"))
import measure                                                       # noqa: E402


class MeasBench(Bench):
    edges = ()

    def pads(self, t):
        c = self.chip
        assert c.uio_oe == 0, "measurement drove a pad at cycle %d" % t
        tt = t + 1 - 1e-6
        k = bisect.bisect_right([e[0] for e in self.edges], tt)
        lv = self.edges[k - 1][1] if k else 0
        return 0x80 | self.hl.level(c) << 3 | lv, (~c.uio_oe | c.uio_out) & 0xFF


@cocotb.test()
async def test_meas_r_meter_freq(dut):
    b = await nbench(dut, 701, cls=MeasBench)
    await b.load(img_of(["meter", "freq"], **{"freq.s:ENG": 1, "freq.s:ORG": 40, "freq.s:GATE": 20000}))
    b.clear()
    highs = [1, 1976, 1000, 500] * 6
    t, edges, phases = b.now + 100.3, [], []
    for h in highs:
        edges += [(t, 1), (t + h, 0)]
        phases += [h, 2000 - h]
        t += 2000
    edges += [(t, 1), (t + 100, 0)]
    b.edges = edges
    await b.run(int(t - b.now) + 21000)
    got = measure.meter(b.stream(0))[:len(phases)]
    assert len(got) == len(phases) and all(p[2] is None and abs(p[1] - d) <= 1 for p, d in zip(got, phases)), got
    assert [p[0] for p in got] == [1, 0] * len(highs)
    win = measure.freq(b.stream(1))
    assert len(win) >= 2 and all(not lost and 9 <= n <= 11 for n, lost in win[:-1]), win
    assert b.notes() == [] and b.chip.eng[0].ovr == 0 and b.chip.eng[1].ovr == 0

