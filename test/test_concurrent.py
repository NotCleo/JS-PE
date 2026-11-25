# SPDX-FileCopyrightText: © 2026 NotCleo
# SPDX-License-Identifier: Apache-2.0
# CAN + UART at the same time from one 512-word image (D131): can.s E_TX E0 / E_RX E1 on uio[4], uart_tx.s E2 on
# uo_out[0], uart_rx.s E3 on ui_in[7]; CanNode on the bus, UART sender on RXD; pads == ISS every clock (Bench).
# The host feeds two input streams over one link, switching streams when H_RDY says the selected one is full.

import cocotb

from test_uart import ESC, GL, img_of, check_tx                      # noqa: E402 (sets sys.path)
from test_ws2812 import nbench                                       # noqa: E402
from test_can import CBench, rx_expect, rx_stream, until_events, BIT  # noqa: E402
from models import UartSender, can_host_tx                           # noqa: E402

ETX, ERX, UTX, URX = 0, 1, 2, 3
NOM = 50
F_CHIP = (0x123, [0x00, 0xFF, 0xA5, 0x5A, 0x01, 0x80, 0x7E, 0x3C])
F_NODE = (0x0F8, [0xFF] * 8)


class CUBench(CBench):
    def __init__(self, dut, seed):
        super().__init__(dut, seed)
        self.pend, self.sel = {}, None

    def feed(self):
        hl, c = self.hl, self.chip
        if c is None or not hl.idle() or not any(self.pend.values()):
            return
        e = self.sel
        if e is not None and self.pend.get(e) and not c.link.h_rdy:      # h_rdy set = selected stream full
            b = self.pend[e].pop(0)
            hl.send(bytes([ESC, ESC]) if b == ESC else bytes([b]), is_data=True)
            return
        nxt = [k for k in sorted(self.pend) if self.pend[k] and k != e]
        if nxt:
            self.sel = nxt[0]
            hl.send(bytes([ESC, self.sel, ESC, self.sel]))

    def pads(self, t):
        self.feed()
        ui, uio = super().pads(t)
        return (ui & 0x7F) | self.line.level(t + 1 - 1e-6) << 7, uio


@cocotb.test()
async def test_cu1_can_uart_together(dut):
    b = await nbench(dut, 211, cls=CUBench)
    n = 16 if GL else 48
    await b.load(img_of(["can", "uart_tx", "uart_rx"], **{"uart_tx.s:ENG": 2, "uart_rx.s:ENG": 3}))
    await b.run(12 * BIT)                        # CAN bus integration
    b.mark_tx()
    b.clear()
    b.bus_log = []
    utx = [b.r.choice((ESC, b.r.getrandbits(8))) for _ in range(n)]
    urx = [b.r.choice((ESC, b.r.getrandbits(8))) for _ in range(n)]
    b.pend = {ETX: list(can_host_tx(*F_CHIP)), UTX: list(utx)}
    b.line.add(UartSender(NOM * 1.02, urx, t0=b.now + 30))
    await until_events(b, ETX, 1, 300 * BIT, "CU1 CAN SENT")
    t_sent = b.now
    await b.run(12 * BIT)
    b.node.send(b.now + 37, *F_NODE)
    await until_events(b, ERX, 2, 300 * BIT, "CU1 CAN RX")
    t_rx = b.now
    await b.run(10 ** 6, lambda: len(b.rx_data(URX)) >= n and b.tx_done(n, NOM), "CU1 UART traffic")
    await b.run(4 * BIT)
    assert b.node.frames == [(F_CHIP[0], F_CHIP[1], 0), (F_NODE[0], F_NODE[1], 0)] and not b.node.errors
    assert b.node.acked is True
    assert b.stream(ETX) == [("ev", 1)]
    assert rx_stream(b) == rx_expect(*F_CHIP) + rx_expect(*F_NODE), rx_stream(b)
    m = check_tx(b.tx_ch, NOM, utx)
    assert b.stream(URX) == urx
    assert b.notes() == [] and all(e.ovr == 0 for e in b.chip.eng)
    last_tx = b.tx_ch[-1][0]
    assert t_rx < last_tx, (t_rx, last_tx)       # both CAN frames finished while UART TX was still sending
    cocotb.log.info("CU1: CAN chip frame SENT at %d, node frame received at %d; UART %d bytes each way, TX %s",
                    t_sent, t_rx, n, m)
