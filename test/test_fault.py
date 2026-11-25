# SPDX-FileCopyrightText: © 2026 NotCleo
# SPDX-License-Identifier: Apache-2.0
# Fault injection (docs/spec/programs/fault_design.md), RTL == ISS every clock. FI1: uart_tx -D FAULTS=1 (E0) looped
# back from uo_out[0] into uart_rx (E1, ui_in[7]); faults armed between characters by WRMEM over the host link.

import cocotb

from test_uart import Bench, img_of                                  # noqa: E402 (sets sys.path)
from test_ws2812 import nbench                                       # noqa: E402

FW = 475                               # cleo_mem.inc FAULT_W + engine 0
ARMED = 0x8000


class LoopBench(Bench):
    def pads(self, t):
        c = self.chip
        return (c.uo & 1) << 7 | self.hl.level(c) << 3, (~c.uio_oe | c.uio_out) & 0xFF


@cocotb.test()
async def test_fi1_uart_faults_lockstep(dut):
    b = await nbench(dut, 501, cls=LoopBench)
    await b.load(img_of(["uart_tx", "uart_rx"], **{"uart_tx.s:FAULTS": 1, "uart_rx.s:ENG": 1, "uart_rx.s:ORG": 64,
                                                   "PARITY": 1}))
    b.clear()
    plan = [(0x41, 0), (0x42, ARMED | 2 << 12), (0x43, ARMED | 1 << 12), (0x44, ARMED | 3 << 12 | 20), (0x45, 0)]
    for ch, w in plan:
        if w:
            await b.wrmem(FW, [w])
        b.send(0, [ch])
        await b.run(4000)
        assert await b.rdmem(FW, 1) == [0], "fault word not consumed after 0x%02x" % ch
    want = [0x41, 0x42, ("ev", 1), 0x43, ("ev", 2), 0x00, ("ev", 1), 0x45]
    assert b.stream(1) == want, b.stream(1)
    assert b.notes() == [] and b.chip.eng[0].late == 0 and b.chip.eng[1].ovr == 0
