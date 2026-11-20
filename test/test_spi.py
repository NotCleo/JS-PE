# SPDX-FileCopyrightText: © 2026 NotCleo
# SPDX-License-Identifier: Apache-2.0
# SPI system tests (VERIF_PLAN 7.2 SC1-SC4, 7.3 ST1-ST5, 8 EI1 SPI part, H5): spi_ctrl.s / spi_tgt.s loaded through
# the host link; independent SPI models (sw/tests/models.py) on uio[3:0]; pads == ISS every clock (test_uart Bench).
# JSPE_SPI_FULL=1: VERIF_PLAN sizes (mode x bit order x word length x rate cross, n = 255, all SCK rates x 4 partner
# phases). GATES=yes: the G subset.

import bisect
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from test_uart import Bench, img_of, ESC, GL, MEM_WORDS                       # noqa: E402 (sets sys.path)
from models import SpiController, SpiTargetOnline, spi_ctrl_check, pad_levels   # noqa: E402
from jspe_asm import assemble_text                                              # noqa: E402

FULL = os.environ.get("JSPE_SPI_FULL") == "1"
TAB = [0xA5, 0x5A, 1, 2, 3, 4, 5, 6]          # spi_tgt.s table
SETUP_NS = {11.25: 25, 12.5: 50, 25: 300}       # ST2 MISO setup at the controller model (D081)
PHASES = (0.0, 0.25, 0.5, 0.75)
CMDS = [(0, 7), (1, 2), (7, 1)]                 # ST1: command, response bytes


class SpiBench(Bench):
    def __init__(self, dut, seed):
        super().__init__(dut, seed)
        self.partner = None

    def pads(self, t):
        # partner drives its uio bits; a bit driven by both with different levels is contention
        c = self.chip
        uio = (~c.uio_oe | c.uio_out) & 0xFF
        if self.partner is not None:
            m, v = self.partner.drive(t + 1 - 1e-6, c)
            clash = m & c.uio_oe & (v ^ c.uio_out)
            if clash:
                raise AssertionError("uio contention %02x at cycle %d" % (clash, t))
            uio = uio & ~m & 0xFF | v & m
        return 0x80 | self.hl.level(c) << 3, uio

    def lv(self, k, t0, get=None):
        # changes of uio[k] after t0, led by its level at t0; default: pad level (released reads high)
        get = get or (lambda o, oe: pad_levels(o, oe) >> k & 1)
        v0, out = 1, []
        for t, uo, o, oe in self.chip.pad_trace:
            v = get(o, oe)
            if t <= t0:
                v0 = v
            elif v != (out[-1][1] if out else v0):
                out.append((t, v))
        return [(t0, v0)] + out


class TgtPartner:
    # SPI target model on MISO (uio[2]) answering spi_ctrl
    def __init__(self, tgt):
        self.tgt = tgt

    def drive(self, t, c):
        self.tgt.update(c.pad_trace)
        return 0x04, self.tgt.level(t) << 2


class Bus:
    # SPI controller models for spi_tgt in time order on SCK uio[3], CS uio[0], MOSI uio[1]; noise pulses
    # (SCK away from idle, MOSI high) only while CS is high
    def __init__(self, cpol):
        self.cpol, self.x, self.hi, self.noise, self.nt = cpol, [], [], [], []

    def add(self, x):
        assert not self.x or x.cs_lo > self.x[-1].cs_hi
        self.x.append(x)
        self.hi.append(x.cs_hi)
        return x

    def pulse(self, t0, t1):
        assert all(t1 <= x.cs_lo or t0 >= x.cs_hi for x in self.x), "noise while CS is low"
        assert not self.nt or t0 >= self.noise[-1][1]
        self.noise.append((t0, t1))
        self.nt.append(t0)

    @property
    def end(self):
        return self.x[-1].cs_hi

    def windows(self):
        return [(x.cs_lo, x.cs_hi) for x in self.x]

    def drive(self, t, c):
        k = bisect.bisect_right(self.nt, t) - 1
        if k >= 0 and t < self.noise[k][1]:
            return 0x0B, (1 ^ self.cpol) << 3 | 0x03
        k = bisect.bisect_right(self.hi, t)
        if k == len(self.x):
            return 0x0B, self.cpol << 3 | 1
        x = self.x[k]
        return 0x0B, x.sck(t) << 3 | x.cs(t) | x.mosi(t) << 1


