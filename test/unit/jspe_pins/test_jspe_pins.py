# jspe_pins block test (VERIF_PLAN 5): every clock against pin_filter / pad_outputs of the ISS (sw/jspe_iss.py).
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

from jspe_iss import pad_outputs, pin_filter

SEED = int(os.environ.get("JSPE_SEED", "1"))
NCLK = int(os.environ.get("JSPE_CLK", "20000"))
EN, OD, INV = 0x40, 0x20, 0x10


def unpack(v):
    return [[v >> (4 * e + p) & 1 for p in range(4)] for e in range(4)]


class Bench:
    def __init__(self, dut):
        self.dut = dut
        self.clk_n = 0
        self.ff1 = self.ff2 = self.h1 = self.h2 = self.held = 0
        self.pad = [0] * 16
        self.out = (0x10, 0, 0)
        self.cov = set()
        self.known = False  # synchronizer / history flops hold driven values

    async def start(self):
        d = self.dut
        cocotb.start_soon(Clock(d.clk, 20, unit="ns").start())
        for n in ("ui_in", "uio_in", "filt", "e_val", "e_oe", "pad_we", "pad_idx", "pad_wdata", "h_tx", "h_rdy"):
            getattr(d, n).value = 0
        d.rst_n.value = 0
        for _ in range(4):
            await RisingEdge(d.clk)
        self.known = True
        d.rst_n.value = 1

    def rd(self, n):
        try:
            return int(getattr(self.dut, n).value)
        except ValueError:
            raise AssertionError("clk %d: %s is %s" % (self.clk_n, n, getattr(self.dut, n).value)) from None

    # one clock; pw = None or (pad index, 7-bit configuration)
    async def step(self, ui=0, uio=0, filt=0, val=0, oe=0, pw=None, h_tx=1, h_rdy=0, rst_n=1):
        d = self.dut
        await FallingEdge(d.clk)
        d.ui_in.value, d.uio_in.value, d.filt.value = ui, uio, filt
        d.e_val.value, d.e_oe.value = val, oe
        d.pad_we.value = int(pw is not None)
        d.pad_idx.value, d.pad_wdata.value = pw if pw is not None else (0, 0)
        d.h_tx.value, d.h_rdy.value, d.rst_n.value = h_tx, h_rdy, rst_n
        await Timer(1, unit="ns")
        lvl, held_n = pin_filter(self.ff2, self.h1, self.h2, self.held, filt)
        got = (self.rd("uo_out"), self.rd("uio_out"), self.rd("uio_oe"))
        assert got == self.out, "clk %d: pads uo/uio/oe %02x/%02x/%02x, expected %02x/%02x/%02x" % (
            (self.clk_n,) + got + self.out)
        assert got[1] & got[2] & self.od_mask() == 0, "clk %d: open-drain pad drives 1" % self.clk_n
        if self.known:
            assert self.rd("lvl_raw") == self.ff2, "clk %d: lvl_raw %04x, expected %04x" % (
                self.clk_n, self.rd("lvl_raw"), self.ff2)
            assert self.rd("lvl") == lvl, "clk %d: lvl %04x, expected %04x (filt %04x)" % (
                self.clk_n, self.rd("lvl"), lvl, filt)
        vs, os_ = unpack(val), unpack(oe)
        for j in range(16):
            c = self.pad[j]
            self.cov.add((j, c, vs[c >> 2 & 3][c & 3], os_[c >> 2 & 3][c & 3]))
        await RisingEdge(d.clk)
        self.prev_cfg = list(self.pad)
        self.out = pad_outputs(self.pad, vs, os_, h_tx, h_rdy)
        if pw is not None:
            self.pad[pw[0]] = pw[1]
        self.held = held_n
        self.h2, self.h1, self.ff2, self.ff1 = self.h1, self.ff2, self.ff1, ui | uio << 8
        if not rst_n:
            self.pad = [0] * 16
            self.out = (0x10, 0, 0)
            self.held = 0
        self.clk_n += 1

    def od_mask(self):
        cfg = getattr(self, "prev_cfg", self.pad)
        return sum(1 << (j - 8) for j in range(8, 16) if cfg[j] & OD)

    async def write_all(self, c, **kw):
        for j in range(16):
            await self.step(pw=(j, c), **kw)


@cocotb.test()
async def reset_values(dut):
    b = Bench(dut)
    await b.start()
    # all pads disabled after reset: nothing but H_TX / H_RDY reaches a pad whatever the engines drive
    for _ in range(200):
        await b.step(val=random.getrandbits(16), oe=random.getrandbits(16), h_tx=random.getrandbits(1),
                     h_rdy=random.getrandbits(1), ui=random.getrandbits(8), uio=random.getrandbits(8))
    assert b.rd("uio_oe") == 0 and b.rd("uio_out") == 0 and b.rd("uo_out") & 0xCF == 0


