# SPDX-FileCopyrightText: © 2026 NotCleo
# SPDX-License-Identifier: Apache-2.0
# UART system tests (VERIF_PLAN 7.1 U1-U8, 8 H5): uart_tx.s on E0 (uo_out[0]), uart_rx.s on E1 (ui_in[7]),
# loaded through the host link; independent UART sender / decoder on the pads; pads == ISS every clock.
# JSPE_UART_FULL=1: VERIF_PLAN sizes (full format cross, 4 KB duplex, 1000 characters). GATES=yes: the G subset.

import bisect
import os
import random
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROG = os.path.join(ROOT, "docs", "spec", "programs")
sys.path.append(os.path.join(ROOT, "sw"))             # appended: sw/tests has its own test_*.py
sys.path.append(os.path.join(ROOT, "sw", "tests"))
from jspe_asm import assemble                        # noqa: E402
from jspe_iss import Chip, HostLink, play_bin        # noqa: E402
from models import UartSender, uart_decode, ws2812_decode   # noqa: E402

ESC = 0x1B
ID = 0x4A10
CLK = 50e6
MEM_WORDS = int(os.environ.get("JSPE_MEM_WORDS", "128"))
GL = os.environ.get("GATES") == "yes"
FULL = os.environ.get("JSPE_UART_FULL") == "1"
IDLE_UI = 0x88                                       # H_RX and RXD idle high


class AliasMem(list):
    # ISS memory with the build's aliasing (ARCH 2.3)
    def __init__(self, n):
        super().__init__([None] * n)
        self.n = n

    def __getitem__(self, a):
        return list.__getitem__(self, a % self.n)

    def __setitem__(self, a, v):
        list.__setitem__(self, a % self.n, v)


class Line:
    # RXD as driven by the partner: idle high, sender segments in time order, extra low pulses
    def __init__(self):
        self.ed = []
        self.end = 0.0

    def add(self, s):
        assert s.edges[0][0] >= self.end
        self.ed += s.edges + [(s.end, 1)]
        self.end = s.end
        return s

    def pulse(self, t0, t1):
        assert t0 >= self.end
        self.ed += [(t0, 0), (t1, 1)]
        self.end = t1

    def level(self, t):
        if not hasattr(self, "_t") or len(self._t) != len(self.ed):
            self._t = [e[0] for e in self.ed]
        k = bisect.bisect_right(self._t, t) - 1
        return 1 if k < 0 else self.ed[k][1]