async def sbench(dut, seed, link=2, fill=False):
    # fill: random words in all memory, as on silicon (spi_tgt reads table[c + n], past the table for c + n > 7)
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    b = SpiBench(dut, seed)
    await b.reset()
    await b.sync()
    if link:
        await b.set_link(link)
    b.fill = [b.r.getrandbits(16) for _ in range(MEM_WORDS)] if fill else None
    for a in range(0, MEM_WORDS if fill else 0, 256):
        await b.wrmem(a, b.fill[a:a + 256])
    return b


async def exec_src(b, e, src):
    # STOP, EXEC the assembled instructions, RUN (HOSTIF 6)
    ws = assemble_text(".engine %d\n.init\n%s\n.code\n" % (e, src)).exec_words(e)
    await b.op([ESC, 0x20 | 1 << e])
    for w in ws:
        await b.op([ESC, 0x60 | e, w & 0xFF, w >> 8])
    await b.op([ESC, 0x30 | 1 << e])


# ---------------- controller (spi_ctrl.s on E0, target model on MISO)
def hbytes(words, wl):
    return [x for w in words for x in ((w & 0xFF, w >> 8) if wl > 8 else (w,))]


def hwords(bs, wl):
    return [bs[k] | bs[k + 1] << 8 for k in range(0, len(bs) - 1, 2)] if wl > 8 else list(bs)


def ccfg(mode=0, lsb=0, wl=8, bit=8, smp=6, csdly=16):
    return dict(CPHA=mode & 1, CPOL=mode >> 1, LSB=lsb, WL=wl, BIT=bit, SMP=smp, CSDLY=csdly)


def cmode(cfg):
    return cfg["CPOL"] << 1 | cfg["CPHA"]


async def ctrl_load(b, cfg):
    b.partner = None
    await b.load(img_of(["spi_ctrl"], **cfg))
    await b.run(20)


async def ctrl_xfers(b, cfg, xfers, delay=1.0, tw=None, late=None, check=True):
    # queue the transfers back to back (late = (clk, k): words from index k of the first transfer sent clk later);
    # the target model answers tw. Returns (checker windows, host words, target words sent)
    wl, lsb = cfg["WL"], bool(cfg["LSB"])
    if tw is None:
        tw = [[b.r.getrandbits(wl) for _ in x] for x in xfers]
    flat = [w for x in tw for w in x]
    tgt = SpiTargetOnline(cmode(cfg), flat, wl, lsb, delay)
    tgt.skip(b.chip.pad_trace)
    b.partner = TgtPartner(tgt)
    t0 = b.now
    b.clear()
    hb = [[len(x)] + hbytes(x, wl) for x in xfers]
    if late:
        k = 1 + late[1] * (2 if wl > 8 else 1)
        b.send(0, hb[0][:k])
        await b.run(late[0])
        hb[0] = hb[0][k:]
    b.send(0, [x for h in hb for x in h])
    nb = len(hbytes(flat, wl))
    await b.run(10 ** 7, lambda: len(b.rx_data(0)) >= nb and b.chip.uio_out & 1, "SPI transfers")
    await b.run(5)
    tr = spi_ctrl_check(b.lv(3, t0), b.lv(0, t0), b.lv(1, t0), cmode(cfg), wl, cfg["BIT"], cfg["SMP"], t0, lsb)
    got = hwords(b.rx_data(0), wl)
    assert len(tr) == len(xfers), "%d CS windows for %d transfers" % (len(tr), len(xfers))
    assert [w["mosi"] for w in tr] == xfers, ("MOSI words differ", [w["mosi"][:6] for w in tr], [x[:6] for x in xfers])
    assert tgt.words() == [w for x in xfers for w in x], "target model received other words"
    assert b.notes() == [] and b.chip.eng[0].ovr == 0, b.notes()
    if check:
        assert got == flat, "host received %s, target sent %s" % (got[:8], flat[:8])
    return tr, got, flat


