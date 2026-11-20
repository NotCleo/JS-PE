# SPDX-FileCopyrightText: © 2026 NotCleo
# SPDX-License-Identifier: Apache-2.0
# CAN 2.0A system tests (program not designed in, zero RTL diff; D102-D104): can.s E_TX on E0, E_RX on E1, bus on
# uio[4] open-drain, loaded through the host link; independent bit-level CAN node (sw/tests/models.py CanNode) on the
# wired-AND bus; memory pre-filled with random words; pads == ISS every clock (test_uart Bench). 500 kbit/s.
# GATES=yes: the G subset (C1, one frame).

import cocotb

from test_uart import Bench, GL, MEM_WORDS                           # noqa: E402 (sets sys.path)
from test_ws2812 import PROG, nbench                                 # noqa: E402
from models import CanNode, can_host_tx, can_stuff, can_frame        # noqa: E402
from jspe_asm import Assembler                                       # noqa: E402

BIT = 100
ETX, ERX = 0, 1


class CBench(Bench):
    def __init__(self, dut, seed):
        super().__init__(dut, seed)
        self.node = CanNode(BIT)
        self.bus_log = []                       # (t, bus, chip drives)

    def pads(self, t):
        c = self.chip
        own = (c.uio_oe >> 4 & 1) and not (c.uio_out >> 4 & 1)
        bus = 0 if own or self.node.drive(t) == 0 else 1
        self.node.observe(t, bus)
        self.bus_log.append((t, bus, own))
        uio = ((~c.uio_oe | c.uio_out) & 0xEF) | bus << 4
        return 0x80 | self.hl.level(c) << 3, uio


def can_img():
    d = {"ORG_TX": 0, "ORG_RX": 62} if MEM_WORDS == 128 else {}
    return Assembler([("can.s", open(PROG + "/can.s").read())], {"can.s:%s" % k: str(v) for k, v in d.items()}).run()


async def c_load(b):
    await b.load(can_img())
    await b.run(12 * BIT)                       # bus integration (11 recessive samples)
    b.clear()
    b.bus_log = []


def rx_expect(ident, data, rtr=0, ev=1):
    return [ident >> 8 & 7, ident & 0xFF, (ident & 1) << 7 | rtr << 6 | len(data)] + ([] if rtr else list(data)) + \
        [("ev", ev)]


def rx_stream(b):
    # header byte 0: ID10..8 in bits 2:0, bits above undefined (can.s header): masked for every frame
    out, first = [], True
    for x in b.stream(ERX):
        out.append(x & 7 if first and not isinstance(x, tuple) else x)
        first = isinstance(x, tuple)
    return out


def drive_runs(log, t_from=0):
    out, start = [], None
    for t, _, d in log:
        if t < t_from:
            continue
        if d and start is None:
            start = t
        elif not d and start is not None:
            out.append((start, t - start))
            start = None
    return out


async def until_events(b, e, n, clk, what):
    await b.run(clk, lambda: sum(1 for x in b.stream(e) if isinstance(x, tuple)) >= n, what)


FRAMES = [(0x123, [0x00, 0xFF, 0xA5]), (0x0F8, [0xFF] * 8), (0x000, [0x00] * 8), (0x7C1, [0x07, 0xC1, 0xF0, 0x7C])]


