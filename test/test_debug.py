# SPDX-FileCopyrightText: © 2026 NotCleo
# SPDX-License-Identifier: Apache-2.0
# Debug family (docs/spec/programs/debug_design.md), RTL == ISS every clock. DBG-R1: jtag_ctrl.s (E0) drives
# jtag_tap.s (E1) on the same pads (TCK uio[3], TMS uio[0], TDI uio[1], TDO uio[2]); commands from host/debug.py,
# expected TDO from the independent JtagTap model.

import os
import sys

import cocotb

from test_uart import Bench, img_of, ROOT                            # noqa: E402 (sets sys.path)
from test_ws2812 import nbench                                       # noqa: E402
from models import JtagTap                                           # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "host"))
import debug                                                         # noqa: E402

ID0 = 0x1C1E0001


@cocotb.test()
async def test_dbg_r1_jtag_ctrl_to_tap(dut):
    b = await nbench(dut, 801)
    await b.load(img_of(["jtag_ctrl", "jtag_tap"], **{"jtag_tap.s:ENG": 1, "jtag_ctrl.s:BIT": 40,
                                                      "jtag_ctrl.s:SMP": 20}))
    b.clear()
    j = debug.Jtag().reset()
    j.scan(False, 0, 40).scan(True, 0xA, 4).scan(False, 0xC3, 8).scan(False, 0x3C, 8)
    j.goto("PDR").goto("SHDR").clocks(3).goto("UDR")
    j.scan(True, 0x3, 4).scan(False, 0b101, 3).reset().scan(False, 0, 32)
    ref = JtagTap(4, ID0, 0xE, {0xA: [8, 0x5A]})
    want = []
    for h, v, n, t in j.cmds:                                         # model TDO per command
        held, on_tms, got = h & 1, h >> 1 & 1, 0
        for i in range(n):
            bit = v >> i & 1
            tms, tdi = (bit, held) if on_tms else (held, bit)
            got |= (ref.tdo if ref.tdo is not None else 1) << i
            ref.rise(tms, tdi)
            ref.fall()
        want.append(got)
    b.send(0, j.data())
    await b.run(10 ** 6, lambda: len(b.stream(0)) >= len(j.cmds), "TDO bytes")
    rx = b.stream(0)
    res = j.result(rx)
    assert res[0] & 0xFFFFFFFF == ID0 and res[2] == 0x5A and res[3] == 0xC3 and res[6] == ID0, [hex(x) for x in res]
    assert rx == want                                                 # every TDO bit, walks included
    assert b.stream(1) == [u for _, u in ref.updates] and len(ref.updates) == 3
    assert b.notes() == [] and b.chip.eng[0].ovr == 0 and b.chip.eng[1].ovr == 0


# DBG-R2: swd_host.s (E0, SWCLK uio[3], SWDIO uio[1]) talks to swd_dp.s (E1, SWDIO uio[4]); uio[1] and uio[4] are
# one wire (pull-up when nobody drives); SWCLK period 64 clk (the DP's back-to-back limit); AP box served by WRMEM.
class SwdBench(Bench):
    def pads(self, t):
        c = self.chip
        lv = c.uio_out >> 1 & 1 if c.uio_oe >> 1 & 1 else (c.uio_out >> 4 & 1 if c.uio_oe >> 4 & 1 else 1)
        assert not (c.uio_oe >> 1 & c.uio_oe >> 4 & 1), "SWDIO driven by both at cycle %d" % t
        uio = ((~c.uio_oe | c.uio_out) & 0xED) | lv << 1 | lv << 4
        return 0x80 | self.hl.level(c) << 3, uio


async def xfer(b, data, n):
    b.send(0, data)
    await b.run(10 ** 6, lambda: len(b.stream(0)) >= b.k + n, "SWD reply")
    r = b.stream(0)[b.k:b.k + n]
    b.k += n
    return r


def word(r):
    return r[0] | r[1] << 8 | r[2] << 16 | r[3] << 24


@cocotb.test()
async def test_dbg_r2_swd_host_to_dp(dut):
    b = await nbench(dut, 802, cls=SwdBench)
    b.k = 0
    await b.load(img_of(["swd_host", "swd_dp"], **{"swd_dp.s:ENG": 1, "swd_dp.s:DIO": 4, "swd_host.s:BIT": 64,
                                                   "swd_host.s:SMP": 32}))
    b.clear()
    box = 455 + 5
    await xfer(b, debug.swd_line_reset(), 8)
    r = await xfer(b, debug.swd_read(0, 0), 6)
    assert r[0] == 1 and word(r[1:]) == 0x0C1E1001 and r[5] & 1 == debug.parity(0x0C1E1001), r
    assert await xfer(b, debug.swd_write(0, 4, 0x50000000), 1) == [1]
    r = await xfer(b, debug.swd_read(0, 4), 6)
    assert r[0] == 1 and word(r[1:]) == 0xF0000000, r
    assert await xfer(b, debug.swd_write(1, 4, 0xCAFE1B1B), 1) == [1]          # AP write -> box
    await b.run(10 ** 5, lambda: len(b.stream(1)) >= 1, "box request")
    assert b.stream(1) == [0x15] and await b.rdmem(box + 2, 3) == [0x15, 0x1B1B, 0xCAFE]   # AP W A4
    r = await xfer(b, debug.swd_read(1, 4), 1)                                  # WAIT: box still busy
    assert r == [2]
    await b.wrmem(box, [0x5678, 0x1234, 0])                                     # served: RDB, ST idle
    r = await xfer(b, debug.swd_read(1, 4), 6)                                  # posted: returns RDB
    assert r[0] == 1 and word(r[1:]) == 0x12345678, r
    await b.wrmem(box, [0x0BAD, 0x0BAD, 0])
    r = await xfer(b, debug.swd_read(0, 0xC), 6)                                # RDBUFF
    assert r[0] == 1 and word(r[1:]) == 0x0BAD0BAD, r
    w = bytearray(debug.swd_write(1, 0, 7))
    w[5] ^= 1
    assert await xfer(b, bytes(w), 1) == [1]                                    # bad parity -> WDATAERR
    assert await xfer(b, debug.swd_write(1, 0, 7), 1) == [4]                    # FAULT
    r = await xfer(b, debug.swd_read(0, 4), 6)
    assert r[0] == 1 and word(r[1:]) == 0xF0000080, r
    assert await xfer(b, debug.swd_write(0, 0, 8), 1) == [1]                    # ABORT WDERRCLR
    await xfer(b, debug.swd_raw([0] * 8), 1)
    assert b.stream(1) == [0x15, 0x17] and b.notes() == []
    assert all(e.ovr == 0 for e in b.chip.eng[:2])