def cs_times(cfg, tr):
    # (setup, hold, inactive) minus CSDLY; setup also minus BIT + SMP (CPHA 0) or BIT (CPHA 1)
    d = cfg["CSDLY"]
    fix = cfg["BIT"] + (0 if cfg["CPHA"] else cfg["SMP"])
    out = [(w["setup"] - d - fix, w["hold"] - d) for w in tr]
    ina = [y["lo"] - x["hi"] - d for x, y in zip(tr, tr[1:])]
    return out, ina


async def rst_mid(b, cfg):
    # H5: rst_n at a random clock of a transfer; pads at reset values within 3 clk; chip ready again
    await ctrl_load(b, cfg)
    w = [b.r.getrandbits(8) for _ in range(16)]
    tgt = SpiTargetOnline(cmode(cfg), [0] * 16)
    tgt.skip(b.chip.pad_trace)
    b.partner = TgtPartner(tgt)
    b.send(0, [16] + w)
    at = b.r.randrange(200, 1800)
    cocotb.log.info("H5: rst_n at cycle %d", b.now + at)
    await b.run(at)
    d = b.dut
    d.rst_n.value = 0
    seen = []
    for _ in range(3):
        await FallingEdge(d.clk)
        seen.append((int(d.uo_out.value), int(d.uio_out.value), int(d.uio_oe.value)))
    assert seen[-1] == (0x10, 0, 0), seen
    b.partner = None
    await b.reset(4)
    await b.sync()
    await b.set_link(2)


def sc1_set():
    # (mode, lsb, word length, (BIT, SMP), words in the first transfer); a 1-word transfer follows back to back
    if GL:
        return [(0, 0, 8, (8, 6), 4), (3, 0, 8, (8, 6), 4)]
    if FULL:
        out = [(m, l, wl, bs, 4) for m in range(4) for l in (0, 1) for wl in (1, 4, 7, 8, 9, 12, 16)
               for bs in ((8, 6), (10, 5), (50, 25))]
        out += [(m, 0, 8, (8, 6), n) for m in range(4) for n in (1, 2, 16, 255)]
        return out + [(m, 1, 16, (500, 250), 2) for m in range(4)]
    # each mode with both bit orders; word lengths 1, 4, 7, 8, 9, 12, 16; every BIT/SMP of SC1; n = 1, 2, 16, 255
    return [(0, 0, 8, (8, 6), 255), (1, 1, 16, (8, 6), 16), (2, 1, 8, (10, 5), 2), (3, 0, 12, (10, 5), 4),
            (0, 1, 4, (50, 25), 2), (1, 0, 9, (8, 6), 4), (2, 0, 1, (8, 6), 16), (3, 1, 7, (500, 250), 1)]


@cocotb.test()
async def test_sc1_ctrl_formats(dut):
    # SC1: modes x bit order x word length x BIT/SMP; SCK grid, idle level, MOSI only at launch edges, CS held,
    # both directions; two transfers back to back. H5 first.
    b = await sbench(dut, 21)
    await rst_mid(b, ccfg())
    for mode, lsb, wl, (bit, smp), n in sc1_set():
        cfg = ccfg(mode, lsb, wl, bit, smp)
        await ctrl_load(b, cfg)
        xf = [[b.r.getrandbits(wl) for _ in range(n)], [b.r.getrandbits(wl)]]
        tr, got, flat = await ctrl_xfers(b, cfg, xf)
        st, ina = cs_times(cfg, tr)
        cocotb.log.info("SC1 mode %d %s WL %d BIT %d/%d n %d: CS (setup-fix, hold) %s, inactive %s", mode,
                        "LSB" if lsb else "MSB", wl, bit, smp, n, st, ina)
        check_cs(cfg, st, ina)


