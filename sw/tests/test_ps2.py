# ps2_dev.s and ps2_host.s against independent PS/2 models (models.Ps2HostModel, Ps2DeviceModel); keyboard and
# mouse personalities and the host driver from host/ps2.py.
import os
import sys
import pytest
from harness import prog, boot, simulate, ROOT
from models import Ps2HostModel, Ps2DeviceModel

sys.path.insert(0, os.path.join(ROOT, "host"))
import ps2                                                            # noqa: E402

PADS, PADS2 = (6, 5), (4, 7)


class Bus:
    # open-drain CLK / DATA: chip pads (one pair per program) and models; released lines read high (pull-ups)
    def __init__(self, models, pads=(PADS,)):
        self.models, self.pads = models, pads
        self.drv = [(False, False)] * len(models)

    def levels(self, c):
        clk = dat = 1
        for cp, dp in self.pads:
            clk &= 0 if (c.uio_oe >> cp & 1) and not (c.uio_out >> cp & 1) else 1
            dat &= 0 if (c.uio_oe >> dp & 1) and not (c.uio_out >> dp & 1) else 1
        for cl, dl in self.drv:
            clk &= not cl
            dat &= not dl
        return int(clk), int(dat)

    def inputs(self, t, c):
        clk, dat = self.levels(c)
        for i, m in enumerate(self.models):
            self.drv[i] = m.step(c.cycle, clk, dat)
        clk, dat = self.levels(c)
        uio = 0xFF
        for cp, dp in self.pads:
            uio &= ~(1 << cp | 1 << dp)
            uio |= clk << cp | dat << dp
        return 8, uio


def items(c, e, k=0):
    return [("ev", v & 63) if v & 0x100 else v for _, v in c.host_out[e]][k:]


class DevRun:
    # ps2_dev.s on engine 0 with a DevLink personality: chip items -> link, link bytes -> chip, one in flight
    def __init__(self, dev, ops, half=200, slot=0, img=None, **kw):
        self.c = boot(img or prog("ps2_dev", HALF=half, IDLE=half), slot)
        self.link = ps2.DevLink(dev)
        self.host = Ps2HostModel(ops, half, **kw)
        self.bus, self.k = Bus([self.host]), 0

    def inputs(self, t, c):
        new = items(c, 0, self.k)
        self.k += len(new)
        for it in new:
            self.link.item(it)
        c.host_in[0] += list(self.link.out())
        return self.bus.inputs(t, c)

    def run(self, n, until=None):
        return simulate(self.c, n, self.inputs, until=until)


def dev_ok(r, half, tol=6):
    h = r.host
    assert h.viol == [], h.viol[:3]
    assert all(abs(d - half) <= tol for _, d in h.phases), sorted(set(d for _, d in h.phases))[:8]
    assert r.c.eng[0].ovr == 0 and not r.c.eng[0].trap


@pytest.mark.parametrize("half,slot", [(200, 0), (28, 1), (100, 2), pytest.param(1500, 3, marks=pytest.mark.slow)])
def test_dev_sends_scan_codes(half, slot):
    kb = ps2.Keyboard()
    r = DevRun(kb, [], half, slot)
    want = kb.key("A") + kb.key("A", True) + kb.key("RIGHT") + kb.key("RIGHT", True) + [0x00, 0xFF, 0x55]
    r.link.queue(want)
    r.run(13 * 2 * half * (len(want) + 2), until=lambda c: len(r.host.frames) == len(want))
    r.run(4 * half)
    assert [f[0] for f in r.host.frames] == want and all(all(f[1:]) for f in r.host.frames)
    assert r.link.sent == want and items(r.c, 0) == [("ev", ps2.DEV_SENT)] * len(want)
    dev_ok(r, half)


