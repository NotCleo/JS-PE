# spi_ctrl.s and spi_tgt.s against the independent SPI models (SC1, SC2, SC4, ST1-ST5 subset at the ISS level).
import pytest
from harness import prog, boot, simulate, changes
from models import SpiController, SpiTargetOnline, spi_ctrl_check, pad_levels, level_at
import random
from test_first import Seq, uio_spi, spi_transactions


class OnlineTarget:
    # SPI target for any mode, answering from the chip's SCK/CS pad history; MISO changes `delay` clk after the
    # launch edge (CS fall for the first bit in CPHA 0)
    def __init__(self, mode, data, delay=1.0):
        self.cpol, self.cpha = mode >> 1, mode & 1
        self.bits = [(b >> (7 - i)) & 1 for b in data for i in range(8)]
        self.delay, self.k, self.seen, self.ch = delay, 0, 0, []
        self.prev = None

    def update(self, chip):
        tr = chip.pad_trace
        while self.seen < len(tr):
            t, uo, out, oe = tr[self.seen]
            self.seen += 1
            sck, cs = ((out >> 3) & 1) ^ self.cpol, out & 1
            if self.prev is None:
                self.prev = (sck, cs)
                continue
            psck, pcs = self.prev
            self.prev = (sck, cs)
            launch = (pcs and not cs and not self.cpha) or \
                (not cs and psck != sck and ((not sck) if not self.cpha else sck))
            if launch and self.k < len(self.bits):
                self.ch.append((t + self.delay, self.bits[self.k]))
                self.k += 1

    def level(self, t):
        return level_at(self.ch, t, 0)


def ctrl_run(mode, out_bytes, in_bytes, slot=0, **defs):
    img = prog("spi_ctrl", CPHA=mode & 1, CPOL=mode >> 1, **defs)
    c = boot(img, slot)
    tgt = OnlineTarget(mode, in_bytes)
    c.host_in[0] += [len(out_bytes)] + list(out_bytes)

    def inputs(t, ch):
        tgt.update(ch)
        return 8, tgt.level(t) << 2
    simulate(c, 600 + 150 * len(out_bytes), inputs)
    sck = changes(c, lambda uo, o, oe: (o >> 3) & 1, 0)
    cs = changes(c, lambda uo, o, oe: o & 1, 0)
    mosi = changes(c, lambda uo, o, oe: (o >> 1) & 1, 0)
    return c, sck, cs, mosi


@pytest.mark.parametrize("mode,slot", [(0, 0), (1, 1), (2, 2), (3, 3)])
def test_ctrl_modes(mode, slot):
    out_b, in_b = [0x3C, 0xA5, 0x01, 0xFE], [0x81, 0x7E, 0x55, 0xC3]
    c, sck, cs, mosi = ctrl_run(mode, out_b, in_b, slot)
    assert c.host_bytes(0) == in_b
    cpol, cpha = mode >> 1, mode & 1
    cs_lo = [t for t, v in cs if v == 0][-1]
    cs_hi = [t for t, v in cs if v == 1 and t > cs_lo][0]
    edges = [(t, v) for t, v in sck if cs_lo < t < cs_hi]
    lead = [t for t, v in edges if v != cpol]
    samp = lead if not cpha else [t for t, v in edges if v == cpol]
    assert len(lead) == 32 and sck[-1][1] == cpol
    got = [level_at(mosi, t - 1e-6, 0) for t in samp]
    bits = [(b >> (7 - i)) & 1 for b in out_b for i in range(8)]
    assert got == bits
    for k in range(0, 32, 8):
        assert all(b - a == 8 for a, b in zip(lead[k:k + 8], lead[k + 1:k + 8])), lead[k:k + 8]


@pytest.mark.parametrize("csdly", [6, 40])
def test_ctrl_cs_timing(csdly):
    c, sck, cs, mosi = ctrl_run(0, [0x12], [0x34], 0, CSDLY=csdly)
    cs_lo = [t for t, v in cs if v == 0][-1]
    cs_hi = [t for t, v in cs if v == 1 and t > cs_lo][0]
    first = [t for t, v in sck if v == 1 and t > cs_lo][0]
    last = [t for t, v in sck if v == 0 and t < cs_hi][-1]
    assert c.host_bytes(0) == [0x34]
    setup, hold = first - cs_lo, cs_hi - last
    assert csdly <= setup <= csdly + 2 * 8 + 16 and csdly <= hold <= csdly + 16, (setup, hold)