@cocotb.test()
async def test_sc2_cs_timing(dut):
    # SC2: CSDLY 16, then 6 and 1000 by EXEC between transfers; setup exact, hold / inactive = CSDLY + ISS overhead
    b = await sbench(dut, 22)
    for mode in (0, 3):
        cfg = ccfg(mode)
        await ctrl_load(b, cfg)
        for d in (16, 6, 1000):
            if d != cfg["CSDLY"]:
                await exec_src(b, 0, "li R2, #%d" % (d - 1))
                cfg["CSDLY"] = d
            xf = [[b.r.getrandbits(8) for _ in range(4)], [b.r.getrandbits(8)]]    # 2nd transfer's bytes wait
            tr, got, flat = await ctrl_xfers(b, cfg, xf)
            st, ina = cs_times(cfg, tr)
            cocotb.log.info("SC2 mode %d CSDLY %d: (setup-fix, hold) %s, inactive %s", mode, d, st, ina)
            check_cs(cfg, st, ina, waiting=True)


def check_cs(cfg, st, ina, waiting=False):
    # overheads recorded from the ISS (D091): setup = CSDLY + BIT (+ SMP for CPHA 0) + 2 for CSDLY >= 9 (the tset
    # after CS low is then prefetched, D018), up to 8 clk more below (ISS with host data waiting: + 2 at CSDLY 6);
    # hold = CSDLY + 11..16 (WL > 8: ..20); inactive = max(CSDLY + 8, 20) (WL > 8: + 4) with the next transfer's
    # host bytes waiting, longer when they arrive later
    d, big = cfg["CSDLY"], cfg["WL"] > 8
    for s, h in st:
        assert s == 2 if d >= 9 else 2 <= s <= 10, ("setup", s, cfg)
        assert 11 <= h <= (20 if big else 16), ("hold", h, cfg)
    for i in ina:
        m = max(d + 8, 20) + (4 if big else 0)
        assert i + d == m if waiting else i + d >= m, ("inactive", i, cfg)


@cocotb.test()
async def test_sc3_answer_window(dut):
    # SC3: target clock-to-MISO delay (pad round trip 10 ns included) 0 / 35 / 49 ns correct, 51 / 70 ns wrong
    b = await sbench(dut, 23)
    for mode in (0, 3):
        cfg = ccfg(mode)
        await ctrl_load(b, cfg)
        tw = [[0x55, 0xAA, 0x5A, 0xA5]]
        for ns, ok in ((0, True), (35, True), (49, True), (51, False), (70, False)):
            tr, got, flat = await ctrl_xfers(b, cfg, [[1, 2, 3, 4]], delay=(ns + 10) / 20.0, tw=tw, check=False)
            assert (got == flat) == ok, (mode, ns, got, flat)


@cocotb.test()
async def test_sc4_host_late(dut):
    # SC4: words 1.. of a transfer sent 3000 clk late; then a 256-word RDMEM reply holds H_TX during a 32-word
    # transfer. SCK idles with CS low, one CS window, no word lost or repeated
    b = await sbench(dut, 24, fill=True)
    fill = b.fill
    cfg = ccfg(1, 0, 8)
    img = img_of(["spi_ctrl"], **cfg)
    for a, w in img.words.items():
        fill[a % MEM_WORDS] = w
    await ctrl_load(b, cfg)
    tr, got, flat = await ctrl_xfers(b, cfg, [[b.r.getrandbits(8) for _ in range(3)]], late=(3000, 1))
    gap = tr[0]["lead"][8] - tr[0]["lead"][7]
    assert gap > 2500, gap
    b.rd_len = 512
    b.hl.send(bytes([ESC, 0x71, 0, 0, 255]))
    await b.run(300)
    tr, got, flat = await ctrl_xfers(b, cfg, [[b.r.getrandbits(8) for _ in range(32)]])
    lead = tr[0]["lead"]
    gaps = [lead[k + 1] - lead[k] for k in range(7, len(lead) - 1, 8)]
    assert max(gaps) > 10000, gaps                        # stalled behind the reply
    x, p = b.replies.pop(0)
    assert x == 0xC1 and [p[2 * k] | p[2 * k + 1] << 8 for k in range(256)] == [fill[k % MEM_WORDS] for k in range(256)]


# ---------------- target (spi_tgt.s, controller models on SCK / CS / MOSI)
async def tgt_load(b, m12, eng=0):
    b.partner = None
    await b.load(img_of(["spi_tgt"], MODE12=m12, ENG=eng))
    await b.run(20)


