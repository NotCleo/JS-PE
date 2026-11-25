# SPDX-FileCopyrightText: © 2026 NotCleo
# SPDX-License-Identifier: Apache-2.0
# Monitor (docs/spec/programs/monitor_design.md), RTL == ISS every clock. MON-R: spi_mon.s on E0 (MOSI) and E1 (MISO)
# at the maximum SCK (sampling edges 4 clk apart, 12.5 MHz), modes 0 and 3; SPI models on uio[3:0]; no pad enabled.

import cocotb

from test_uart import Bench, img_of                                  # noqa: E402 (sets sys.path)
from test_ws2812 import nbench                                       # noqa: E402
from models import SpiController                                     # noqa: E402


class MonBench(Bench):
    frames = ()
    cpol = 0

    def pads(self, t):
        c = self.chip
        assert c.uio_oe == 0, "monitor drove a pad at cycle %d" % t
        tt = t + 1 - 1e-6
        x = next((f for f in self.frames if tt < f[0].cs_hi + 1), None)
        if x is None or tt < x[0].cs_lo:
            uio = 1 | (self.cpol << 3)                                 # CS high, SCK at rest
        else:
            m, s = x
            uio = m.sck(tt) << 3 | m.cs(tt) | m.mosi(tt) << 1 | s.mosi(tt) << 2
        return 0x80 | self.hl.level(c) << 3, uio


async def mon_run(dut, mode, seed):
    b = await nbench(dut, seed, cls=MonBench)
    b.cpol = mode >> 1
    await b.load(img_of(["spi_mon"]))
    b.clear()
    data = [([0xA5, 0x3C], [0x5A, 0xC3]), ([0x00, 0xFF], [0x81, 0x7E]), ([0x1B, 0x96], [0x69, 0x1B])]
    t, fr = b.now + 100, []
    for k, (mo, mi) in enumerate(data):
        m = SpiController(4, mode, mo, phase=0.25 * k, t0=t, cs_setup=1, cs_hold=1)
        s = SpiController(4, mode, mi, phase=0.25 * k, t0=t, cs_setup=1, cs_hold=1)
        fr.append((m, s))
        t = m.cs_hi + 1200                                             # the link drains 8 entries in between
    b.frames = fr
    await b.run(int(t - b.now))
    for e, k in ((0, 0), (1, 1)):
        want = []
        for d in data:
            want += [("ev", 1)] + d[k] + [("ev", 2)]
        assert b.stream(e) == want, (e, b.stream(e))
    assert b.notes() == [] and b.chip.eng[0].ovr == 0 and b.chip.eng[1].ovr == 0


@cocotb.test()
async def test_mon_r_spi_12m5_mode0(dut):
    await mon_run(dut, 0, 601)


@cocotb.test()
async def test_mon_r_spi_12m5_mode3(dut):
    await mon_run(dut, 3, 602)