@pytest.mark.slow
def test_dev_real_rate_12k5():
    # HALF 2000 (12.5 kHz) and IDLE 50 us as written: keyboard reset + ID over the wire
    r = DevRun(ps2.Keyboard(), [("send", 0xFF), ("wait", 120000), ("send", 0xF2)], img=prog("ps2_dev"), half=2000,
               inh=5500)
    r.run(600000, until=lambda c: len(r.host.frames) == 5)
    assert [f[0] for f in r.host.frames] == [0xFA, 0xAA, 0xFA, 0xAB, 0x83] and r.host.results == [0, 0]
    assert r.link.cmds == [0xFF, 0xF2]
    dev_ok(r, 2000)


@pytest.mark.parametrize("half,slot", [(200, 0), (60, 3)])
def test_dev_keyboard_commands(half, slot):
    ops = [("send", 0xFF), ("wait", 60 * half), ("send", 0xED), ("wait", 30 * half), ("send", 0x07),
           ("wait", 30 * half), ("send", 0xF2), ("wait", 80 * half), ("send", 0xEE), ("wait", 30 * half),
           ("send", 0xF0), ("wait", 30 * half), ("send", 0x00), ("wait", 60 * half)]
    kb = ps2.Keyboard()
    r = DevRun(kb, ops, half, slot)
    r.run(600 * half, until=lambda c: r.host.done)
    assert r.host.results == [0] * 7
    assert [f[0] for f in r.host.frames] == [0xFA, 0xAA, 0xFA, 0xFA, 0xFA, 0xAB, 0x83, 0xEE, 0xFA, 0xFA, 0x02]
    assert kb.leds == 7 and r.link.cmds == [0xFF, 0xED, 0x07, 0xF2, 0xEE, 0xF0, 0x00]
    dev_ok(r, half)


def test_dev_mouse_intellimouse():
    half = 100
    seq = [0xFF, 0xF3, 200, 0xF3, 100, 0xF3, 80, 0xF2, 0xF4]
    ops = []
    for b in seq:
        ops += [("send", b), ("wait", 70 * half)]
    m = ps2.Mouse()
    r = DevRun(m, ops, half, 1)
    r.run(1500 * half, until=lambda c: r.host.done)
    got = [f[0] for f in r.host.frames]
    assert got == [0xFA, 0xAA, 0x00] + [0xFA] * 6 + [0xFA, 0x03, 0xFA] and m.id == 3
    r.link.queue(m.move(5, -3, 1, -1) + m.move(-300, 20, 4, 2))
    r.run(13 * 2 * half * 10, until=lambda c: len(r.host.frames) == len(got) + 8)
    pk = [f[0] for f in r.host.frames[len(got):]]
    assert ps2.mouse_decode(pk[:4], 3) == (5, -3, 1, -1, False)
    assert ps2.mouse_decode(pk[4:], 3) == (-256, 20, 4, 2, True)
    dev_ok(r, half)


