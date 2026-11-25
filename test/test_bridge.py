# SPDX-FileCopyrightText: © 2026 NotCleo
# SPDX-License-Identifier: Apache-2.0
# On-chip bridges (docs/spec/programs/bridge_design.md), host out of the data path; memory random before the load;
# pads == ISS every clock (Bench). BR1: bridge_uart_spi.s, UART sender on ui_in[7], SPI checker on uio[3,0,1].
# BR2: bridge_i2c_uart.s, I2C controller model on uio[6,5], UART decoder on uo_out[0].

import cocotb

from test_uart import Bench, GL, img_of                              # noqa: E402 (sets sys.path)
from test_ws2812 import nbench                                       # noqa: E402
from test_i2c import I2cBus, Ctl, tgt_timing, SCL, SDA, FM400, R_FM  # noqa: E402
from models import UartSender, uart_decode, spi_ctrl_check           # noqa: E402

UBIT = 14                              # 3.57 Mbaud, the bridge's max
RING = 480


class BrBench(Bench):
    def __init__(self, dut, seed):
        super().__init__(dut, seed)
        self.bus = None

    def pads(self, t):
        c = self.chip
        uio = (~c.uio_oe | c.uio_out) & 0xFF
        if self.bus is not None:
            hi = c.uio_oe & c.uio_out & (1 << SCL | 1 << SDA)
            assert not hi, "open-drain pad driven high (uio %02x) at cycle %d" % (hi, t)
            uio = self.bus.step(t, c, uio)
        return self.line.level(t + 1 - 1e-6) << 7 | self.hl.level(c) << 3, uio


def lv(c, k, t0, init):
    out, v = [], init
    for t, uo, o, oe in c.pad_trace:
        if t > t0 and (o >> k & 1) != v:
            v = o >> k & 1
            out.append((t, v))
    return out


def spi_bytes(b, t0, sbit):
    c = b.chip
    win = spi_ctrl_check(lv(c, 3, t0, 0), lv(c, 0, t0, 1), lv(c, 1, t0, c.uio_out >> 1 & 1), 0, 8, sbit, sbit - 2, t0=t0)
    assert all(len(x["mosi"]) == 1 for x in win)
    return [x["mosi"][0] for x in win]


def ring_empty(c):
    return all(c.mem[RING + k] >> 15 for k in range(32))


async def us_case(b, sbit, data):
    await b.load(img_of(["bridge_uart_spi"], UBIT=UBIT, BIT=sbit))
    await b.run(20)
    b.clear()
    t0 = b.now
    s = b.line.add(UartSender(UBIT, data, t0=t0 + 30))
    await b.run(int(s.end) - t0 + 100)
    await b.run(40 * (9 * sbit + 100), lambda: ring_empty(b.chip) and b.chip.uio_out & 1 and b.now > s.end + 400,
                "ring drained")
    await b.run(9 * sbit + 100)
    return s, spi_bytes(b, t0, sbit), b.stream(1)


@cocotb.test()
async def test_br1_uart_spi(dut):
    b = await nbench(dut, 401, cls=BrBench)
    n = 8 if GL else 24
    data = [b.r.getrandbits(8) for _ in range(n)]
    s, got, ev = await us_case(b, 6, data)                       # sustained: SPI BIT 6 at 3.57 Mbaud
    assert got == data and ev == [], (got, ev)
    if GL:
        return
    data = [b.r.getrandbits(8) for _ in range(80)]
    s, got, ev = await us_case(b, 20, data)                      # SPI BIT 20: ring fills, first drop at byte 68
    assert ev and all(x == ("ev", 2) for x in ev), ev
    it = iter(data)
    assert all(x in it for x in got)                             # order kept, only dropped bytes missing
    assert len(got) + len(ev) == len(data) and got[:68] == data[:68]
    assert all(e.ovr == 0 for e in b.chip.eng) and b.notes() == []
    cocotb.log.info("BR1: %d bytes at UBIT %d exact; SPI BIT 20: %d delivered, %d dropped (event 2)",
                    n, UBIT, len(got), len(ev))


@cocotb.test()
async def test_br2_i2c_uart(dut):
    b = await nbench(dut, 402, cls=BrBench)
    for ubit, n in ((50, 6 if GL else 12), (434, 3 if GL else 6)):
        await b.load(img_of(["bridge_i2c_uart"], UBIT=ubit))
        await b.run(20)
        b.clear()
        b.mark_tx()
        data = [b.r.getrandbits(8) for _ in range(n)]
        script = [("S",), ("W", 0x85), ("P",), ("S",), ("W", 0x84)] + [("W", x) for x in data] + [("P",)]
        m = Ctl(script, **FM400)
        b.bus = I2cBus(R_FM)
        b.bus.models = [m]
        await b.run(10 ** 6, lambda: b.now % 8 == 0 and m.done(), "I2C controller model")
        await b.run(25 * ubit)
        assert m.acks == [1] + [0] * (n + 1), m.acks           # read NACKed, address + data ACKed
        assert b.chip.uio_oe & (1 << SCL | 1 << SDA) == 0
        tgt_timing(b.bus, mode="Fm", tlow=FM400["tlow"], rise=R_FM)
        frames = uart_decode(b.tx_ch, ubit)
        assert [f[1] for f in frames] == data and all(f[3] for f in frames)
        assert b.stream(0) == [] and b.stream(1) == [] and b.notes() == []
        b.bus = None
    cocotb.log.info("BR2: writes forwarded at UBIT 50 and 434 (SCL stretched), read NACKed")
