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


# ---------------- ps2_host.s
def host_defs(half):
    # timer steps of 16 clk scaled to the device half period (the program's defaults are for 50 MHz, 12.5 kHz)
    return dict(TBIT=max(2, 4 * half // 16), TDSU=1, TFIRST=20 * half // 16)


class HostRun:
    # ps2_host.s on engine 0 against a Ps2DeviceModel; port.send / recv for ps2.transact
    def __init__(self, dev, half=100, slot=0, img=None, inject=None):
        self.c = boot(img or prog("ps2_host", **host_defs(half)), slot)
        self.dev, self.bus, self.k, self.res = dev, Bus([dev]), 0, []
        self.inject = inject

    def inputs(self, t, c):
        if self.inject:
            self.inject(self, c)
        return self.bus.inputs(t, c)

    def run(self, n, until=None):
        return simulate(self.c, n, self.inputs, until=until)

    def results(self):
        return ps2.h_parse(items(self.c, 0))

    def send(self, b):
        self.c.host_in[0] += list(b)

    def recv(self):
        assert self.run(10 ** 6, until=lambda c: len(self.results()) > self.k), "no result"
        self.k += 1
        return self.results()[self.k - 1]


def kb_model(kb):
    return lambda v, pok, sok: [ps2.RESEND] if not pok else kb.command(v)[0]


def host_ok(r, inh_min):
    d = r.dev
    assert d.viol == [], d.viol[:3]
    assert all(x >= inh_min for x in d.inh), d.inh
    assert r.c.eng[0].ovr == 0 and not r.c.eng[0].trap


@pytest.mark.parametrize("half,slot", [(100, 0), (28, 1), (300, 2), (14, 3)])
def test_host_receives_frames(half, slot):
    data = [0x1C, 0xF0, 0x1C, 0xE0, 0x74, 0x00, 0xFF, 0xAA]
    d = Ps2DeviceModel(half, [(b, {}) for b in data])
    r = HostRun(d, half, slot)
    r.run(13 * 2 * half * (len(data) + 2), until=lambda c: len(r.results()) == len(data))
    r.run(2 * half)
    assert r.results() == [("byte", b, True) for b in data] and d.sent == data and d.aborts == 0
    host_ok(r, 0)


@pytest.mark.parametrize("half,slot", [(100, 1), (40, 2)])
def test_host_keyboard_session(half, slot):
    kb = ps2.Keyboard()
    d = Ps2DeviceModel(half, on_rx=kb_model(kb))
    r = HostRun(d, half, slot)
    assert ps2.transact(r, 0xFF, nresp=1) == [ps2.BAT_OK]
    assert ps2.transact(r, 0xED, [0x05]) == [] and kb.leds == 5
    assert ps2.transact(r, 0xF2, nresp=2) == [0xAB, 0x83]
    assert ps2.transact(r, 0xEE) == [ps2.ECHO]
    assert ps2.transact(r, 0xF0, [0x00], nresp=1) == [2]
    assert [g[0] for g in d.got] == [0xFF, 0xED, 0x05, 0xF2, 0xEE, 0xF0, 0x00] and all(g[1] and g[2] for g in d.got)
    host_ok(r, 16 * host_defs(half)["TBIT"])


def test_host_mouse_intellimouse():
    half = 60
    m = ps2.Mouse()
    d = Ps2DeviceModel(half, on_rx=kb_model(m))
    r = HostRun(d, half, 3)
    assert ps2.transact(r, 0xFF, nresp=2) == [ps2.BAT_OK, 0x00]
    for rate in (200, 100, 80):
        ps2.transact(r, 0xF3, [rate])
    assert ps2.transact(r, 0xF2, nresp=1) == [3]
    ps2.transact(r, 0xF4)
    d.tx += [(b, {}) for b in m.move(-7, 12, 2, 1)]
    pk = [r.recv()[1] for _ in range(4)]
    assert ps2.mouse_decode(pk, 3) == (-7, 12, 2, 1, False)
    host_ok(r, 16 * host_defs(half)["TBIT"])


def test_host_frame_errors():
    # device frames with a parity error, start 1, stop 0, and one cut after 4 bits (timeout); then a good one
    half = 80
    tx = [(0x11, {"bad_parity": True}), (0x22, {"start1": True}), (0x33, {"stop0": True}), (0x44, {"cut": 4}),
          (0x55, {})]
    d = Ps2DeviceModel(half, tx, idle=8 * half)
    r = HostRun(d, half, 1)
    r.run(13 * 2 * half * 12, until=lambda c: len(r.results()) == 5)
    assert r.results() == [("byte", 0x11, False), ("ferr",), ("ferr",), ("tmo",), ("byte", 0x55, True)]
    assert items(r.c, 0)[:2] == [("ev", ps2.HOST_PERR), 0x11]
    host_ok(r, 0)


def test_host_send_errors():
    # no device clock after the request-to-send -> TMO; pulse 11 without ACK -> NACK; the lines end released
    half = 50
    d = Ps2DeviceModel(half, rx={"noclock": True})
    r = HostRun(d, half, 2)
    r.send(ps2.h_send(0xF4))
    assert r.recv() == ("tmo",)
    d.rxo = {"nack": True}
    r.send(ps2.h_send(0xF4))
    assert r.recv() == ("nack",)
    r.run(2 * half)
    assert d.got == [(0xF4, True, True)]
    d.rxo = {}
    r.send(ps2.h_send(0xF5))
    assert r.recv() == ("ack",)
    r.run(10 * half)
    assert (r.c.uio_oe >> 6 & 1, r.c.uio_oe >> 5 & 1) == (0, 0)
    host_ok(r, 16 * host_defs(half)["TBIT"])


def test_host_inhibit_and_release():
    # 0x01 holds CLK low: the device keeps its bytes; 0x00 releases them
    half = 60
    d = Ps2DeviceModel(half)
    r = HostRun(d, half, 0)
    r.send(ps2.h_inhibit())
    r.run(200)
    d.tx += [(0x5A, {}), (0xA5, {})]
    r.run(40 * half)
    assert r.results() == [] and d.sent == [] and r.c.uio_oe >> 6 & 1
    r.send(ps2.h_release())
    r.run(13 * 2 * half * 4, until=lambda c: len(r.results()) == 2)
    assert r.results() == [("byte", 0x5A, True), ("byte", 0xA5, True)]
    host_ok(r, 0)


def test_host_inhibits_device_mid_frame():
    # the send command arrives while the device has just put its start bit on DATA: the device aborts, takes the
    # command, answers FA, then sends its byte again
    half = 100
    kb = ps2.Keyboard()
    d = Ps2DeviceModel(half, [(0x1C, {})], on_rx=kb_model(kb))
    st = {"done": False}

    def inject(run, c):
        if not st["done"] and d.dat_lo:
            st["done"] = True
            run.send(ps2.h_send(0xF4))
    r = HostRun(d, half, 1, inject=inject)
    r.run(13 * 2 * half * 6, until=lambda c: len(r.results()) == 3)
    assert r.results() == [("ack",), ("byte", 0xFA, True), ("byte", 0x1C, True)] and d.aborts == 1
    host_ok(r, 16 * host_defs(half)["TBIT"])


@pytest.mark.slow
def test_host_real_rate():
    # program constants as written (50 MHz) against a 12.5 kHz device: reset + ID
    kb = ps2.Keyboard()
    d = Ps2DeviceModel(2000, on_rx=kb_model(kb), idle=2500)
    r = HostRun(d, img=prog("ps2_host"))
    assert ps2.transact(r, 0xFF, nresp=1) == [ps2.BAT_OK]
    assert ps2.transact(r, 0xF2, nresp=2) == [0xAB, 0x83]
    host_ok(r, 16 * 625)


def host_scenario(img, ph, pp, half=40, full=False):
    # T5/T6: a command with a 2-byte answer (send + receive paths); full: + inhibit / release, frame errors,
    # timeouts, NACK, a device frame cut by the host's request-to-send
    kb = ps2.Keyboard()
    d = Ps2DeviceModel(half, on_rx=kb_model(kb), idle=half + pp)
    r = HostRun(d, half, ph, img=img)
    assert ps2.transact(r, 0xF2, nresp=2) == [0xAB, 0x83]
    if full:
        r.send(ps2.h_inhibit())
        d.idle = 5 * half                    # > TBIT: the frame after the cut one starts after the timeout
        d.tx += [(0x11, {"bad_parity": True}), (0x22, {"start1": True}), (0x33, {"stop0": True}),
                 (0x44, {"cut": 3}), (0x55, {})]
        r.run(20 * half)
        r.send(ps2.h_release())
        want = [("byte", 0x11, False), ("ferr",), ("ferr",), ("tmo",), ("byte", 0x55, True)]
        assert [r.recv() for _ in range(5)] == want
        d.rxo = {"noclock": True}
        r.send(ps2.h_send(0xF4))
        assert r.recv() == ("tmo",)
        d.rxo = {"nack": True}
        r.send(ps2.h_send(0xF4))
        assert r.recv() == ("nack",) and r.recv() == ("byte", 0xFA, True)    # the model answers anyway
        d.rxo, st = {}, {}

        def inject(run, c):
            if not st and d.dat_lo:
                st["sent"] = run.send(ps2.h_send(0xF4))
        r.inject = inject
        d.tx.append((0x1C, {}))
        assert [r.recv() for _ in range(3)] == [("ack",), ("byte", 0xFA, True), ("byte", 0x1C, True)]
    return r.c