@pytest.mark.parametrize("k", [1, 2, 5, 9, 10, 11])
def test_dev_inhibit_mid_frame(k):
    # host takes CLK low after the k-th fall of the first frame: before the stop bit's fall -> ABORT and resend;
    # after the 11th fall the frame is complete
    half = 100
    r = DevRun(ps2.Keyboard(), [("inhibit", k, half // 2, 3 * half)], half, k % 4)
    r.link.queue([0xA5, 0x3C])
    r.run(13 * 2 * half * 5, until=lambda c: len(r.host.frames) == 2)
    r.run(4 * half)
    assert [f[0] for f in r.host.frames] == [0xA5, 0x3C] and all(all(f[1:]) for f in r.host.frames)
    ev = items(r.c, 0)
    if k < 11:
        assert len(r.host.aborted) == 1 and len(r.host.aborted[0]) == k
        assert ev == [("ev", ps2.DEV_ABORT), ("ev", ps2.DEV_SENT), ("ev", ps2.DEV_SENT)]
    else:
        assert r.host.aborted == [] and ev == [("ev", ps2.DEV_SENT)] * 2
    assert r.host.viol == [] and r.c.eng[0].ovr == 0


def test_dev_inhibit_before_start():
    # CLK held low by the host: the device waits; a byte only goes after the bus was idle IDLE clk
    half = 100
    r = DevRun(ps2.Keyboard(), [("hold", 3000), ("wait", 20), ("hold", 400)], half, 2)
    r.link.queue([0x5A])
    r.run(9000, until=lambda c: len(r.host.frames) == 1)
    r.run(400)
    assert [f[0] for f in r.host.frames] == [0x5A] and r.host.aborted == []
    assert items(r.c, 0) == [("ev", ps2.DEV_SENT)]


def test_dev_receive_errors():
    # parity error -> PERR before the byte, ACK given, personality answers FE; stop 0 -> FERR, no ACK, FE;
    # host gives up mid-byte -> RABORT; host DATA hold time short (2 clk after the fall) still read correctly
    half = 120
    ops = [("send", 0xF4, {"bad_parity": True}), ("wait", 40 * half), ("send", 0xF4, {"stop0": True}),
           ("wait", 40 * half), ("send", 0xF4, {"cut": 4}), ("wait", 40 * half), ("send", 0xF4, {"hd": 2}),
           ("wait", 40 * half)]
    r = DevRun(ps2.Keyboard(), ops, half, 3)
    r.run(400 * half, until=lambda c: r.host.done)
    assert r.host.results == [0, None, None, 0]
    ev = items(r.c, 0)
    S = ("ev", ps2.DEV_SENT)
    assert ev == [("ev", ps2.DEV_PERR), 0xF4, S, ("ev", ps2.DEV_FERR), S, ("ev", ps2.DEV_RABORT), 0xF4, S]
    assert [f[0] for f in r.host.frames] == [0xFE, 0xFE, 0xFA] and r.link.errors == [ps2.DEV_FERR, ps2.DEV_RABORT]
    assert r.c.eng[0].ovr == 0


def test_dev_drop_pending_byte():
    # a scan code waits while the host inhibits; the host's command takes the bus first: the waiting byte is
    # dropped (DROP), the response goes out first and the scan code after it
    half = 100
    kb = ps2.Keyboard()
    r = DevRun(kb, [("hold", 1000), ("send", 0xEE), ("wait", 100 * half)], half, 0)
    r.link.queue([0x1C])
    r.run(200 * half, until=lambda c: r.host.done)
    assert [f[0] for f in r.host.frames] == [0xEE, 0x1C] and r.host.results == [0]
    assert items(r.c, 0) == [("ev", ps2.DEV_DROP), 0xEE, ("ev", ps2.DEV_SENT), ("ev", ps2.DEV_SENT)]


def dev_scenario(img, ph, pp, half=40, full=False):
    # T5/T6: a command with a 3-byte answer (rx + tx paths); full: + DROP, inhibit abort, PERR, FERR, RABORT
    ops = [("send", 0xF2, {"hd": 2 + pp}), ("wait", 90 * half)]
    if full:
        ops = [("hold", 30 * half), ("send", 0xEE), ("wait", 60 * half), ("send", 0xF2),
               ("inhibit", 1 + 3 * pp, half // 2, 3 * half), ("wait", 100 * half),
               ("send", 0xED, {"bad_parity": True}), ("wait", 40 * half), ("send", 0xF4, {"stop0": True}),
               ("wait", 40 * half), ("send", 0xF4, {"cut": 3}), ("wait", 40 * half)]
    r = DevRun(ps2.Keyboard(), ops, half, ph, img=img)
    if full:
        r.link.queue([0x1C])
    r.run(1200 * half, until=lambda c: r.host.done)
    want = [0xEE, 0x1C, 0xFA, 0xAB, 0x83, 0xFE, 0xFE] if full else [0xFA, 0xAB, 0x83]
    assert [f[0] for f in r.host.frames] == want, r.host.frames
    return r.c
