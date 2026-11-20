# SPDX-FileCopyrightText: © 2026 NotCleo
# SPDX-License-Identifier: Apache-2.0
# I2C system tests (VERIF_PLAN 7.4 IC1-IC6, 7.5 IT1-IT7, 8 EI1 I2C part, H5): i2c_ctrl.s / i2c_tgt.s loaded through the
# host link; independent I2C models (sw/tests/models.py) on an open-drain wired-AND bus with pull-up rise time;
# memory pre-filled with random words; pads == ISS every clock (test_uart Bench).
# JSPE_I2C_FULL=1: VERIF_PLAN sizes (16 bytes and every stretch at 100 kHz, all IT4 cases, 16 IT7 phases).
# GATES=yes: the G subset.

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from test_uart import Bench, ESC, GL, MEM_WORDS, PROG                          # noqa: E402 (sets sys.path)
from test_spi import exec_src                                                  # noqa: E402
from models import (I2cLine, I2cMonitor, I2cControllerModel, I2cTargetModel,   # noqa: E402
                    i2c_timing, i2c_violations)
from jspe_asm import Assembler                                                 # noqa: E402

FULL = os.environ.get("JSPE_I2C_FULL") == "1"
SCL, SDA = 6, 5
E1_PADS = (2, 1)                       # two-engine test: E1's SCL / SDA pads, wired to the same bus
OD = 1 << SCL | 1 << SDA | 1 << E1_PADS[0] | 1 << E1_PADS[1]
ORGS = {"i2c_ctrl": 32, "i2c_tgt": 80} if MEM_WORDS == 128 else {}
START, BYTE, REL, STOP = 1, 2, 4, 8
ADDR = 0x42                            # i2c_tgt.s default
TA = 0x50                              # target model address (controller tests)
# controller model timing (clk): bus tLOW = tlow + rise, bus tHIGH = thigh
FM400 = dict(thigh=45, tlow=65, tsu=20)   # 125 clk = 400 kHz with a 300 ns rise; data 35 clk after the fall
FMMIN = dict(thigh=30, tlow=61, tsu=20)   # IT7: tLOW (30 %) 65.5 / tHIGH 30 clk ~ the Fm minimum 1.3 / 0.6 us
SM100 = dict(thigh=230, tlow=240)         # 520 clk = 96 kHz with a 1 us rise; tLOW >= 240 clk = 4.8 us at any rise
R_FM, R_SM = 15, 50                    # pull-up rise, clk (300 ns, 1 us)
CTL_SM = dict(TLOW=242, THIGH=240)     # IC2 (D042)


# ---------------- bus, bench, images
class I2cBus:
    # SCL / SDA nets: wired-AND of the chip pads in `pairs` and the models, released lines rise after `rise` clk
    def __init__(self, rise, pairs=((SCL, SDA),)):
        self.ls, self.ld = I2cLine(rise), I2cLine(rise)
        self.pairs, self.models, self.mon = pairs, [], I2cMonitor()
        self.hist, self.drv = None, {p: None for p in pairs}

    def step(self, t, c, uio):
        def low(k):
            return c.uio_oe >> k & 1 and not c.uio_out >> k & 1
        dv = {p: (low(p[0]), low(p[1])) for p in self.pairs}
        scl = self.ls.update(t, any(v[0] for v in dv.values()) or any(not m.scl_rel for m in self.models))
        sda = self.ld.update(t, any(v[1] for v in dv.values()) or any(not m.sda_rel for m in self.models))
        for m in self.models:
            m.step(t, scl, sda)
        self.mon.step(t, scl, sda)
        if self.hist is None:
            self.hist = [(t, scl, sda)]
        elif self.hist[-1][1:] != (scl, sda):
            self.hist.append((t, scl, sda))
        for p, v in dv.items():
            if self.drv[p] is None:
                self.drv[p] = [(t,) + v]
            elif self.drv[p][-1][1:] != v:
                self.drv[p].append((t,) + v)
        for s, d in self.pairs:
            uio = uio & ~(1 << s | 1 << d) | scl << s | sda << d
        return uio

    def scl_at(self, t):
        v = 1
        for x in self.hist:
            if x[0] > t:
                break
            v = x[1]
        return v

    def falls(self):
        return [b[0] for a, b in zip(self.hist, self.hist[1:]) if a[1] and not b[1]]


def _intervals(hist, lv):
    # bus SCL intervals at level lv
    out, st, prev = [], None, None
    for t, s, d in hist:
        if s != prev:
            if s == lv:
                st = t
            elif st is not None:
                out.append((st, t))
            prev = s
    return out


class I2cBench(Bench):
    def __init__(self, dut, seed):
        super().__init__(dut, seed)
        self.bus = None

    def pads(self, t):
        # IC5 on every clock: an open-drain pad is enabled only while its value is 0
        c = self.chip
        hi = c.uio_oe & c.uio_out & OD
        if hi:
            raise AssertionError("IC5: open-drain pad driven high (uio %02x) at cycle %d" % (hi, t))
        uio = (~c.uio_oe | c.uio_out) & 0xFF
        if self.bus is not None:
            uio = self.bus.step(t, c, uio)
        return 0x80 | self.hl.level(c) << 3, uio

    def ok(self):
        assert self.notes() == [] and not any(g.ovr for g in self.chip.eng), (self.notes(), [g.ovr for g in
                                                                                             self.chip.eng])