def drive_check(oe_ch, wins):
    # ST2: MISO driven <= 4 clk after CS falls until CS rises; a CS fall during the idle path is latched, MISO then
    # driven <= 25 clk after the previous CS rise (spi_tgt.s, D091); released <= 9 clk after CS rises
    def oe_at(t):
        k = bisect.bisect_right([x[0] for x in oe_ch], t) - 1
        return oe_ch[k][1] if k >= 0 else 0
    for k, (lo, hi) in enumerate(wins):
        nxt = wins[k + 1][0] if k + 1 < len(wins) else hi + 40
        on = max(lo, wins[k - 1][1] + 21 if k else lo) + 4
        if hi > on:
            bad = [e for e in oe_ch[1:] if on < e[0] < hi]
            assert not bad, ("MISO oe changes while CS is low", lo, hi, bad)
            assert oe_at(on) == 1, ("MISO not driven 4 clk after CS fell", lo)
        bad = [e for e in oe_ch[1:] if hi + 9 < e[0] < nxt]
        assert not bad, ("MISO oe changes while CS is high", lo, hi, nxt, bad)
        assert oe_at(hi + 9) == 0 and (k or oe_at(lo - 1e-6) == 0), ("MISO not released 9 clk after CS rose", hi)


def margins(x, miso):
    # MISO setup / hold at the controller model's sampling edges of the response bytes (ns)
    ts = [t for t, v in miso]
    su, ho = 1e9, 1e9
    for i, s in enumerate(x.samp[8:], 8):
        if s >= x.cs_hi:
            break
        k = bisect.bisect_left(ts, s)
        su = min(su, s - ts[k - 1])
        if k < len(ts) and i < len(x.samp) - 1:
            ho = min(ho, ts[k] - s)
    return su * 20, ho * 20


async def tgt_run(b, bus, want, T=None):
    # run until the bus is done; check each full transfer's MISO bytes, drive / release, setup / hold
    t0 = b.now
    b.clear()
    b.partner = bus
    await b.run(int(bus.end - b.now) + 40)
    miso = b.lv(2, t0)                      # released MISO reads high: the command-time 0x00 must be driven
    for x, w in want:
        got = x.sample(miso)
        assert got == w, (got, w, x.T, x.mode)
        if T in SETUP_NS and len(w) > 1:
            su, ho = margins(x, miso)
            assert su >= SETUP_NS[T] - 1e-6 and ho >= 60, ("MISO setup / hold ns", su, ho, T)
    drive_check(b.lv(2, t0, lambda o, oe: oe >> 2 & 1), [w for w in bus.windows() if w[0] > t0])
    assert b.notes() == [] and not any(g.ovr for g in b.chip.eng), b.notes()
    b.partner = None


async def rst_tgt(b):
    # H5: rst_n in the middle of a target transfer (MISO driven); pads at reset values within 3 clk; memory refilled
    await tgt_load(b, 0)
    bus = Bus(0)
    x = bus.add(SpiController(25, 0, [1, 0, 0], t0=b.now + 30))
    b.partner = bus
    await b.run(int(x.lead[b.r.randrange(4, 20)] - b.now))
    assert b.chip.uio_oe & 4, "MISO not driven mid-transfer"
    d = b.dut
    d.rst_n.value = 0
    seen = []
    for _ in range(3):
        await FallingEdge(d.clk)
        seen.append((int(d.uo_out.value), int(d.uio_out.value), int(d.uio_oe.value)))
    assert seen[-1] == (0x10, 0, 0), seen
    b.partner = None
    await b.reset(4)
    await b.sync()
    await b.set_link(2)
    for a in range(0, MEM_WORDS, 256):
        await b.wrmem(a, b.fill[a:a + 256])


def st1_rates():
    if GL:
        return [25]
    return [500, 50, 25, 12.5, 11.25] if FULL else [11.25, 12.5]