# ---------------- C1: chip transmits, node receives; self-reception; bit timing
@cocotb.test()
async def test_c1_chip_tx(dut):
    b = await nbench(dut, 101, cls=CBench)
    await c_load(b)
    frames = FRAMES[:1] if GL else FRAMES
    exp_rx, intervals = [], set()
    for k, (ident, data) in enumerate(frames):
        b.bus_log = []
        b.send(ETX, can_host_tx(ident, data))
        await until_events(b, ETX, k + 1, 200 * BIT, "C1 SENT %d" % k)
        await until_events(b, ERX, k + 1, 20 * BIT, "C1 RX event %d" % k)
        await b.run(12 * BIT)
        exp_rx += rx_expect(ident, data)
        want = can_stuff(can_frame(ident, data))
        runs = drive_runs(b.bus_log)
        t0 = runs[0][0]
        lv = {t: v for t, v, _ in b.bus_log}
        assert [lv[t0 + 104 + (i - 1) * BIT + BIT // 2] for i in range(1, len(want))] == want[1:], ("C1 bits", k)
        edges = [t for t, n in runs] + [t + n for t, n in runs]
        edges = sorted(e for e in edges if e < t0 + len(want) * BIT + 4)
        intervals |= {e2 - e1 for e1, e2 in zip(edges, edges[1:])}
    assert b.node.frames == [(i, d, 0) for i, d in frames] and not b.node.errors, (b.node.frames, b.node.errors)
    assert b.stream(ETX) == [("ev", 1)] * len(frames)
    assert rx_stream(b) == exp_rx, rx_stream(b)
    assert b.notes() == []
    first = min(intervals)
    grid = all((iv - 4) % BIT == 0 or iv % BIT == 0 for iv in intervals)
    assert grid, sorted(intervals)
    cocotb.log.info("C1: %d frames sent + self-received; bus intervals (clk) %s: bit time %d clk = %.1f kbit/s",
                    len(frames), sorted(intervals)[:8], BIT, 50e3 / BIT)


# ---------------- C2: node transmits (+-1 %), chip receives and ACKs through the relay
@cocotb.test()
async def test_c2_chip_rx(dut):
    b = await nbench(dut, 102, cls=CBench)
    await c_load(b)
    exp = []
    for k, ((ident, data), off) in enumerate(zip(FRAMES, (0.01, -0.01, 0.01, 0.0))):
        b.node = CanNode(BIT * (1 + off))
        await b.run(12 * BIT)                    # node integration
        b.node.send(b.now + 17 + k, ident, data)
        await until_events(b, ERX, k + 1, 200 * BIT, "C2 frame %d" % k)
        await b.run(4 * BIT)
        assert b.node.acked is True and not b.node.errors, (k, b.node.errors)
        exp += rx_expect(ident, data)
    assert rx_stream(b) == exp and b.stream(ETX) == [] and b.notes() == []
    cocotb.log.info("C2: %d frames received and ACKed, node clock +1 / -1 / +1 / 0 %%", len(exp) and 4)


# ---------------- C3: arbitration
@cocotb.test()
async def test_c3_arbitration(dut):
    b = await nbench(dut, 103, cls=CBench)
    await c_load(b)
    # chip 0x101 vs node 0x100 (last ID bit): chip loses, E_RX ACKs the winner, host retries
    b.node.send_on_sof(3, 0x100, [0x22])
    b.send(ETX, can_host_tx(0x101, [0x11]))
    await until_events(b, ETX, 1, 200 * BIT, "C3 lost")
    assert b.stream(ETX) == [("ev", 2)]
    await b.run(60 * BIT)                      # rest of the winner's frame and its ACK slot
    assert b.node.frames == [(0x100, [0x22], 0)] and b.node.acked is True
    b.send(ETX, can_host_tx(0x101, [0x11]))
    await until_events(b, ETX, 2, 200 * BIT, "C3 retry")
    await b.run(12 * BIT)
    assert b.stream(ETX) == [("ev", 2), ("ev", 1)] and b.node.frames[-1] == (0x101, [0x11], 0)
    # chip data frame vs node remote frame, same ID (RTR): chip wins
    b.node.send_on_sof(0, 0x123, [], True)
    b.send(ETX, can_host_tx(0x123, [0x33]))
    await until_events(b, ETX, 3, 200 * BIT, "C3 RTR")
    await b.run(12 * BIT)
    assert b.stream(ETX)[-1] == ("ev", 1) and b.node.lost and b.node.frames[-1] == (0x123, [0x33], 0)
    assert rx_stream(b) == rx_expect(0x100, [0x22]) + rx_expect(0x101, [0x11]) + rx_expect(0x123, [0x33])
    assert not b.node.errors and b.notes() == []
    cocotb.log.info("C3: lost on the last ID bit + retry, won on RTR")


# ---------------- C4: errors and recovery
@cocotb.test()
async def test_c4_errors(dut):
    b = await nbench(dut, 104, cls=CBench)
    await c_load(b)
    # stuff error from the node: E_RX event 3, one 6-bit error flag from the next bit
    body = can_stuff(can_frame(0x123, [0x00, 0x42]))
    k = [i for i in range(20, len(body)) if body[i] != body[i - 1] and
         all(body[i - j] == body[i - 1] for j in range(1, 6))][0]
    b.node.send(b.now + 20, 0, [], bits=body[:k] + [body[k - 1]] + body[k + 1:])
    t_bad = b.node.t0 + k * BIT
    await until_events(b, ERX, 1, 100 * BIT, "C4 stuff error")
    await b.run(20 * BIT)
    assert rx_stream(b)[-1] == ("ev", 3)
    fl = [r for r in drive_runs(b.bus_log, t_bad) if r[1] > 300]
    assert fl and t_bad + BIT <= fl[0][0] <= t_bad + BIT + 25 and 575 <= fl[0][1] <= 625, (fl, t_bad)
    # CRC error: event 2, no ACK
    b.node = CanNode(BIT, flags=False)
    await b.run(12 * BIT)
    fb = can_frame(0x2A5, [0x81, 0x18])
    fb[-3] ^= 1
    b.node.send(b.now + 20, 0, [], bits=can_stuff(fb))
    await until_events(b, ERX, 2, 100 * BIT, "C4 CRC error")
    await b.run(20 * BIT)
    assert rx_stream(b)[-1] == ("ev", 2) and b.node.acked is False
    # missing ACK on a chip frame: TX event 4, then a retry with ACK: event 1
    b.node = CanNode(BIT, ack=False, flags=False)
    await b.run(12 * BIT)
    b.send(ETX, can_host_tx(0x456, [0x01, 0x02]))
    await until_events(b, ETX, 1, 200 * BIT, "C4 ACK error")
    await b.run(20 * BIT)
    b.node.ack = True
    b.send(ETX, can_host_tx(0x456, [0x01, 0x02]))
    await until_events(b, ETX, 2, 200 * BIT, "C4 retry")
    assert b.stream(ETX) == [("ev", 4), ("ev", 1)]
    # a good frame after all errors
    await b.run(12 * BIT)
    n0 = len(rx_stream(b))
    b.node.send(b.now + 20, 0x321, [0x5A])
    await until_events(b, ERX, 5, 100 * BIT, "C4 good frame")
    assert rx_stream(b)[n0:] == rx_expect(0x321, [0x5A]) and b.notes() == []
    cocotb.log.info("C4: stuff error (flag at +%d clk, %d clk), CRC error, missing ACK + retry, recovery",
                    fl[0][0] - t_bad - BIT, fl[0][1])