@cocotb.test()
async def sync_latency(dut):
    b = Bench(dut)
    await b.start()
    for k in range(16):
        for lvl0 in (1, 0):
            v = lvl0 << k
            await b.step(ui=v & 0xFF, uio=v >> 8)
            seen = None
            for n in range(1, 5):
                await b.step(ui=v & 0xFF, uio=v >> 8)
                if seen is None and (b.rd("lvl_raw") >> k & 1) == lvl0:
                    seen = n
            assert seen == 2, "input %d: lvl_raw after %s clk" % (k, seen)


@cocotb.test()
async def filter_pulses(dut):
    b = Bench(dut)
    await b.start()
    for filt in (0xFFFF, 0x5A5A, 0):
        for base in (0, 0xFFFF):
            for w in (1, 2, 3, 4):
                for _ in range(6):
                    await b.step(ui=base & 0xFF, uio=base >> 8, filt=filt)
                hit = 0
                for _ in range(w):
                    p = base ^ 0xFFFF
                    await b.step(ui=p & 0xFF, uio=p >> 8, filt=filt)
                for _ in range(6):
                    await b.step(ui=base & 0xFF, uio=base >> 8, filt=filt)
                    hit |= (b.rd("lvl") ^ base) & filt
                    if w < 3:
                        assert hit == 0, "filt %04x: pulse of %d samples passed" % (filt, w)
                if w >= 3:
                    assert hit == filt, "filt %04x: pulse of %d samples blocked (%04x)" % (filt, w, hit)


@cocotb.test()
async def every_pad_mode(dut):
    b = Bench(dut)
    await b.start()
    # every pad x every configuration x owner (value, enable); all other logical pins drive the opposite
    for c in range(128):
        await b.write_all(c)
        own = c & 15
        for vo in range(4):
            v, o = vo & 1, vo >> 1
            val = (1 << own) if v else 0xFFFF ^ (1 << own)
            oe = (1 << own) if o else 0xFFFF ^ (1 << own)
            await b.step(val=val, oe=oe, h_tx=vo & 1, h_rdy=vo >> 1)
        await b.step()
    missing = [(j, c, v, o) for j in range(16) for c in range(128) for v in (0, 1) for o in (0, 1)
               if (j, c, v, o) not in b.cov]
    assert not missing, "uncovered (pad, cfg, v, oe): %s" % missing[:8]


@cocotb.test()
async def link_pads_and_shared_owner(dut):
    b = Bench(dut)
    await b.start()
    # PAD[4], PAD[5] writes ignored: H_TX / H_RDY stay on uo_out[4], [5]
    for c in (EN, EN | INV, EN | 0x0F):
        await b.step(pw=(4, c))
        await b.step(pw=(5, c))
        for t in range(4):
            await b.step(val=0xFFFF * (t & 1), oe=0xFFFF, h_tx=t & 1, h_rdy=t >> 1)
        await b.step(h_tx=0, h_rdy=1, val=0xFFFF)
        await b.step()
        assert b.rd("uo_out") >> 4 & 3 == 0b10
    # every pad owned by E2.p1, alternating inv, uio pads alternating od
    for j in range(16):
        await b.step(pw=(j, EN | (j & 1) * INV | (j >> 1 & 1) * OD | 2 << 2 | 1))
    for t in range(8):
        await b.step(val=(t & 1) << 9, oe=(t >> 1 & 1) << 9)


@cocotb.test()
async def reset_mid_operation(dut):
    b = Bench(dut)
    await b.start()
    rng = random.Random(SEED + 7)
    for _ in range(3):
        for j in range(16):
            await b.step(pw=(j, EN | rng.getrandbits(6)), filt=0xFFFF)
        for _ in range(20):
            await b.step(val=rng.getrandbits(16), oe=rng.getrandbits(16), ui=rng.getrandbits(8),
                         uio=rng.getrandbits(8), filt=0xFFFF)
        await b.step(rst_n=0, pw=(8, EN), val=0xFFFF, oe=0xFFFF, filt=0xFFFF)
        for _ in range(4):
            await b.step(val=0xFFFF, oe=0xFFFF, filt=0xFFFF)
        assert b.rd("uio_oe") == 0 and b.rd("uio_out") == 0 and b.rd("uo_out") == 0x10


@cocotb.test()
async def random_run(dut):
    b = Bench(dut)
    await b.start()
    rng = random.Random(SEED)
    filt = 0
    for n in range(NCLK):
        if rng.random() < 0.01:
            filt = rng.getrandbits(16)
        pw = (rng.getrandbits(4), rng.getrandbits(7)) if rng.random() < 0.1 else None
        ui = rng.getrandbits(8) if rng.random() < 0.3 else b.ff1 & 0xFF
        uio = rng.getrandbits(8) if rng.random() < 0.3 else b.ff1 >> 8
        await b.step(ui=ui, uio=uio, filt=filt, val=rng.getrandbits(16), oe=rng.getrandbits(16), pw=pw,
                     h_tx=rng.getrandbits(1), h_rdy=rng.getrandbits(1), rst_n=int(rng.random() > 0.002))
    modes = {(j >= 8, c >> 5 & 3, v, o) for (j, c, v, o) in b.cov}
    assert len(modes) == 32, "pad modes covered: %d of 32" % len(modes)