def move_pads(txt, scl, sda):
    # the same program on other pads of the same bus (PINS.md 3.4: only the pad and bind lines differ, D094)
    for a, b in (("uio6, p0", "uio%d, p0" % scl), ("uio5, p1", "uio%d, p1" % sda),
                 ("#14 | (13 << 8)", "#%d | (%d << 8)" % (8 + scl, 8 + sda))):
        assert txt.count(a) == 1, a
        txt = txt.replace(a, b)
    return txt


def i2c_img(ctrl=None, tgt=None):
    # ctrl / tgt: .equ overrides (None = not loaded); "PADS": (scl, sda) moves the program's pads
    srcs, defs = [], {}
    for name, d in (("i2c_ctrl", ctrl), ("i2c_tgt", tgt)):
        if d is None:
            continue
        d = dict(d)
        pads = d.pop("PADS", None)
        txt = open(os.path.join(PROG, name + ".s")).read()
        srcs.append((name + ".s", move_pads(txt, *pads) if pads else txt))
        if name in ORGS:
            d.setdefault("ORG", ORGS[name])
        defs.update({"%s.s:%s" % (name, k): str(v) for k, v in d.items()})
    return Assembler(srcs, defs).run()


async def ibench(dut, seed):
    # memory filled with random words first, as on silicon (an unwritten word fetched would fail the lockstep)
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    b = I2cBench(dut, seed)
    await b.reset()
    await b.sync()
    await b.set_link(2)
    b.fill = [b.r.getrandbits(16) for _ in range(MEM_WORDS)]
    await refill(b)
    return b


async def refill(b):
    for a in range(0, MEM_WORDS, 256):
        await b.wrmem(a, b.fill[a:a + 256])


async def iload(b, img):
    b.bus = None
    await b.load(img)
    b.img = img
    await b.run(20)


# ---------------- checks
def summary_ns(q):
    return {k: (round((max(v) if k == "tVDDAT" else min(v)) * 20)) for k, v in q.items() if v and k != "same"}


def ctrl_timing(bus, mode, thd=12, pair=(SCL, SDA), tlow=72, rise=R_FM):
    # UM10204 table on the bus (tVD;DAT for unstretched low phases: TLOW + THD + 12 + rise, header of i2c_ctrl.s);
    # chip SDA changes while it holds SCL low come >= THD + 3 clk after it pulled SCL
    q = i2c_timing(bus.hist, tlow + thd + 12 + rise, rise)
    bad = i2c_violations(q, mode)
    hold, lo = [], None
    drv = bus.drv[pair]
    for (pt, pcs, pcd), (t, cs, cd) in zip(drv, drv[1:]):
        if cs and not pcs:
            lo = t
        if cd != pcd and pcs and lo is not None:
            hold.append(t - lo)
        elif cd != pcd and cs:
            hold.append(0)
    if thd and hold and min(hold) < thd + 3:
        bad.append(("SDA hold after SCL driven low, clk", min(hold), thd + 3))
    assert not bad, ("I2C timing", mode, bad, summary_ns(q))
    return q, hold


def tgt_timing(bus, pair=(SCL, SDA), thd=13, mode=None, tlow=0, rise=0, active=True):
    # IT6: chip SCL hold <= 4 clk after the bus SCL fall; every chip SDA change while SCL is low, >= THD + 3 clk
    # after the fall, the timer-paced ones THD + 3..4 (bit 7 of a read byte follows the host pull: later).
    # mode: UM10204 table on the bus too (tVD;DAT for low phases up to the model's tlow + rise)
    if mode:
        q = i2c_timing(bus.hist, tlow + rise, rise)
        bad = i2c_violations(q, mode)
        assert not bad, ("I2C timing (target side)", mode, bad, summary_ns(q))
    falls = bus.falls()
    hold, near, late = [], [], []
    drv = bus.drv[pair]
    for (pt, pcs, pcd), (t, cs, cd) in zip(drv, drv[1:]):
        f = max([x for x in falls if x <= t], default=None)
        if cs and not pcs:
            hold.append(t - f)
        if cd != pcd:
            assert bus.scl_at(t) == 0, "chip changed SDA while SCL high at %d" % t
            (near if t - f <= thd + 5 else late).append(t - f)
    if not active:
        assert not hold and not near and not late, ("chip drove a line", hold, near, late)
        return hold, near, late
    assert hold and near, ("no chip SCL hold or SDA change measured", hold, near)
    assert max(hold) <= 4, ("SCL hold clk", max(hold))
    assert min(near) >= thd + 3 and max(near) <= thd + 4, ("SDA after SCL fall", sorted(set(near)))
    assert all(x >= thd + 3 for x in late), late
    return hold, near, late