def pin_name(k):
    return ("uo_out", "uio_out", "uio_oe")[k // 8] + "[%d]" % (k % 8)


def divergence(cyc, dut, exp):
    vals = (dut.uo_out.value, dut.uio_out.value, dut.uio_oe.value)
    for k in range(24):
        s = str(vals[k // 8])
        g = s[7 - k % 8]
        e = str(exp[k // 8] >> (k % 8) & 1)
        if g != e:
            return "lockstep divergence at cycle %d: %s expected %s got %s" % (cyc, pin_name(k), e, g)
    return "lockstep divergence at cycle %d" % cyc


class Bench:
    def __init__(self, dut, seed=1):
        self.dut = dut
        self.r = random.Random(seed)
        self.line = Line()
        self.chip = None
        cocotb.log.info("seed %d", seed)

    async def reset(self, n=10):
        d = self.dut
        d.ena.value = 1
        d.ui_in.value = IDLE_UI
        d.uio_in.value = 0xFF
        d.rst_n.value = 0
        await ClockCycles(d.clk, n)
        d.rst_n.value = 1
        await ClockCycles(d.clk, 2)   # rst_n 2-FF sync: ISS cycle 0 is the clock after this edge
        c = self.chip = Chip(direct=False)
        c.mem = AliasMem(MEM_WORDS)
        sync = IDLE_UI | 0xFF << 8    # DUT pin-sync flops are not reset: they hold the idle pads
        c.ff1 = c.ff2 = sync
        c.fhist = [sync, sync]
        self.hl = HostLink(16)
        self.pi, self.cur, self.rd_len = 0, None, 0
        self.items, self.replies = [], []
        self.tx_ch, self.tx_lv = [], 0
        self.ws_ch, self.ws_lv = [], 0               # uo_out[2] (WS2812 DOUT in C2)

    # ---------------- clock loop
    async def run(self, n, until=None, what="condition"):
        d, c, hl = self.dut, self.chip, self.hl
        for _ in range(n):
            await FallingEdge(d.clk)
            exp = (c.uo, c.uio_out, c.uio_oe)
            try:
                got = (int(d.uo_out.value), int(d.uio_out.value), int(d.uio_oe.value))
            except ValueError:
                got = None
            if got != exp:
                raise AssertionError(divergence(c.cycle, d, exp))
            t = c.cycle
            if (got[0] & 1) != self.tx_lv:
                self.tx_lv = got[0] & 1
                self.tx_ch.append((t, self.tx_lv))
            if (got[0] >> 2 & 1) != self.ws_lv:
                self.ws_lv = got[0] >> 2 & 1
                self.ws_ch.append((t, self.ws_lv))
            ui, uio = self.pads(t)
            d.ui_in.value = ui
            d.uio_in.value = uio
            c.step(ui, uio)
            hl.sample(c)
            if until is not None and until():
                return
        if until is not None:
            raise AssertionError("timeout at cycle %d waiting for %s" % (c.cycle, what))

    def pads(self, t):
        # partner levels for the sampling edge at t + 1: RXD, H_RX; released uio pads read high
        c = self.chip
        return self.line.level(t + 1 - 1e-6) << 7 | self.hl.level(c) << 3, (~c.uio_oe | c.uio_out) & 0xFF

    @property
    def now(self):
        return self.chip.cycle

    # ---------------- chip -> host decoder (HOSTIF 4)
    def parse(self):
        g = self.hl.got
        while self.pi < len(g):
            b = g[self.pi]
            if b != ESC:
                self.items.append(("d", self.cur, b))
                self.pi += 1
                continue
            if self.pi + 1 >= len(g):
                return
            x = g[self.pi + 1]
            n = {0xC1: self.rd_len, 0xC3: 2, 0xC4: 2}.get(x, 0)
            if self.pi + 2 + n > len(g):
                return
            p = g[self.pi + 2:self.pi + 2 + n]
            self.pi += 2 + n
            if x == ESC:
                self.items.append(("d", self.cur, ESC))
            elif x <= 3:
                self.cur = x
            elif 0x40 <= x < 0x80:
                self.items.append(("ev", self.cur, x & 63))
            elif x in (0xC1, 0xC3, 0xC4):
                self.replies.append((x, p))
            else:
                self.items.append(("note", None, x))

    def stream(self, e):
        self.parse()
        return [b if k == "d" else ("ev", b) for k, s, b in self.items if s == e and k in ("d", "ev")]

    def notes(self):
        self.parse()
        return [b for k, s, b in self.items if k == "note"]

    def rx_data(self, e=1):
        return [x for x in self.stream(e) if not isinstance(x, tuple)]

    # ---------------- host operations
    async def op(self, bs):
        self.hl.send(bytes(bs))
        await self.run(10 ** 6, lambda: self.hl.idle() and self.chip.link.rx_state == "idle", "host op sent")

    async def reply(self, code):
        def have():
            self.parse()
            return bool(self.replies)
        await self.run(10 ** 6, have, "reply %02x" % code)
        x, p = self.replies.pop(0)
        assert x == code, (hex(x), hex(code))
        return p

    async def sync(self):
        await self.op([ESC, 0x74])
        assert await self.reply(0xC4) == [ID & 0xFF, ID >> 8]

    async def set_link(self, rate):
        await self.op([ESC, 0x72, 0x01, rate, 0])
        await self.run(10 ** 6, lambda: self.chip.link.tx_bits is None and not self.chip.link.outq, "H_TX idle")
        await self.run(40)
        self.hl.B = {0: 16, 1: 8, 2: 4}[rate]
        await self.sync()

    async def rdmem(self, a, n):
        self.rd_len = 2 * n
        await self.op([ESC, 0x71, a & 0xFF, a >> 8, n - 1])
        p = await self.reply(0xC1)
        return [p[2 * k] | p[2 * k + 1] << 8 for k in range(n)]

    async def wrmem(self, a, words):
        await self.op([ESC, 0x70, a & 0xFF, a >> 8, len(words) - 1] + [x for w in words for x in (w & 0xFF, w >> 8)])

    async def rdreg(self, r):
        await self.op([ESC, 0x73, r])
        p = await self.reply(0xC3)
        return p[0] | p[1] << 8

    async def load(self, img, between=None):
        # HOSTIF 6-8, as host/jspe.py: CLK record checked; every WRMEM read back, in RDMEMs of
        # (200 / B - 3) / 2 words while any engine runs (D088); between() runs after each record
        assert img.clk in (None, CLK), "image assembled for clk %s" % img.clk
        running = False
        for e in range(4):
            running |= bool(await self.rdreg(0x08 + e) >> 9 & 1)
        chunk = (200 // self.hl.B - 3) // 2 if running else 256
        for o in play_bin(img.bin_bytes()):
            await self.op(o)
            if o[1] == 0x70:
                a, n = o[2] | o[3] << 8, o[4] + 1
                ws = [o[5 + 2 * k] | o[6 + 2 * k] << 8 for k in range(n)]
                for k in range(0, n, chunk):
                    assert await self.rdmem(a + k, len(ws[k:k + chunk])) == ws[k:k + chunk], "load read-back"
            if between is not None:
                await between()

    def send(self, e, data):
        # SEL twice so H_RDY shows stream e before the first data byte (HOSTIF 1)
        self.hl.send(bytes([ESC, e, ESC, e]))
        for b in data:
            self.hl.send(bytes([ESC, ESC]) if b == ESC else bytes([b]), is_data=True)

    def tx_done(self, n, nom, dbits=8, parity=0):
        # n frames decoded and the last one complete on the line
        dec = uart_decode(self.tx_ch, nom, dbits, parity)
        return len(dec) >= n and self.now > dec[n - 1][0] + (dbits + 4) * nom

    def clear(self):
        assert self.notes() == [], "unexpected notices %s" % self.notes()
        self.items = []

    def mark_tx(self):
        assert self.tx_lv == 1, "TXD not idle high after load"
        self.tx_ch = []


async def bench(dut, seed=1, link=2):
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    b = Bench(dut, seed)
    await b.reset()
    await b.sync()
    if link:
        await b.set_link(link)
    return b


def img_of(names, **defs):
    return assemble([os.path.join(PROG, n + ".s") for n in names], {k: str(v) for k, v in defs.items()})


# ---------------- checks
def check_tx(ch, nom, vals, dbits=8, parity=0, stop=1.0, b2b=True):
    # decoded values; back to back: start-to-start = frame length, mean within 1 %; bit: every edge inside a frame
    # within 1 clk of the frame's grid; drift: nominal receiver's stop sample vs the stop bit as transmitted
    dec = uart_decode(ch, nom, dbits, parity)
    assert [d[1] for d in dec] == vals, "TX decoded %s, sent %s" % ([hex(d[1]) for d in dec], [hex(v) for v in vals])
    assert all(d[2] and d[3] for d in dec), "parity or stop error in %s" % (dec,)
    st = [d[0] for d in dec]
    ks = 1 + dbits + (1 if parity else 0)              # index of the stop bit
    frame = (ks + stop) * nom
    m = dict(gap=0.0, mean=0.0, bit=0.0, drift=0.0)
    if b2b:
        for a, b in zip(st, st[1:]):
            g = b - a
            if dbits == 9:
                assert frame - 1 < g <= frame + 15, (g, frame)
            else:
                assert abs(g - frame) <= 1, (g, frame)
                m["mean"] = max(m["mean"], abs(g / (ks + stop) - nom) / nom)
            m["gap"] = max(m["gap"], abs(g - frame))
        assert m["mean"] <= 0.01, m
    times = [t for t, v in ch]
    nd = 0
    for s, d in zip(st, dec):
        lo, hi = bisect.bisect_left(times, s), bisect.bisect_right(times, s + (ks + 0.5) * nom)
        for j in range(lo, hi):
            t, v = ch[j]
            k = round((t - s) / nom)
            m["bit"] = max(m["bit"], abs(t - s - k * nom))
            if k == ks and v == 1:
                # centre of the first stop bit as sent: up to the next start edge (1 stop bit), else one BIT
                e = ch[j + 1][0] if j + 1 < len(ch) and stop == 1.0 and ch[j + 1][0] - t < 1.5 * nom else t + nom
                m["drift"] = max(m["drift"], abs(s + (ks + 0.5) * nom - (t + e) / 2))
                nd += 1
    assert nd or not dec, "no frame with a 0 bit before its stop bit: drift not measured"
    assert m["bit"] < 1.0, m
    assert m["drift"] <= nom / 2 - 2, m
    return m


def ends_low(v, dbits=8, parity=0):
    # the bit before the stop bit is 0 (needed for a narrowed stop window)
    if parity:
        p = bin(v & ((1 << dbits) - 1)).count("1") & 1
        return (p if parity == 1 else 1 - p) == 0
    return not (v >> (dbits - 1)) & 1


def end_low(v, dbits=8, parity=0):
    # flipping the top data bit toggles both the last data bit and the parity bit
    return v if ends_low(v, dbits, parity) else v ^ 1 << (dbits - 1)


def burst(b, n, nom, pct, t0, dbits=8, parity=0, stop=1.0, esc=True):
    # n random characters back to back from a sender pct off the nominal rate (+0.02 = fast, bit time BIT/1.02);
    # characters ending in a 0 bit (not the last) get a stop bit narrowed to +-(BIT/2 - 2) of its centre, so a stop
    # sample outside it gives event 1
    vals = [b.r.getrandbits(dbits) for _ in range(n)]
    if esc and dbits == 8 and n > 2:
        vals[b.r.randrange(1, n)] = ESC
    vals[0] = end_low(vals[0], dbits, parity)
    nar = [k for k, v in enumerate(vals[:-1]) if ends_low(v, dbits, parity)]
    assert nar or n < 2
    s = UartSender(nom / (1 + pct), vals, dbits=dbits, parity=parity, stop=stop, t0=t0, narrow=nar, win=nom / 2 - 2)
    b.line.add(s)
    out = []
    for v in vals:
        out += [v & 0xFF, v >> 8] if dbits == 9 else [v]
    return out


def host_bytes(vals, dbits):
    out = []
    for v in vals:
        out += [v & 0xFF, v >> 8] if dbits == 9 else [v]
    return out


async def rst_midrun(b, n=8):
    # H5: rst_n at a random clock of UART traffic both ways; pads at reset values within 3 clk; chip ready again
    await b.load(img_of(["uart_tx", "uart_rx"]))
    await b.run(50)
    b.mark_tx()
    b.send(0, [b.r.getrandbits(8) for _ in range(n)])
    burst(b, n, 50.0, 0, b.now + 20)
    at = b.r.randrange(1, 500 * n)
    cocotb.log.info("H5: rst_n at cycle %d", b.now + at)
    await b.run(at)
    d = b.dut
    d.rst_n.value = 0
    seen = []
    for _ in range(3):
        await FallingEdge(d.clk)
        seen.append((int(d.uo_out.value), int(d.uio_out.value), int(d.uio_oe.value)))
    assert seen[-1] == (0x10, 0, 0), seen
    b.line = Line()
    await b.reset(4)
    await b.sync()
    await b.set_link(2)


async def est_after_pulse(b, dt=12):
    # RDREG EST1 timed so the register is read about dt clk after a 1-clk low pulse on RXD
    b.hl.send(bytes([ESC, 0x73, 0x09]))
    await b.run(10 ** 5, lambda: not b.hl.q and b.hl.bits is not None, "last RDREG byte")
    t = b.now + int(9.5 * b.hl.B) + 3 - dt
    b.line.pulse(t, t + 1)
    p = await b.reply(0xC3)
    return p[0] | p[1] << 8


# ---------------- U1 + U4: rates
@cocotb.test()
async def test_u1_u4_rates(dut):
    # U1 TX 8N1 mean / bit / drift; U4 RX from senders at -2 %, 0, +2 % with narrowed stop bits (RX drift)
    b = await bench(dut, 11)
    await rst_midrun(b)
    rates = [1_000_000] if GL else [1_000_000, 921_600, 115_200]
    for baud in rates:
        nom = CLK / baud
        await b.load(img_of(["uart_tx", "uart_rx"], BAUD=baud))
        await b.run(50)
        b.mark_tx()
        tx = [0x55] * 3 + ([0xA3] if GL else [b.r.getrandbits(8) for _ in range(4)] + [ESC])
        b.send(0, tx)
        t, rx = b.now + 20, []
        for f in (-0.02, 0, 0.02):
            rx += burst(b, 3 if GL else 6, nom, f, t)
            t = b.line.end + 3 * nom
        b.clear()
        await b.run(10 ** 6, lambda: len(b.rx_data()) >= len(rx) and b.now > b.line.end + 2 * nom, "RX characters")
        await b.run(int(nom * 10 * (len(tx) + 1)), lambda: b.tx_done(len(tx), nom), "TX characters")
        await b.run(int(2 * nom))
        m = check_tx(b.tx_ch, nom, tx)
        assert b.stream(1) == rx, (b.stream(1), rx)
        assert b.notes() == [], b.notes()
        cocotb.log.info("U1 %d baud: %s", baud, {k: round(v, 4) for k, v in m.items()})


# ---------------- U2 + U5: formats
def format_set():
    # full cross, or pairwise: each (data bits, parity) pair once, stop = (i + p) % 3 covers the other two pairs;
    # five of them at 115 200 covering every value of each dimension
    full = [(d, p, s, baud) for baud in (1_000_000, 115_200) for d in range(5, 10) for p in range(3) for s in range(3)]
    if FULL:
        return full
    out = []
    for i, d in enumerate(range(5, 10)):
        for p in range(3):
            slow = (i, p) in ((0, 0), (1, 1), (2, 2), (3, 0), (4, 1))
            out.append((d, p, (i + p) % 3, 115_200 if slow else 1_000_000))
    return out


@cocotb.test()
async def test_u2_u5_formats(dut):
    b = await bench(dut, 12)
    for dbits, parity, si, baud in format_set():
        stop = (1.0, 1.5, 2.0)[si]
        defs = dict(BAUD=baud, DBITS=dbits, PARITY=parity)
        if si:
            defs["STOP15" if si == 1 else "STOP2"] = 1
        nom = CLK / baud
        await b.load(img_of(["uart_tx", "uart_rx"], **defs))
        await b.run(50)
        b.mark_tx()
        tx = [b.r.getrandbits(dbits) for _ in range(4)]
        tx[0] = end_low(tx[0], dbits, parity)            # a frame for the TX drift measurement
        b.send(0, host_bytes(tx, dbits))
        t, rx = b.now + 20, []
        slow = baud < 1_000_000
        rates = (-0.02, 0.02) if dbits == 9 and parity else ((0,) if slow else (-0.02, 0.02))
        for f in rates:
            rx += burst(b, 4 if slow else 6, nom, f, t, dbits, parity, stop, esc=False)
            t = b.line.end + 3 * nom
        b.clear()
        await b.run(10 ** 7, lambda: len(b.rx_data()) >= len(rx) and b.now > b.line.end + 2 * nom, "RX characters")
        await b.run(int(nom * 14 * 5), lambda: b.tx_done(len(tx), nom, dbits, parity), "TX")
        await b.run(int(3 * nom))
        m = check_tx(b.tx_ch, nom, tx, dbits, parity, stop)
        assert b.stream(1) == rx, (defs, b.stream(1), rx)
        assert b.notes() == [], b.notes()
        cocotb.log.info("U2 %s: %s", defs, {k: round(v, 4) for k, v in m.items()})


# ---------------- U3 + U7: back to back, full duplex
@cocotb.test()
async def test_u3_u7_duplex(dut):
    # link at 16 clk per bit with H_RDY; TX and RX at 1 Mbaud both ways; then TX reloaded at 115 200, RX keeps going
    b = await bench(dut, 13, link=0)
    n = 64 if GL else (4096 if FULL else 256)
    nom = 50.0
    await b.load(img_of(["uart_tx", "uart_rx"]))
    await b.run(200)
    b.mark_tx()
    tx = [0x55] + [b.r.choice((ESC, b.r.getrandbits(8))) for _ in range(n - 1)]
    b.send(0, tx)
    rx = [b.r.choice((ESC, b.r.getrandbits(8))) for _ in range(n)]
    b.line.add(UartSender(nom, rx, t0=b.now + 300))
    b.clear()
    await b.run(10 ** 7, lambda: len(b.rx_data()) >= n and len(b.tx_ch) and b.now > b.tx_ch[0][0] + nom * 10 * n + 100,
                "duplex traffic")
    m = check_tx(b.tx_ch, nom, tx)
    assert m["gap"] == 0 and m["bit"] == 0, m                  # integer BIT: exactly 10 BIT start to start
    assert b.tx_ch[1][0] - b.tx_ch[0][0] == nom, b.tx_ch[:2]    # first start bit after idle exact
    assert b.stream(1) == rx
    assert b.notes() == [], b.notes()
    assert b.chip.eng[0].ovr == 0 and b.chip.eng[1].ovr == 0
    cocotb.log.info("U3/U7 %d bytes each way: %s", n, m)
    if GL:
        return
    # TX at 115 200 (E0 reloaded while E1 streams), RX at 1 Mbaud
    ntx, nom2 = (64 if FULL else 16), CLK / 115_200
    k = int((ntx * 10 * nom2 + 30000) / (10 * nom))
    rx2 = [b.r.getrandbits(8) for _ in range(k)]
    b.line.add(UartSender(nom, rx2, t0=b.now + 100))      # E1 receives during the reload (short read-backs, D088)
    b.clear()
    await b.load(img_of(["uart_tx"], BAUD=115_200))
    await b.run(50)
    b.mark_tx()
    tx2 = [b.r.getrandbits(8) for _ in range(ntx)]
    b.send(0, tx2)
    await b.run(10 ** 7, lambda: b.notes() or len(b.rx_data()) >= k and b.tx_done(ntx, nom2), "TX 115200 + RX")
    await b.run(int(2 * nom2))
    m = check_tx(b.tx_ch, nom2, tx2)
    assert b.stream(1) == rx2
    assert b.notes() == [], b.notes()
    cocotb.log.info("U7 TX 115200 with RX 1M: %s", m)


# ---------------- U4 long run
@cocotb.test()
async def test_u4_long(dut):
    b = await bench(dut, 14)
    await rst_midrun(b)
    n = 1000 if FULL else 100
    await b.load(img_of(["uart_rx"]))
    rx = burst(b, n, 50.0, 0.02, b.now + 50)
    b.clear()
    await b.run(10 ** 7, lambda: len(b.rx_data()) >= n and b.now > b.line.end + 100, "RX characters")
    assert b.stream(1) == rx
    assert b.notes() == [] and b.chip.eng[1].ovr == 0


# ---------------- U6: receive errors
@cocotb.test()
async def test_u6_rx_errors(dut):
    b = await bench(dut, 15)
    await rst_midrun(b)
    nom = 50.0
    await b.load(img_of(["uart_rx"]))
    b.clear()
    # stop bit low, break of 3 character times, start glitch < BIT/2
    s = b.line.add(UartSender(nom, [0x41, 0x42, 0x00, 0x43], t0=b.now + 50, gap=nom, bad_stop=(0,), breaks=(2,)))
    b.line.pulse(s.end + 2 * nom, s.end + 2 * nom + 20)
    b.line.add(UartSender(nom, [0x44], t0=b.line.end + 2 * nom))
    await b.run(10 ** 6, lambda: b.now > b.line.end + 200, "line")
    # 1-clk noise pulse, FILT off: taken as a start edge, rejected at mid-bit (engine at the @tock after rx)
    est = await est_after_pulse(b)
    assert est & 0x1FF == 0x11 and est >> 9 & 3 == 3, hex(est)
    b.line.add(UartSender(nom, [0x45], t0=b.now + 100))
    await b.run(10 ** 6, lambda: b.now > b.line.end + 200, "line")
    # FILT on for ui_in[7]: the pulse never reaches the engine (still waiting at rx)
    await b.op([ESC, 0x72, 0x04, 0x80, 0])
    est = await est_after_pulse(b)
    assert est & 0x1FF == 0x10 and est >> 9 & 3 == 3, hex(est)
    b.line.add(UartSender(nom, [0x46], t0=b.now + 100))
    await b.run(10 ** 6, lambda: b.now > b.line.end + 300, "line")
    want = [0x41, ("ev", 1), 0x42, 0x00, ("ev", 1), 0x43, 0x44, 0x45, 0x46]
    assert b.stream(1) == want, b.stream(1)
    # parity: wrong parity -> event 2; wrong parity and low stop bit -> event 2 then event 1
    await b.load(img_of(["uart_rx"], PARITY=1))
    b.clear()
    b.line.add(UartSender(nom, [0x11, 0x22, 0x33, 0x44], parity=1, t0=b.now + 50, gap=nom, bad_parity=(1, 2),
                          bad_stop=(2,)))
    await b.run(10 ** 6, lambda: b.now > b.line.end + 300, "line")
    want = [0x11, 0x22, ("ev", 2), 0x33, ("ev", 2), ("ev", 1), 0x44]
    assert b.stream(1) == want, b.stream(1)
    assert b.notes() == [] and b.chip.eng[1].ovr == 0


# ---------------- U8: overflow while a long reply holds H_TX
@cocotb.test()
async def test_u8_overflow(dut):
    b = await bench(dut, 16, link=0)
    fill = [b.r.getrandbits(16) for _ in range(MEM_WORDS)]
    for a in range(0, MEM_WORDS, 256):
        await b.wrmem(a, fill[a:a + 256])
    img = img_of(["uart_rx"])
    await b.load(img)
    for a, w in img.words.items():
        fill[a % MEM_WORDS] = w
    b.clear()
    b.rd_len = 512
    b.hl.send(bytes([ESC, 0x71, 0, 0, 255]))
    rx = [b.r.getrandbits(8) for _ in range(160)]
    b.line.add(UartSender(50.0, rx, t0=b.now + 200))

    def done():
        b.parse()
        return bool(b.replies) and b.now > b.line.end + 100
    await b.run(10 ** 6, done, "RDMEM reply and RX")
    x, p = b.replies.pop(0)
    assert [p[2 * k] | p[2 * k + 1] << 8 for k in range(256)] == [fill[k % MEM_WORDS] for k in range(256)]
    got = b.rx_data()
    it = iter(rx)
    assert all(v in it for v in got) and len(got) < len(rx), got     # in-order subset
    await b.run(4 * 10 * b.hl.B, lambda: bool(b.notes()), "OVR notice")   # notice follows the reply (HOSTIF 4)
    assert b.notes() == [0x81], b.notes()
    # reception continues
    more = [b.r.getrandbits(8) for _ in range(8)]
    b.line.add(UartSender(50.0, more, t0=b.now + 50))
    await b.run(10 ** 6, lambda: b.rx_data()[-8:] == more, "RX after overflow")
    assert b.notes() == [0x81] and b.chip.eng[1].run


# ---------------- C2: reload one engine while two others stream UART
@cocotb.test()
async def test_c2_reload_while_streaming(dut):
    # PINS 3.7 image at link B = 16; E3 reloaded (spi_tgt -> ws2812) by the default loader while E1 (TX) and
    # E2 (RX) stream UART at 1 Mbaud: no error, loss or notice on E1 / E2; then E3 drives WS2812 bits
    b = await bench(dut, 18, link=0)
    img = img_of(["i2c_tgt", "uart_tx", "uart_rx", "spi_tgt"],
                 **{"uart_tx.s:ENG": 1, "uart_rx.s:ENG": 2, "spi_tgt.s:ENG": 3, "i2c_tgt.s:ORG": 32})
    await b.load(img)
    await b.run(200)
    b.mark_tx()
    tx = [b.r.choice((ESC, b.r.getrandbits(8))) for _ in range(256 if FULL else 64)]
    left = list(tx)

    async def top_up():
        # keep E1's RX FIFO fed between load records (data bytes wait for H_RDY)
        if left:
            b.send(1, left[:3])
            del left[:3]
    rx = burst(b, 400 if FULL else 120, 50.0, 0.02, b.now + 100)
    await top_up()
    b.clear()
    await b.load(img_of(["ws2812"], ENG=3, ORG=64), between=top_up)
    b.send(1, left)
    b.ws_ch = []
    leds = [0x5A, 0xC3, 0x01]
    b.send(3, leds)
    await b.run(10 ** 7, lambda: b.notes() or len(b.rx_data(2)) >= len(rx) and b.tx_done(len(tx), 50.0)
                and b.now > b.line.end, "C2 traffic")
    await b.run(200)
    check_tx(b.tx_ch, 50.0, tx, b2b=False)
    assert b.stream(2) == rx
    assert b.notes() == [], b.notes()
    assert not any(g.ovr for g in b.chip.eng[1:3])
    assert ws2812_decode(b.ws_ch, 31)[0] == leds, ws2812_decode(b.ws_ch, 31)
    cocotb.log.info("C2: %d TX + %d RX characters across the reload", len(tx), len(rx))


# ---------------- H5: rst_n in the middle of UART traffic, reload, rerun
@cocotb.test()
async def test_h5_rst_midrun(dut):
    b = await bench(dut, 17)
    img = img_of(["uart_tx", "uart_rx"])
    await rst_midrun(b, 16)
    await b.load(img)
    await b.run(50)
    b.mark_tx()
    tx = [b.r.getrandbits(8) for _ in range(8)]
    b.send(0, tx)
    rx = burst(b, 8, 50.0, 0, b.now + 20)
    b.clear()
    await b.run(10 ** 6, lambda: len(b.rx_data()) >= 8 and b.tx_done(8, 50.0), "rerun")
    await b.run(100)
    check_tx(b.tx_ch, 50.0, tx)
    assert b.stream(1) == rx and b.notes() == []