def test_ctrl_host_late():
    # SC4: second byte arrives late; SCK idles with CS low, no byte lost or repeated
    img = prog("spi_ctrl")
    c = boot(img, 1)
    tgt = OnlineTarget(0, [0x11, 0x22])
    c.host_in[0] += [2, 0xA1]

    def inputs(t, ch):
        tgt.update(ch)
        if ch.cycle == 600:
            ch.host_in[0].append(0xB2)
        return 8, tgt.level(t) << 2
    simulate(c, 1200, inputs)
    assert c.host_bytes(0) == [0x11, 0x22]
    cs = changes(c, lambda uo, o, oe: o & 1, 0)
    assert [v for t, v in cs] == [1, 0, 1]


def ctrl_fmt_run(mode, lsb, wl, slot, xfers=(3, 1), bit=8, smp=6, csdly=16, seed=1):
    # CPOL / LSB / WL by .equ (spi_ctrl.s); transfers queued back to back; checked by spi_ctrl_check
    r = random.Random(seed)
    img = prog("spi_ctrl", CPHA=mode & 1, CPOL=mode >> 1, LSB=lsb, WL=wl, BIT=bit, SMP=smp, CSDLY=csdly)
    c = boot(img, slot)
    out_w = [[r.getrandbits(wl) for _ in range(n)] for n in xfers]
    in_w = [r.getrandbits(wl) for _ in range(sum(xfers))]
    tgt = SpiTargetOnline(mode, in_w, wl, bool(lsb))
    for x in out_w:
        c.host_in[0] += [len(x)] + [b for w in x for b in ((w & 0xFF, w >> 8) if wl > 8 else (w,))]

    def inputs(t, ch):
        tgt.update(ch.pad_trace)
        return 8, tgt.level(t) << 2
    simulate(c, 600 + 3 * 3 * csdly + 2 * (bit * wl + 80) * sum(xfers), inputs)
    lv = [changes(c, lambda uo, o, oe, k=k: pad_levels(o, oe) >> k & 1, 1) for k in (3, 0, 1)]
    tr = spi_ctrl_check(*lv, mode, wl, bit, smp, lsb=bool(lsb))
    hb = c.host_bytes(0)
    got = [hb[k] | hb[k + 1] << 8 for k in range(0, len(hb) - 1, 2)] if wl > 8 else hb
    assert got == in_w and [w["mosi"] for w in tr] == out_w and tgt.words() == [w for x in out_w for w in x]
    assert c.eng[0].ovr == 0
    return tr


@pytest.mark.parametrize("wl", [1, 4, 7, 8, 9, 12, 16])
@pytest.mark.parametrize("lsb", [0, 1])
@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_ctrl_formats(mode, lsb, wl):
    # SC1 at I: modes x bit order x word length, all four slot phases, two transfers back to back
    for slot in range(4):
        ctrl_fmt_run(mode, lsb, wl, slot, seed=slot)


@pytest.mark.parametrize("bit,smp", [(8, 6), (10, 5), (50, 25), (12, 8), (13, 6)])
@pytest.mark.parametrize("csdly", [6, 7, 8, 12, 16, 1000])
def test_ctrl_cs_overhead(bit, smp, csdly):
    # SC2 at I (D091), host data waiting: setup = CSDLY + BIT (+ SMP, CPHA 0) + 2, + 2 at CSDLY 6; hold = CSDLY +
    # 11..16 (WL > 8: ..20); inactive = max(CSDLY + 8, 20) (WL > 8: + 4)
    for mode in (0, 1):
        for wl in (8, 16):
            for slot in range(4):
                tr = ctrl_fmt_run(mode, 0, wl, slot, (slot % 3 + 1, 1), bit, smp, csdly, seed=slot)
                fix = bit + (0 if mode else smp) + 2 + (2 if csdly == 6 else 0)
                for w in tr:
                    assert w["setup"] == csdly + fix and 11 <= w["hold"] - csdly <= (20 if wl > 8 else 16), w
                assert tr[1]["lo"] - tr[0]["hi"] == max(csdly + 8, 20) + (4 if wl > 8 else 0)