@cocotb.test()
async def test_st1_st2_tgt_modes(dut):
    # ST1 + ST2: modes 0-3 at the ISS-measured maximum (T 11.25 clk = 4.44 MHz), 4 MHz and 2 MHz, commands 0 / 1 / 7
    # with 7 / 2 / 1 response bytes, random partner phase (all four with FULL); mode 0 also at 1 and 0.1 MHz.
    # H5 first: rst_n while spi_tgt drives MISO
    b = await sbench(dut, 31, fill=True)
    await rst_tgt(b)
    for m12 in ((0,) if GL else (0, 1)):
        await tgt_load(b, m12)
        for mode in ((0,) if GL else ((0, 3) if not m12 else (1, 2))):
            rates = st1_rates() + ([] if FULL or GL else [25] + ([50, 500] if mode == 0 else []))
            for T in rates:
                for ph in (PHASES if FULL else (b.r.choice(PHASES),)):
                    bus, want, t = Bus(mode >> 1), [], b.now + 30
                    cmds = [(1, 1)] if T == 500 and not FULL else CMDS
                    for c, n in cmds:
                        x = bus.add(SpiController(T, mode, [c] + [0] * n, phase=ph, t0=t))
                        want.append((x, [0] + TAB[c:c + n]))
                        t = x.cs_hi + 25
                    await tgt_run(b, bus, want, T)
                    cocotb.log.info("ST1 mode %d T %g phase %g ok", mode, T, ph)


@cocotb.test()
async def test_st3_cs_abort(dut):
    # ST3 + EI1 (SPI): CS raised after 1..7 bits of the command and of each response byte; CS glitch of 3 clk with
    # no SCK; the next transfer is correct from its first bit
    b = await sbench(dut, 33, fill=True)
    await tgt_load(b, 0)
    T = 12.5
    pos = [4] if GL else [k for k in range(1, 24) if k % 8]
    for mode in ((0,) if GL else (0, 3)):
        bus, want, t = Bus(mode >> 1), [], b.now + 30
        for k in pos + [0]:
            ph = b.r.choice(PHASES)
            if k:
                x = bus.add(SpiController(T, mode, [b.r.randrange(8), 0, 0], phase=ph, t0=t, cs_cuts=k))
            else:
                x = bus.add(SpiController(T, mode, [], phase=ph, t0=t, cs_setup=0, cs_hold=3))
            c = b.r.randrange(7)
            y = bus.add(SpiController(T, mode, [c, 0, 0], phase=ph, t0=x.cs_hi + 25))
            want.append((y, [0] + TAB[c:c + 2]))
            t = y.cs_hi + 25
        await tgt_run(b, bus, want, T)


@cocotb.test()
async def test_st4_st5_shared_bus(dut):
    # ST4 + ST5 (D055, D100): first sampling edge 32 clk after the previous CS rise and 11 clk after CS falls; CS high for
    # only 5 clk; SCK / MOSI pulses anywhere while CS is high; a whole transfer to another device (CS high)
    b = await sbench(dut, 34, fill=True)
    await tgt_load(b, 0)
    T = 12.5
    for mode in (0, 3):
        bus, want, t = Bus(mode >> 1), [], b.now + 30
        lead = T / 2 if mode & 1 else 0.0           # CPHA 1: the first sampling edge is half a period later
        gaps, prev = [], None
        for k in range(10 if FULL else 5):
            for gap, setup in ((24.0, 11.0), (5.0, 27.0)):       # CS high time, then CS fall -> sampling edge
                t = t if prev is None else prev + gap
                c = b.r.randrange(7)
                x = bus.add(SpiController(T, mode, [c, 0, 0], phase=b.r.choice(PHASES), t0=t,
                                          cs_setup=setup - lead))
                want.append((x, [0] + TAB[c:c + 2]))
                if prev is not None:
                    gaps.append((prev, x.cs_lo))
                prev = x.cs_hi
        t = prev
        # another device's transfer: SCK at 10 clk, MOSI toggling, CS high
        t += 40
        other = (t, t + 160)
        y = bus.add(SpiController(T, mode, [3, 0, 0], phase=0.25, t0=t + 200, cs_setup=7.0 - lead))
        want.append((y, [0] + TAB[3:5]))
        for a, z in gaps:
            p = a + 0.5
            for _ in range(b.r.randint(1, 3)):
                w = b.r.choice((0.5, 1.0, 2.0, 3.0))
                if p + w > z - 0.1:
                    break
                s = b.r.uniform(p, z - 0.1 - w)
                bus.pulse(s, s + w)
                p = s + w + 0.5
        for s in range(int(other[0]), int(other[1]), 10):
            bus.pulse(s, s + 5)
        await tgt_run(b, bus, want, T)
        cocotb.log.info("ST4/ST5 mode %d: %d noise pulses", mode, len(bus.noise))