def release_setup(bus, pair=(SCL, SDA)):
    # per chip SCL hold window: clk from the chip's last SDA change in it to the release (None: no change)
    out, hs, last = [], None, None
    for t, cs, cd in bus.drv[pair]:
        if cs and hs is None:
            hs, last, prev = t, None, cd
        elif hs is not None:
            if cd != prev:
                last, prev = t, cd
            if not cs:
                out.append((hs, t, None if last is None else t - last))
                hs = None
    return out


def released(bus, pair=(SCL, SDA), after=12):
    # chip lines released within `after` clk of every START and STOP on the bus
    drv = bus.drv[pair]
    for e in bus.mon.events:
        if e[0] in "SP":
            st = [x for x in drv if x[0] <= e[1] + after][-1]
            assert st[1:] == (0, 0), ("chip holds a line %d clk after %s at %d" % (after, e[0], e[1]), st)


# ---------------- controller (i2c_ctrl.s on E0, target model on the bus)
def wr(a, data, stop=True):
    st = [(START | BYTE | REL, a << 1)] + [(BYTE | REL, x) for x in data]
    return st[:-1] + [(st[-1][0] | (STOP if stop else 0), st[-1][1])]


def rd(a, n, stop=True):
    return [(START | BYTE | REL, a << 1 | 1)] + [(BYTE, 0xFF)] * (n - 1) + [(BYTE | REL | (STOP if stop else 0), 0xFF)]


def words_of(out):
    return [(out[k] >> 1 | (out[k + 1] & 1) << 7, out[k] & 1) for k in range(0, len(out) - 1, 2)]


async def ctrl_run(b, steps, tgt, rise, bus=None, limit=10 ** 6):
    # send the steps, run until E0 waits for the next step with every returned byte out; -> (words, bus)
    if bus is None:
        bus = I2cBus(rise)
        bus.models = [tgt]
    b.bus = bus
    b.clear()
    b.send(0, [x for s in steps for x in s])
    n = 2 * sum(1 for f, d in steps if f & BYTE)
    nxt = b.img.label("next", "i2c_ctrl.s") + 1
    c = b.chip

    def done():
        e = c.eng[0]
        return b.now % 8 == 0 and len(b.rx_data(0)) >= n and b.hl.idle() and c.rx[0].empty() and e.waiting \
            and e.PC == nxt
    await b.run(limit, done, "I2C controller steps")
    await b.run(40)
    b.ok()
    return words_of(b.rx_data(0)), bus


def bus_of(steps, acks):
    # expected monitor summary: START per START flag, (byte, ack) per byte, P per STOP flag
    out, k = [], 0
    for f, d in steps:
        if f & START:
            out.append("S")
        if f & BYTE:
            out.append(acks[k])
            k += 1
        if f & STOP:
            out.append("P")
    return out


@cocotb.test()
async def test_ic1_ic3_ctrl(dut):
    # IC1 + IC2 (Fm) + IC3: writes of 1 / 2 / 16 bytes, reads of 1 / 2 / 16 with ACK and a final NACK, write + Sr +
    # read, at 400 kHz with a 300 ns rise; UM10204 Fm table on the bus; NACK on the address and on a data byte
    b = await ibench(dut, 51)
    await iload(b, i2c_img(ctrl={}))
    sizes = (1,) if GL else (1, 2, 16)
    r = b.r
    steps, want, rx, wd = [], [], [], []
    for n in sizes:
        d = [r.getrandbits(8) for _ in range(n)]
        steps += wr(TA, d)
        wd += d
        want += [(TA << 1, 0)] + [(x, 0) for x in d]
    for n in sizes:
        d = [r.getrandbits(8) for _ in range(n)]
        steps += rd(TA, n)
        rx += d
        want += [(TA << 1 | 1, 0)] + [(x, 0) for x in d[:-1]] + [(d[-1], 1)]
    if not GL:
        d, e = r.getrandbits(8), [r.getrandbits(8) for _ in range(2)]
        steps += wr(TA, [d], stop=False) + rd(TA, 2)
        rx += e
        wd.append(d)
        want += [(TA << 1, 0), (d, 0), (TA << 1 | 1, 0), (e[0], 0), (e[1], 1)]
    tgt = I2cTargetModel(TA, tx=rx)
    words, bus = await ctrl_run(b, steps, tgt, R_FM)
    assert words == want, ("returned", words[:8], want[:8])
    assert bus.mon.summary() == bus_of(steps, want), bus.mon.summary()
    assert tgt.rx == wd, (tgt.rx, wd)
    q, hold = ctrl_timing(bus, "Fm")
    cocotb.log.info("IC1/IC2 Fm (rise 300 ns): %s; SDA hold %d..%d clk", summary_ns(q), min(hold), max(hold))
    if GL:
        return
    # IC3 + EI1 (forced NACK): address NACK, then a data byte NACK; the following STOP / transfer are correct
    tgt = I2cTargetModel(TA, nack_after=2)
    a, x, y = r.getrandbits(8), r.getrandbits(8), r.getrandbits(8)
    steps = wr(TA + 1, [a], stop=False) + [(STOP, 0)] + wr(TA, [x, y], stop=False) + [(STOP, 0)] + wr(TA, [a])
    words, bus = await ctrl_run(b, steps, tgt, R_FM)
    want = [((TA + 1) << 1, 1), (a, 1), (TA << 1, 0), (x, 0), (y, 1), (TA << 1, 0), (a, 1)]   # model NACKs data from byte 2
    assert words == want, (words, want)
    assert bus.mon.summary() == bus_of(steps, want), bus.mon.summary()
    ctrl_timing(bus, "Fm")