# ---------------------------------------------------------------- target
def tgt_run(T, mode, cmds, nresp, slot=0, phase=0.0, **defs):
    img = prog("spi_tgt", **defs)
    c = boot(img, slot)
    ctrls = []
    t = c.cycle + 40
    for cm in cmds:
        x = SpiController(T, mode, [cm] + [0] * nresp, phase=phase, t0=t)
        ctrls.append(x)
        t = x.cs_hi + 40 - phase
    cpol = mode >> 1

    def inputs(tt, ch):
        x = Seq(ctrls)
        miso = (ch.uio_out >> 2) & (ch.uio_oe >> 2) & 1
        sck = x.sck(tt) if tt >= ctrls[0].cs_lo else cpol
        return 8, sck << 3 | x.cs(tt) | x.mosi(tt) << 1 | miso << 2
    simulate(c, int(ctrls[-1].cs_hi) + 40, inputs)
    miso = changes(c, lambda uo, o, oe: (o >> 2) & (oe >> 2) & 1)
    return c, img, ctrls, [x.sample(miso) for x in ctrls]


TAB = [0xA5, 0x5A, 1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize("mode,T,slot,defs", [(0, 50, 0, {}), (3, 25, 1, {}), (1, 25, 2, {"MODE12": 1}),
                                              (2, 50, 3, {"MODE12": 1}), (0, 12.5, 2, {})])
def test_tgt_modes(mode, T, slot, defs):
    c, img, ctrls, got = tgt_run(T, mode, [0, 1, 6], 2, slot, **defs)
    for cm, g in zip([0, 1, 6], got):
        assert g == [0] + TAB[cm:cm + 2], (cm, g)
    assert c.eng[0].ovr == 0


def test_tgt_miso_drive_release():
    # ST2: high-Z while CS high; driven <= 4 clk after CS falls, released <= 9 clk after CS rises (at the pad, D081)
    c, img, ctrls, got = tgt_run(25, 0, [0, 2], 1, 0)
    oe = changes(c, lambda uo, o, e: (e >> 2) & 1)
    drive = [t for t, v in oe if v == 1]
    rel = [t for t, v in oe if v == 0]
    for x, d, r in zip(ctrls, drive, rel):
        assert d - x.cs_lo <= 4 and r - x.cs_hi <= 9, (d - x.cs_lo, r - x.cs_hi)
    assert len(drive) == 2 and len(rel) == 2


def test_tgt_ignores_bus_while_cs_high():
    # ST5 + ST4: SCK/MOSI toggling with CS high is ignored; transfer after a short CS-high gap is correct
    img = prog("spi_tgt")
    c = boot(img, 2)
    t0 = c.cycle + 40
    noise = SpiController(10, 0, [0xFF, 0x00], t0=t0)
    x = SpiController(25, 0, [3, 0, 0], t0=noise.cs_hi + 30)

    def inputs(tt, ch):
        miso = (ch.uio_out >> 2) & (ch.uio_oe >> 2) & 1
        if tt < noise.cs_hi:
            return 8, noise.sck(tt) << 3 | 1 | noise.mosi(tt) << 1 | miso << 2
        return 8, x.sck(tt) << 3 | x.cs(tt) | x.mosi(tt) << 1 | miso << 2
    simulate(c, int(x.cs_hi) + 40, inputs)
    miso = changes(c, lambda uo, o, oe: (o >> 2) & (oe >> 2) & 1)
    oe = changes(c, lambda uo, o, e: (e >> 2) & 1)
    assert x.sample(miso) == [0, 2, 3]
    assert all(t >= x.cs_lo for t, v in oe if v == 1)


@pytest.mark.slow
@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_ctrl_rates_sizes(mode):
    # SC1 at I, rest of the cross: BIT/SMP 10/5, 50/25, 500/250 in both bit orders; n = 1, 2, 16, 255 at 8/6
    for lsb in (0, 1):
        for bit, smp in ((10, 5), (50, 25), (500, 250)):
            ctrl_fmt_run(mode, lsb, 12 if lsb else 8, mode, (2, 1), bit, smp)
    for n in (1, 2, 16, 255):
        ctrl_fmt_run(mode, 0, 8, n % 4, (n, 1))


def tgt_bus_run(img, slot, T, mode, xs, noise=()):
    # xs: controllers in time order (cut ones included); noise: (t0, t1) SCK pulses with MOSI high while CS is high
    c = boot(img, slot)
    cpol = mode >> 1
    seq = Seq(xs)

    def inputs(tt, ch):
        lv = (ch.uio_out & ch.uio_oe | ~ch.uio_oe) >> 2 & 1          # released MISO reads high
        if any(a <= tt < b for a, b in noise):
            return 8, (1 ^ cpol) << 3 | 3 | lv << 2
        sck = seq.sck(tt) if tt >= xs[0].cs_lo else cpol
        return 8, sck << 3 | seq.cs(tt) | seq.mosi(tt) << 1 | lv << 2
    simulate(c, int(xs[-1].cs_hi) + 40, inputs)
    return c, changes(c, lambda uo, o, oe: (o & oe | ~oe) >> 2 & 1, 1)


@pytest.mark.parametrize("mode,defs", [(0, {}), (3, {}), (1, {"MODE12": 1})])
def test_tgt_cs_cuts(mode, defs):
    # ST3 + EI1 at I: CS raised after 1..7 bits of the command and of each response byte; a 3-clk CS glitch;
    # the following transfer is correct from its first bit
    img = prog("spi_tgt", **defs)
    for slot in range(4):
        xs, full, t = [], [], 200.0
        for k in [k for k in range(1, 24) if k % 8] + [0]:
            ph = (k + slot) % 4 * 0.25
            x = SpiController(12.5, mode, [k % 7, 0, 0], phase=ph, t0=t, cs_cuts=k) if k else \
                SpiController(12.5, mode, [], phase=ph, t0=t, cs_setup=0, cs_hold=3)
            y = SpiController(12.5, mode, [(k + 3) % 7, 0, 0], phase=ph, t0=x.cs_hi + 25)
            xs += [x, y]
            full.append(y)
            t = y.cs_hi + 25
        c, miso = tgt_bus_run(img, slot, 12.5, mode, xs)
        for y in full:
            cm = y.tx[0]
            assert y.sample(miso) == [0] + TAB[cm:cm + 2], (slot, cm)
        assert c.eng[0].ovr == 0


@pytest.mark.parametrize("mode", [0, 3])
def test_tgt_shared_bus_spacing(mode):
    # ST4 + ST5 at I (D055, D100): first sampling edge 32 clk after the previous CS rise and 11 clk after CS falls
    # (CS high < 25 clk), or CS high for only 5 clk; SCK / MOSI pulses anywhere while CS is high
    img = prog("spi_tgt")
    lead = 6.25 if mode & 1 else 0.0
    for seed in range(16):
        r = random.Random(seed)
        xs, noise, prev, t = [], [], None, 200.0
        for k in range(3):
            for gap, setup in ((24.0, 11.0), (5.0, 27.0)):
                t = t if prev is None else prev + gap
                x = SpiController(12.5, mode, [r.randrange(7), 0, 0], phase=r.choice((0, .25, .5, .75)), t0=t,
                                  cs_setup=setup - lead)
                if prev is not None:
                    p = prev + 0.5
                    for _ in range(r.randint(1, 3)):
                        w = r.choice((0.5, 1.0, 2.0, 3.0))
                        if p + w > x.cs_lo - 0.1:
                            break
                        s0 = r.uniform(p, x.cs_lo - 0.1 - w)
                        noise.append((s0, s0 + w))
                        p = s0 + w + 0.5
                xs.append(x)
                prev = x.cs_hi
        c, miso = tgt_bus_run(img, seed % 4, 12.5, mode, xs, noise)
        for x in xs:
            assert x.sample(miso) == [0] + TAB[x.tx[0]:x.tx[0] + 2], (seed, x.tx[0])
        assert c.eng[0].ovr == 0


@pytest.mark.parametrize("mode", [0, 1, 2, 3])
def test_tgt_cs_high_min(mode):
    # ST2 boundary (D093): CS high 21 clk -> MISO driven <= 4 clk after CS falls in every phase; 20 clk -> the fall
    # is latched, driven <= 25 clk after the CS rise (late by 1 clk in modes 0 and 2); data correct in both
    img = prog("spi_tgt", MODE12=int(mode in (1, 2)))
    for h in (21, 20):
        worst = 0.0
        for slot in range(4):
            for ph in [k / 8 for k in range(8)]:
                x1 = SpiController(12.5, mode, [1, 0], phase=ph, t0=200)
                x2 = SpiController(12.5, mode, [2, 0, 0], t0=x1.cs_hi + h, cs_setup=max(32 - h, 11))
                c, miso = tgt_bus_run(img, slot, 12.5, mode, [x1, x2])
                oe = changes(c, lambda uo, o, e: e >> 2 & 1)
                d = [t for t, v in oe if v == 1 and t >= x2.cs_lo][0] - x2.cs_lo
                assert d <= (4 if h == 21 else 25 - h) and x2.sample(miso) == [0, 1, 2], (h, slot, ph, d)
                worst = max(worst, d)
        if h == 20 and mode in (0, 2):
            assert worst > 4, worst