# ---------------- controller and target on two engines (spi_ctrl E0 -> spi_tgt E1 over uio[3:0])
@cocotb.test()
async def test_ctrl_tgt_two_engines(dut):
    # E0 spi_ctrl (BIT 12 = 4.17 MHz) talks to E1 spi_tgt through the same pads, modes 0-3: E0's host stream gets
    # 0x00 then table[c], table[c+1], ...; SCK grid and CS rules hold on the pads; MISO released while CS is high
    b = await sbench(dut, 41, fill=True)
    for mode in ((0,) if GL else range(4)):
        cfg = ccfg(mode, bit=12, smp=6)
        defs = {"spi_ctrl.s:" + k: v for k, v in cfg.items()}
        defs.update({"spi_tgt.s:ENG": 1, "spi_tgt.s:MODE12": int(mode in (1, 2))})
        b.partner = None
        await b.load(img_of(["spi_ctrl", "spi_tgt"], **defs))
        await b.run(20)
        t0 = b.now
        b.clear()
        cmds = CMDS[:1] if GL else CMDS
        for c, n in cmds:
            b.send(0, [1 + n, c] + [b.r.getrandbits(8) for _ in range(n)])
        want = [x for c, n in cmds for x in [0] + TAB[c:c + n]]
        await b.run(10 ** 6, lambda: len(b.rx_data(0)) >= len(want) and b.chip.uio_out & 1, "two-engine SPI")
        await b.run(20)
        tr = spi_ctrl_check(b.lv(3, t0), b.lv(0, t0), b.lv(1, t0), mode, 8, 12, 6, t0)
        assert b.rx_data(0) == want, (b.rx_data(0), want)
        assert len(tr) == len(cmds) and [w["mosi"][0] for w in tr] == [c for c, n in cmds], [w["mosi"] for w in tr]
        drive_check(b.lv(2, t0, lambda o, oe: oe >> 2 & 1), [(w["lo"], w["hi"]) for w in tr])
        assert b.notes() == [] and not any(g.ovr for g in b.chip.eng), b.notes()
        cocotb.log.info("two engines mode %d: %s", mode, b.rx_data(0))


@cocotb.test()
async def test_st2_cs_high_min(dut):
    # ST2 boundary (D093): CS high 21 clk -> MISO driven <= 4 clk after CS falls; 20 clk -> latched, driven <= 25 clk
    # after the CS rise, late by up to 1 clk in some of 8 partner phases; data correct in both
    b = await sbench(dut, 35, fill=True)
    await tgt_load(b, 0)
    for mode in (0, 3):
        for h in (21, 20):
            bus, want, t, pairs = Bus(mode >> 1), [], b.now + 30, []
            for k in range(8):
                x1 = bus.add(SpiController(12.5, mode, [1, 0], phase=k / 8, t0=t))
                x2 = bus.add(SpiController(12.5, mode, [2, 0, 0], t0=x1.cs_hi + h, cs_setup=max(32 - h, 11)))
                want += [(x1, [0, 0x5A]), (x2, [0, 1, 2])]
                pairs.append(x2)
                t = x2.cs_hi + 40
            t0 = b.now
            await tgt_run(b, bus, want, 12.5)
            oe = b.lv(2, t0, lambda o, oe: oe >> 2 & 1)
            d = [min(e[0] for e in oe if e[1] == 1 and e[0] >= x.cs_lo) - x.cs_lo for x in pairs]
            assert max(d) <= (4 if h == 21 else 25 - h), (mode, h, d)
            if h == 20 and mode == 0:
                assert max(d) > 4, d
            cocotb.log.info("ST2 CS high %d clk mode %d: MISO driven %s clk after CS fall", h, mode, d)