@cocotb.test()
async def test_ic2_ctrl_100k(dut):
    # IC2 (Sm): TLOW / THIGH 242 / 240, THD 12, 1 us rise: write 2, Sr, read 2, STOP, write 1 (tBUF); FULL: IC1 sizes
    b = await ibench(dut, 52)
    await iload(b, i2c_img(ctrl=CTL_SM))
    r = b.r
    d, e, f = [r.getrandbits(8) for _ in range(2)], [r.getrandbits(8) for _ in range(2)], r.getrandbits(8)
    steps = wr(TA, d, stop=False) + rd(TA, 2) + wr(TA, [f])
    want = [(TA << 1, 0)] + [(x, 0) for x in d] + [(TA << 1 | 1, 0), (e[0], 0), (e[1], 1), (TA << 1, 0), (f, 0)]
    rx = list(e)
    if FULL:
        for n in (1, 16):
            g, h = [r.getrandbits(8) for _ in range(n)], [r.getrandbits(8) for _ in range(n)]
            steps += wr(TA, g) + rd(TA, n)
            rx += h
            want += [(TA << 1, 0)] + [(x, 0) for x in g] + [(TA << 1 | 1, 0)] + [(x, 0) for x in h[:-1]] + [(h[-1], 1)]
    tgt = I2cTargetModel(TA, tx=rx)
    words, bus = await ctrl_run(b, steps, tgt, R_SM)
    assert words == want, (words, want)
    assert bus.mon.summary() == bus_of(steps, want)
    q, hold = ctrl_timing(bus, "Sm", tlow=242, rise=R_SM)
    lo = [y - x for x, y in _intervals(bus.hist, 0)]
    cocotb.log.info("IC2 Sm (rise 1 us): %s; SDA hold %d..%d clk; SCL period in a byte %d clk", summary_ns(q),
                    min(hold), max(hold), min(lo) + min(q["tHIGH"]))


@cocotb.test()
async def test_ic4_ctrl_stretch(dut):
    # IC4 + EI1 (extra stretch): 2 us stretches in bits 1, 8, 9; 500 us in one of them (all with FULL); 1.2 ms ->
    # event 3, both lines released, the next step accepted; H_TX blocked by an RDMEM reply at a byte end
    b = await ibench(dut, 53)
    await iload(b, i2c_img(ctrl={}))
    r = b.r
    keys = [("wr", 1, "data"), ("wr", 8, "data"), ("wr", 8, "ack")]
    long = keys if FULL else [r.choice(keys)]
    own = 170                                  # i2c_ctrl's longest own low phase on the bus is 168 clk
    for n, ks in [(100, keys)] + [(25000, [k]) for k in long]:
        tgt = I2cTargetModel(TA, stretch={k: own + n for k in ks})
        d = [r.getrandbits(8) for _ in range(2)]
        words, bus = await ctrl_run(b, wr(TA, d), tgt, R_FM)
        assert words == [(TA << 1, 0), (d[0], 0), (d[1], 0)] and tgt.rx == d, (words, tgt.rx)
        assert not tgt.stretch, ("stretch keys not hit", tgt.stretch)
        q, hold = ctrl_timing(bus, "Fm")
        lo = sorted(y - x for x, y in _intervals(bus.hist, 0))
        assert sum(1 for x in lo if x >= own + n) == len(ks) and min(q["tHIGH"]) >= 34, (lo[-4:], q["tHIGH"])
        cocotb.log.info("IC4 stretch %d clk at %s: longest lows %s, tHIGH min %d clk", n, ks, lo[-3:],
                        min(q["tHIGH"]))
    # 1.2 ms (60 000 clk) in bit 2 of the data byte: deadline 50 000 clk -> event 3
    tgt = I2cTargetModel(TA, stretch={("wr", 2, "data"): 60000})
    bus = I2cBus(R_FM)
    bus.models = [tgt]
    b.bus = bus
    b.clear()
    d = r.getrandbits(8)
    b.send(0, [x for s in wr(TA, [d], stop=False) for x in s])
    await b.run(200000, lambda: b.now % 8 == 0 and ("ev", 3) in b.stream(0), "event 3")
    t_ev = b.now
    await b.run(16)
    assert b.chip.uio_oe >> SCL & 1 == 0 and b.chip.uio_oe >> SDA & 1 == 0, "bus not released after event 3"
    assert words_of(b.rx_data(0)) == [(TA << 1, 0)] and len(b.rx_data(0)) == 2, b.stream(0)
    await b.run(200000, lambda: tgt.hold_until is None, "target model releases SCL")
    held = [y - x for x, y in _intervals(bus.hist, 0)]
    cocotb.log.info("IC4 1.2 ms: event 3 at cycle %d, SCL low %d clk (target model)", t_ev, max(held))
    e = r.getrandbits(8)
    words, bus = await ctrl_run(b, wr(TA, [e]), tgt, R_FM, bus=bus)
    assert words == [(TA << 1, 0), (e, 0)] and tgt.rx[-1] == e, (words, tgt.rx)
    assert bus.mon.summary()[-4:] == ["S", (TA << 1, 0), (e, 0), "P"], bus.mon.summary()
    ctrl_timing(bus, "Fm")
    # H_TX held by a 256-word RDMEM reply at B = 16 (82 000 clk > DEADLINE): SCL stays low, no event, no byte lost
    await b.set_link(0)
    b.rd_len = 512
    b.hl.send(bytes([ESC, 0x71, 0, 0, 255]))
    await b.run(400)
    tgt = I2cTargetModel(TA)
    d = [r.getrandbits(8) for _ in range(4)]
    words, bus = await ctrl_run(b, wr(TA, d), tgt, R_FM, limit=4 * 10 ** 5)
    assert words == [(TA << 1, 0)] + [(x, 0) for x in d] and tgt.rx == d, (words, tgt.rx)
    assert not any(isinstance(x, tuple) for x in b.stream(0)), b.stream(0)
    held = max(y - x for x, y in _intervals(bus.hist, 0))
    assert held > 50000, held
    x, p = b.replies.pop(0)
    mem = [b.fill[k] for k in range(MEM_WORDS)]
    for a, w in b.img.words.items():
        mem[a % MEM_WORDS] = w
    assert x == 0xC1 and [p[2 * k] | p[2 * k + 1] << 8 for k in range(256)] == [mem[k % MEM_WORDS] for k in range(256)]
    cocotb.log.info("IC4 H_TX blocked: SCL held %d clk, no event", held)
    await b.set_link(2)


