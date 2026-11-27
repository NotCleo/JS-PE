# SPDX-FileCopyrightText: © 2026 NotCleo
# SPDX-License-Identifier: Apache-2.0
# PS/2 family (docs/spec/programs/ps2_design.md), RTL == ISS every clock. PS2-R1: ps2_host.s (E0, CLK uio[6], DATA
# uio[5]) and ps2_dev.s (E1, CLK uio[4], DATA uio[7]) on one open-drain bus (uio[6] = uio[4], uio[5] = uio[7],
# pull-ups), device CLK half period 100 clk (250 kHz; the programs' timer constants scaled to it). The device runs
# the host/ps2.py keyboard behind its DevLink; the host sends commands, inhibits, and takes the bus from a waiting
# scan code (DROP).

import os
import sys

import cocotb

from test_uart import Bench, img_of, ROOT                            # noqa: E402 (sets sys.path)
from test_ws2812 import nbench                                       # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "host"))
import ps2                                                           # noqa: E402

HALF = 100


class Ps2Bench(Bench):
    def pads(self, t):
        c = self.chip
        assert not (c.uio_oe & c.uio_out & 0xF0), "PS/2 line driven high at cycle %d" % t

        def lo(k):
            return c.uio_oe >> k & 1 and not c.uio_out >> k & 1
        clk = 0 if lo(6) or lo(4) else 1
        dat = 0 if lo(5) or lo(7) else 1
        uio = ((~c.uio_oe | c.uio_out) & 0x0F) | clk << 6 | clk << 4 | dat << 5 | dat << 7
        return 0x80 | self.hl.level(c) << 3, uio


async def pump(b, n=64):
    # run n clk, then serve the device personality: chip items -> DevLink, DevLink byte -> engine 1
    await b.run(n)
    new = b.stream(1)[b.k1:]
    b.k1 += len(new)
    for it in new:
        b.link.item(it)
    out = b.link.out()
    if out:
        b.send(1, out)


async def until(b, cond, what, limit=200000):
    t0 = b.now
    while not cond():
        assert b.now - t0 < limit, "timeout waiting for " + what
        await pump(b)


def results(b):
    return ps2.h_parse(b.stream(0))


async def command(b, cmd, nresp):
    # host command: ACK on the wire, then FA and nresp response bytes
    k = len(results(b))
    b.send(0, ps2.h_send(cmd))
    await until(b, lambda: len(results(b)) >= k + 2 + nresp, "answer to %02X" % cmd)
    r = results(b)[k:]
    assert r[0] == ("ack",) and r[1] == ("byte", ps2.ACK, True), r
    return [x[1] for x in r[2:]]


@cocotb.test()
async def test_ps2_r1_host_to_device(dut):
    b = await nbench(dut, 901, cls=Ps2Bench)
    q = HALF // 16
    await b.load(img_of(["ps2_host", "ps2_dev"], **{
        "ps2_dev.s:ENG": 1, "ps2_dev.s:PINS2": 1, "ps2_dev.s:HALF": HALF, "ps2_dev.s:IDLE": HALF,
        "ps2_host.s:TBIT": 4 * q, "ps2_host.s:TDSU": 1, "ps2_host.s:TFIRST": 20 * q}))
    b.clear()
    kb = ps2.Keyboard()
    b.link, b.k1 = ps2.DevLink(kb), 0
    await pump(b, 200)
    assert await command(b, 0xFF, 1) == [ps2.BAT_OK]
    assert await command(b, 0xED, 0) == [] and await command(b, 0x06, 0) == [] and kb.leds == 6
    assert await command(b, 0xF2, 2) == [0xAB, 0x83]
    # host inhibit (0x01) while the keyboard has a scan code waiting: the device polls; the host's command takes
    # the bus first: the waiting byte is dropped (DROP), EE is echoed, then the scan code goes out
    k = len(results(b))
    b.send(0, ps2.h_inhibit())
    await pump(b, 400)
    b.link.queue(kb.key("A"))
    await until(b, lambda: b.link.inflight is not None, "scan code handed to the chip")
    await pump(b, 20 * HALF)
    assert len(results(b)) == k
    b.send(0, ps2.h_send(0xEE))
    await until(b, lambda: len(results(b)) >= k + 3, "echo and scan code")
    assert results(b)[k:] == [("ack",), ("byte", ps2.ECHO, True), ("byte", 0x1C, True)], results(b)[k:]
    await pump(b, 4 * HALF)
    assert ("ev", ps2.DEV_DROP) in b.stream(1) and b.link.cmds == [0xFF, 0xED, 0x06, 0xF2, 0xEE]
    assert b.link.sent == [0xFA, 0xAA, 0xFA, 0xFA, 0xFA, 0xAB, 0x83, 0xEE, 0x1C]
    assert b.notes() == [] and b.chip.eng[0].ovr == 0 and b.chip.eng[1].ovr == 0