@cocotb.test()
async def test_ic6_ctrl_fmplus(dut):
    # IC6 (SHOULD): TLOW / THIGH 20 / 16, THD 0, 120 ns rise: Fm+ table; write 2, Sr, read 2
    b = await ibench(dut, 54)
    await iload(b, i2c_img(ctrl=dict(TLOW=20, THIGH=16, THD=0)))
    r = b.r
    d, e = [r.getrandbits(8) for _ in range(2)], [r.getrandbits(8) for _ in range(2)]
    tgt = I2cTargetModel(TA, tx=e)
    words, bus = await ctrl_run(b, wr(TA, d, stop=False) + rd(TA, 2), tgt, 6)
    assert words == [(TA << 1, 0)] + [(x, 0) for x in d] + [(TA << 1 | 1, 0), (e[0], 0), (e[1], 1)], words
    q, hold = ctrl_timing(bus, "Fm+", thd=0, tlow=20, rise=6)
    rises = [x for x, y in _intervals(bus.hist, 1)]
    per = min(y - x for x, y in zip(rises, rises[1:]))
    cocotb.log.info("IC6 Fm+: %s; SCL period min %d clk (%.2f MHz)", summary_ns(q), per, 50.0 / per)


# ---------------- target (i2c_tgt.s on E0, controller model on the bus)
class Ctl(I2cControllerModel):
    # adds ("C", levels, pos, cond): clock levels[:pos - 1] as bits, then START ("S") or STOP ("P") in the high
    # phase of clock pos (pos 10 = in the clock after the byte)
    def bit(self, v):
        return [("sda", v), ("wait", self.tlow - self.tsu), ("scl", 1), ("waithigh",), ("wait", self.thigh),
                ("scl", 0), ("wait", self.tsu)]

    def expand(self):
        it = self.script[0]
        if it[0] != "C":
            return super().expand()
        self.script.pop(0)
        _, lv, pos, cond = it
        ops = [x for v in lv[:pos - 1] for x in self.bit(v)]
        pre = 1 if cond == "S" else 0
        ops += [("sda", pre), ("wait", self.tlow - self.tsu), ("scl", 1), ("waithigh",), ("wait", self.thigh // 2),
                ("sda", 1 - pre), ("wait", self.thigh)]
        return ops + ([("scl", 0), ("wait", self.tsu)] if cond == "S" else [("wait", self.tlow)])


class Lag:
    # model outputs one clock late: a partner edge at t + 1/4..3/4 clk is first sampled at the next clock
    def __init__(self, m):
        self.m, self.scl_rel, self.sda_rel, self.q = m, 1, 1, (1, 1)

    def step(self, t, scl, sda):
        self.m.step(t, scl, sda)
        (self.scl_rel, self.sda_rel), self.q = self.q, (self.m.scl_rel, self.m.sda_rel)

    def done(self):
        return self.m.done()


async def tgt_run(b, script, host=(), timing=FM400, rise=R_FM, off=0, limit=10 ** 6, pairs=((SCL, SDA),), lag=False,
                  check=True, active=True):
    # controller model starts `off` clk after the host bytes are queued; runs until it is done; target-side checks
    m = Ctl(script, **timing)
    m = Lag(m) if lag else m
    bus = b.bus = I2cBus(rise, pairs)
    b.clear()
    if host:
        b.send(0, list(host))
    await b.run(1 + off)
    bus.models = [m]
    await b.run(limit, lambda: b.now % 8 == 0 and m.done(), "I2C controller model")
    await b.run(40)
    b.ok()
    assert b.chip.uio_oe & (1 << SCL | 1 << SDA) == 0, "lines not released at the end"
    if check:
        tgt_timing(bus, mode="Sm" if timing["thigh"] >= 200 else "Fm", tlow=timing["tlow"], rise=rise, active=active)
    return (m.m if lag else m), bus


def W(x):
    return ("W", x)


def R(ack):
    return ("R", ack)


S, P = ("S",), ("P",)


async def tgt_load(b, **defs):
    await iload(b, i2c_img(tgt=defs))


async def rst_tgt(b):
    # H5: rst_n in the middle of a target write; pads at reset values within 3 clk; memory refilled
    await tgt_load(b)
    m = Ctl([S, W(ADDR << 1)] + [W(b.r.getrandbits(8)) for _ in range(6)] + [P], **FM400)
    b.bus = I2cBus(R_FM)
    b.bus.models = [m]
    at = b.r.randrange(1500, 6000)
    cocotb.log.info("H5: rst_n at cycle %d", b.now + at)
    await b.run(at)
    d = b.dut
    d.rst_n.value = 0
    seen = []
    for _ in range(3):
        await FallingEdge(d.clk)
        seen.append((int(d.uo_out.value), int(d.uio_out.value), int(d.uio_oe.value)))
    assert seen[-1] == (0x10, 0, 0), seen
    b.bus = None
    await b.reset(4)
    await b.sync()
    await b.set_link(2)
    await refill(b)


def it_sizes():
    if GL:
        return {400: (2,)}
    return {400: (1, 2, 16), 100: (1, 2, 16) if FULL else (1, 2)}


@cocotb.test()
async def test_it2_it3_tgt_rw(dut):
    # IT2 + IT3 + IT6: writes and reads of 1 / 2 / 16 bytes at 400 kHz (300 ns rise) and 100 kHz (1 us rise,
    # TSU 60 as documented, D043); host gets the address byte then the data; read bytes come from the host. H5 first
    b = await ibench(dut, 61)
    if not GL:
        await rst_tgt(b)
    for khz, ns in it_sizes().items():
        sm = khz == 100
        await tgt_load(b, **(dict(TSU=60) if sm else {}))
        script, host, want, rx = [], [], [], []
        for n in ns:
            d = [b.r.getrandbits(8) for _ in range(n)]
            script += [S, W(ADDR << 1)] + [W(x) for x in d] + [P]
            want += [ADDR << 1] + d
        for n in ns:
            d = [b.r.getrandbits(8) for _ in range(n)]
            script += [S, W(ADDR << 1 | 1)] + [R(0)] * (n - 1) + [R(1), P]
            want += [ADDR << 1 | 1]
            host += d
            rx += d
        m, bus = await tgt_run(b, script, host, SM100 if sm else FM400, R_SM if sm else R_FM,
                               off=b.r.randrange(4))
        assert b.rx_data(0) == want, (b.rx_data(0), want)
        assert m.read == rx and m.acks == [0] * len(m.acks), (m.read, rx, m.acks)
        released(bus)
        hold, near, late = tgt_timing(bus)
        su = [s for hs, t, s in release_setup(bus) if s is not None]
        cocotb.log.info("IT2/IT3 %d kHz: SCL hold %d..%d clk, SDA after fall %s (late %s), release setups %s",
                        khz, min(hold), max(hold), sorted(set(near)), sorted(set(late))[:4], sorted(set(su))[:6])


@cocotb.test()
async def test_it1_tgt_address(dut):
    # IT1: ADDR, ADDR +- 1, 0x00, 0x7F: ACK only for the own address, nothing to the host otherwise; then the own
    # address changed by EXEC (li R0) to 0x20
    b = await ibench(dut, 62)
    await tgt_load(b)
    for own, addrs in ((ADDR, (ADDR, ADDR - 1, ADDR + 1, 0x00, 0x7F)), (0x20, (0x20, ADDR))):
        if own != ADDR:
            await exec_src(b, 0, "li R0, #%d" % own)
        for a in addrs:
            x = b.r.getrandbits(8)
            m, bus = await tgt_run(b, [S, W(a << 1), W(x), P], off=b.r.randrange(4), active=a == own)
            ack = 0 if a == own else 1
            assert m.acks == [ack, ack] and b.rx_data(0) == ([a << 1, x] if a == own else []), (a, m.acks,
                                                                                                b.rx_data(0))
    cocotb.log.info("IT1: own address 0x%02x then 0x20 via EXEC ok", ADDR)


def it4_cases(r):
    # (context, position 1..9, condition): every position in each context; one condition each (both with FULL)
    out = []
    for ctx in ("addr", "wr", "rd"):
        for pos in range(1, 10):
            for cond in (("S", "P") if FULL else (r.choice("SP"),)):
                out.append((ctx, pos, cond))
    return out


@cocotb.test()
async def test_it4_tgt_start_stop_anywhere(dut):
    # IT4: repeated START or STOP in the high phase of clock 1..9 of the address byte, a written byte and a read byte
    # (read bytes 0xFF so the controller controls SDA). Clock 9 of a written byte carries the target's ACK (SDA held
    # low): there the condition comes in the clock after the ACK; clock 9 of the address byte is tested on a NACKed
    # address (ADDR + 1). Partial byte dropped, lines released after every START / STOP, the next transfer correct
    b = await ibench(dut, 63)
    await tgt_load(b)
    r = b.r
    script, host, want = [], [], []
    for ctx, pos, cond in it4_cases(r):
        x = r.getrandbits(8)
        a = ADDR + 1 if pos == 9 else ADDR
        bits = [(a << 1) >> (7 - k) & 1 for k in range(8)] + [1]
        dbits = [x >> (7 - k) & 1 for k in range(8)] + [1]
        p = 10 if pos == 9 and ctx == "wr" else pos
        if ctx == "addr":
            script += [S, ("C", bits, p, cond)]
        elif ctx == "wr":
            script += [S, W(ADDR << 1), ("C", dbits, p, cond)]
            want += [ADDR << 1] + ([x] if p == 10 else [])
        else:
            script += [S, W(ADDR << 1 | 1), ("C", [1] * 9, p, cond)]     # clock 9 = controller's ACK slot
            host.append(0xFF)
            want += [ADDR << 1 | 1]
        y = r.getrandbits(8)
        script += ([] if cond == "S" else [S]) + [W(ADDR << 1), W(y), P]
        want += [ADDR << 1, y]
    m, bus = await tgt_run(b, script, host, off=r.randrange(4), check=False)
    assert b.rx_data(0) == want, (b.rx_data(0), want)
    released(bus)
    tgt_timing(bus)
    sp = [e[0] for e in bus.mon.events if e[0] in "SP"]
    cocotb.log.info("IT4: %d START / %d STOP on the bus", sp.count("S"), sp.count("P"))


@cocotb.test()
async def test_it5_tgt_stretch(dut):
    # IT5: a 16-byte write while a 256-word RDMEM reply holds H_TX (SCL stretched, no byte lost); reads with the
    # host byte 0, 10 us and 1 ms late (SCL held meanwhile); bit 7 put on SDA >= TSU + 2 clk before SCL is released
    b = await ibench(dut, 64)
    await tgt_load(b)
    r = b.r
    b.rd_len = 512
    b.hl.send(bytes([ESC, 0x71, 0, 0, 255]))
    await b.run(200)
    d = [r.getrandbits(8) for _ in range(16)]
    m, bus = await tgt_run(b, [S, W(ADDR << 1)] + [W(x) for x in d] + [P])
    assert b.rx_data(0) == [ADDR << 1] + d and m.acks == [0] * 17, (b.rx_data(0), m.acks)
    held = max(y - x for x, y in _intervals(bus.hist, 0))
    assert held > 5000, held
    x, p = b.replies.pop(0)
    assert x == 0xC1 and len(p) == 512
    tgt_timing(bus)
    cocotb.log.info("IT5 write behind RDMEM: SCL held %d clk", held)
    for late in (0, 500, 50000):
        e = [r.getrandbits(7), r.getrandbits(8)]          # bit 7 of the first byte 0: a change before the release
        m = Ctl([S, W(ADDR << 1 | 1), R(0), R(1), P], **FM400)
        bus = b.bus = I2cBus(R_FM)
        b.clear()
        if not late:
            b.send(0, e)
        await b.run(1)
        bus.models = [m]
        if late:
            await b.run(10 ** 5, lambda: len(bus.mon.events) >= 2 and b.chip.uio_oe >> SCL & 1, "SCL held")
            await b.run(late)
            b.send(0, e)
        await b.run(10 ** 6, lambda: b.now % 8 == 0 and m.done(), "read")
        await b.run(40)
        b.ok()
        assert m.read == e and b.rx_data(0) == [ADDR << 1 | 1], (m.read, e, b.rx_data(0))
        ws = release_setup(bus)
        assert ws[1][1] - ws[1][0] >= late and ws[1][2] >= 22, ws          # TSU + 2 (D095)
        assert all(s >= 22 for hs, t, s in ws if s is not None), ws
        tgt_timing(bus, mode="Fm", tlow=FM400["tlow"], rise=R_FM)
        cocotb.log.info("IT5 read, host %d clk late: SCL held %d clk, bit 7 setup %s clk", late, ws[1][1] - ws[1][0],
                        [s for hs, t, s in ws[1:]])


@cocotb.test()
async def test_it8_tgt_ack_setup(dut):
    # IT8 (D097): zero rise time; every chip hold window of a write (ACK) and the read address ACK: ACK stable on SDA
    # >= 300 ns (100 kHz, at the .assert limit TSU 13) / 150 ns (400 kHz, TSU 20) before the chip releases SCL, and
    # TSU + 3..6 clk
    b = await ibench(dut, 68)
    for khz, timing, need, tsu in ((100, SM100, 15, 13), (400, FM400, 8, 20)):
        await tgt_load(b, TSU=tsu)
        d, e = [b.r.getrandbits(8) for _ in range(3)], b.r.getrandbits(8)
        m, bus = await tgt_run(b, [S, W(ADDR << 1)] + [W(x) for x in d] + [P], timing=timing, rise=0)
        ws = release_setup(bus)
        m2, bus2 = await tgt_run(b, [S, W(ADDR << 1 | 1), R(1), P], [e], timing=timing, rise=0)
        ws2 = release_setup(bus2)
        assert b.rx_data(0) == [ADDR << 1 | 1] and m2.read == [e] and m.acks == [0] * 4, (m.acks, m2.read)
        acks = [s for hs, t, s in ws] + [ws2[0][2]]
        assert len(ws) == 4 and all(s is not None and s >= need and tsu + 3 <= s <= tsu + 6 for s in acks), \
            (khz, ws, ws2)
        cocotb.log.info("IT8 %d kHz, rise 0: ACK -> SCL release %s clk (>= %d)", khz, acks, need)


@cocotb.test()
async def test_it7_tgt_fm_min(dut):
    # IT7: controller at the Fm minimum (tLOW 65.5 / tHIGH 30 clk, 300 ns rise), write 2 + read 2, in 4 slot phases
    # x 4 partner phases (FULL; 4 pairs by default, each slot phase once). The bus model is clocked: a partner edge at
    # t + 1/4..3/4 clk is first sampled at the next clock, so partner phases > 0 are the model's outputs 1 clk late
    b = await ibench(dut, 67)
    await tgt_load(b)
    combos = [(s, p) for s in range(4) for p in (0.0, 0.25, 0.5, 0.75)] if FULL else \
        [(0, 0.0), (1, 0.25), (2, 0.5), (3, 0.75)]
    for s, ph in combos:
        d, e = [b.r.getrandbits(8) for _ in range(2)], [b.r.getrandbits(8) for _ in range(2)]
        m, bus = await tgt_run(b, [S, W(ADDR << 1), W(d[0]), W(d[1]), P, S, W(ADDR << 1 | 1), R(0), R(1), P], e,
                               FMMIN, off=s, lag=ph > 0)
        assert b.rx_data(0) == [ADDR << 1] + d + [ADDR << 1 | 1] and m.read == e, (b.rx_data(0), m.read, d, e)
        q = i2c_timing(bus.hist)
        hold, near, late = tgt_timing(bus)
        cocotb.log.info("IT7 slot %d partner %.2f: tLOW min %d, tHIGH min %d clk, SDA after fall %s", s, ph,
                        min(q["tLOW"]), min(q["tHIGH"]), sorted(set(near)))


# ---------------- controller E0 and target E1 on one bus
@cocotb.test()
async def test_ctrl_tgt_two_engines(dut):
    # E0 i2c_ctrl on uio[6:5], E1 i2c_tgt on uio[2:1], both pairs wired to one bus: write 2, Sr, read 2, STOP at
    # 400 kHz (300 ns rise) and 100 kHz (1 us rise; TLOW / THIGH 242 / 240, TSU 60). Each side's checks as above
    b = await ibench(dut, 71)
    pairs = ((SCL, SDA), E1_PADS)
    for sm in ((False,) if GL else (False, True)):
        ctl = dict(CTL_SM) if sm else {}
        tg = dict(ENG=1, PADS=E1_PADS, **(dict(TSU=60) if sm else {}))
        await iload(b, i2c_img(ctrl=ctl, tgt=tg))
        d, e = [b.r.getrandbits(8) for _ in range(2)], [b.r.getrandbits(8) for _ in range(2)]
        b.clear()
        b.send(1, e)
        await b.run(400)
        bus = I2cBus(R_SM if sm else R_FM, pairs)
        steps = wr(ADDR, d, stop=False) + rd(ADDR, 2)
        words, bus = await ctrl_run(b, steps, None, 0, bus=bus)
        want = [(ADDR << 1, 0)] + [(x, 0) for x in d] + [(ADDR << 1 | 1, 0), (e[0], 0), (e[1], 1)]
        assert words == want, (words, want)
        assert b.rx_data(1) == [ADDR << 1] + d + [ADDR << 1 | 1], b.rx_data(1)
        assert bus.mon.summary() == bus_of(steps, want), bus.mon.summary()
        q, hold = ctrl_timing(bus, "Sm" if sm else "Fm", tlow=242 if sm else 72, rise=R_SM if sm else R_FM)
        th, near, late = tgt_timing(bus, E1_PADS)
        released(bus, E1_PADS)
        cocotb.log.info("two engines %s: %s; E1 SCL hold %d..%d, SDA after fall %s", "100 kHz" if sm else "400 kHz",
                        summary_ns(q), min(th or [0]), max(th or [0]), sorted(set(near)))
