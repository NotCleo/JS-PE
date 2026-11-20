# jspe_lfsr block test (VERIF_PLAN 5): every clock against Chip.lfsr_commit of the ISS (sw/jspe_iss.py).
import os
import random
from types import SimpleNamespace

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

from jspe_iss import Chip

LO, HI, PLO, PHI, CFG = range(5)
SEED = int(os.environ.get("JSPE_SEED", "1"))
NCLK = int(os.environ.get("JSPE_CLK", "20000"))
M32 = 0xFFFFFFFF


class Bench:
    def __init__(self, dut):
        self.dut = dut
        self.m = SimpleNamespace(lfsr=0, poly=0, lf_right=0)
        self.cov = set()
        self.clk_n = 0

    async def start(self):
        d = self.dut
        cocotb.start_soon(Clock(d.clk, 20, unit="ns").start())
        for n in ("e_step", "e_bit", "e_we", "e_idx", "e_wdata"):
            getattr(d, n).value = 0
        d.rst_n.value = 0
        await RisingEdge(d.clk)
        await RisingEdge(d.clk)
        # LFSR and POLY are not reset: give them a value
        await self.load(0, 0)

    def get(self, name):
        h = getattr(self.dut, name)
        try:
            return int(h.value)
        except ValueError:
            raise AssertionError("clk %d: %s is %s" % (self.clk_n, name, h.value)) from None

    def check(self):
        got = (self.get("lfsr"), self.get("poly"), self.get("right"))
        exp = (self.m.lfsr, self.m.poly, self.m.lf_right)
        assert got == exp, "clk %d: lfsr/poly/right %08x %08x %d, expected %08x %08x %d" % ((self.clk_n,) + got + exp)

    # one clock. steps = {engine: bit}, writes = {engine: (idx, value)}
    async def step(self, steps=None, writes=None, rst_n=1, check=True):
        d = self.dut
        steps, writes = steps or {}, writes or {}
        await FallingEdge(d.clk)
        pre = str(d.lfsr.value)
        d.rst_n.value = rst_n
        d.e_step.value = sum(1 << e for e in steps)
        d.e_bit.value = sum(b << e for e, b in steps.items())
        d.e_we.value = sum(1 << e for e in writes)
        d.e_idx.value = sum(i << (3 * e) for e, (i, _) in writes.items())
        d.e_wdata.value = sum(v << (16 * e) for e, (_, v) in writes.items())
        await Timer(1, unit="ns")
        assert str(d.lfsr.value) == pre, "clk %d: lfsr follows the inputs inside a clock" % self.clk_n
        if check:
            self.check()
        self.cov.add((bool(writes), bool(steps), self.m.lf_right))
        await RisingEdge(d.clk)
        ops = [(e, "write", 16 + i, v) for e, (i, v) in writes.items() if i <= 4]
        ops += [(e, "step", b) for e, b in steps.items()]
        Chip.lfsr_commit(self.m, ops)
        if not rst_n:
            self.m.lf_right = 0
        self.clk_n += 1

    async def now(self):
        await Timer(1, unit="ns")
        self.check()
        return self.get("lfsr")

    async def load(self, lfsr, poly, right=None, e=0):
        for i, v in ((LO, lfsr & 0xFFFF), (HI, lfsr >> 16), (PLO, poly & 0xFFFF), (PHI, poly >> 16)):
            await self.step(writes={e: (i, v)}, check=False)
        if right is not None:
            await self.step(writes={e: (CFG, right)})


def ref_step(L, P, right, bit):
    # ARCH 2.7, written out independently of the ISS
    if right:
        return (L >> 1) ^ (P if bit ^ (L & 1) else 0)
    return ((L << 1) & M32) ^ (P if bit ^ (L >> 31) else 0)


@cocotb.test()
async def left_and_right_form(dut):
    b = Bench(dut)
    await b.start()
    rng = random.Random(SEED)
    for right in (0, 1):
        for L0, P in ((0x80000000, 0x04C11DB7), (0x00000001, 0xEDB88320), (0, 0), (M32, M32), (0x12345678, 1),
                      (0x80000001, 0x80000000), (rng.getrandbits(32), rng.getrandbits(32))):
            await b.load(L0, P, right)
            L = L0
            for k in range(80):
                bit = rng.getrandbits(1)
                await b.step(steps={k & 3: bit})
                L = ref_step(L, P, right, bit)
                assert await b.now() == L, (right, hex(L0), hex(P), k)


@cocotb.test()
async def crc_check_values(dut):
    b = Bench(dut)
    await b.start()
    msg = b"123456789"
    for poly, init, width, right, xorout, exp in ((0x07, 0, 8, 0, 0, 0xF4), (0x4599, 0, 15, 0, 0, 0x059E),
                                                  (0x1021, 0xFFFF, 16, 0, 0, 0x29B1),
                                                  (0xEDB88320, M32, 32, 1, M32, 0xCBF43926)):
        sh = 0 if right else 32 - width
        await b.load(init << sh, poly << sh, right, e=width & 3)
        for byte in msg:
            for k in range(8):
                await b.step(steps={k & 3: byte >> (k if right else 7 - k) & 1})
        v = await b.now() >> sh
        assert (v ^ xorout) & ((1 << width) - 1) == exp, (width, hex(v))


@cocotb.test()
async def halves_written_separately(dut):
    b = Bench(dut)
    await b.start()
    await b.load(0x11112222, 0x33334444, 0)
    exp = [0x2222, 0x1111, 0x4444, 0x3333]
    for e in range(4):
        for i, v in ((HI, 0xA5A5), (PLO, 0x0F0F), (LO, 0x5A5A), (PHI, 0xF0F0), (LO, 0xFFFF), (PHI, 0x0000),
                     (HI, 0x0001), (PLO, 0x8000)):
            await b.step(writes={e: (i, v ^ e)})
            exp[i] = v ^ e
            await b.now()
            assert (b.get("lfsr"), b.get("poly")) == (exp[1] << 16 | exp[0], exp[3] << 16 | exp[2])
    # LFCFG touches only the form bit and takes bit 0 of the word
    for v, r in ((0x0001, 1), (0xFFFE, 0), (0xFFFF, 1), (0x0000, 0)):
        before = (b.get("lfsr"), b.get("poly"))
        await b.step(writes={2: (CFG, v)})
        await b.now()
        assert (b.get("lfsr"), b.get("poly"), b.get("right")) == before + (r,)
    # an index that is not a shared register writes nothing
    for i in (5, 6, 7):
        before = (b.get("lfsr"), b.get("poly"), b.get("right"))
        await b.step(writes={1: (i, 0xFFFF)}, check=False)
        await Timer(1, unit="ns")
        assert (b.get("lfsr"), b.get("poly"), b.get("right")) == before


@cocotb.test()
async def write_beats_step(dut):
    b = Bench(dut)
    await b.start()
    for right in (0, 1):
        for we in range(4):
            for i, v in ((LO, 0xBEEF), (HI, 0xDEAD), (PLO, 0x1DB7), (PHI, 0x04C1), (CFG, right)):
                for stepmask in range(1, 16):
                    await b.load(0x80000001, 0xFFFFFFFF, right)
                    before = [b.get("lfsr") & 0xFFFF, b.get("lfsr") >> 16, b.get("poly") & 0xFFFF,
                              b.get("poly") >> 16]
                    await b.step(steps={e: 1 for e in range(4) if stepmask >> e & 1}, writes={we: (i, v)})
                    if i != CFG:
                        before[i] = v
                    await b.now()
                    # the write is done and nothing has shifted, whoever steps
                    assert (b.get("lfsr"), b.get("poly")) == (before[1] << 16 | before[0],
                                                              before[3] << 16 | before[2])


@cocotb.test()
async def lowest_engine_wins(dut):
    b = Bench(dut)
    await b.start()
    data = (0x1111, 0x2222, 0x4444, 0x8888)
    for mask in range(1, 16):
        low = min(e for e in range(4) if mask >> e & 1)
        # writes to the same register and to different registers
        for idxs in ((LO, LO, LO, LO), (HI, PLO, PHI, LO), (PHI, HI, LO, PLO), (PLO, PHI, HI, CFG)):
            await b.load(0, 0, 0)
            await b.step(writes={e: (idxs[e], data[e]) for e in range(4) if mask >> e & 1})
            await b.now()
            exp = [0, 0, 0, 0]
            if idxs[low] != CFG:
                exp[idxs[low]] = data[low]
            assert (b.get("lfsr"), b.get("poly")) == (exp[1] << 16 | exp[0], exp[3] << 16 | exp[2]), (mask, idxs)
        # steps: only the lowest engine's bit counts, one step however many ask
        for right in (0, 1):
            for bits in range(16):
                await b.load(0x40000002, 0x04C11DB7, right)
                await b.step(steps={e: bits >> e & 1 for e in range(4) if mask >> e & 1})
                assert await b.now() == ref_step(0x40000002, 0x04C11DB7, right, bits >> low & 1), (mask, bits)


@cocotb.test()
async def reset_mid_operation(dut):
    b = Bench(dut)
    await b.start()
    for hold in (1, 2):
        await b.load(0xCAFEF00D, 0xEDB88320, 1)
        for k in range(10):
            await b.step(steps={0: k & 1})
        # reset during a run of steps, with a write of the form bit pending: reset wins on `right`
        for _ in range(hold):
            await b.step(steps={1: 1}, writes={0: (CFG, 1)}, rst_n=0, check=False)
        await Timer(1, unit="ns")
        assert b.get("right") == 0
        # LFSR and POLY are not reset (ARCH 9.1): the program writes them again, then the left form runs
        await b.load(0x80000000, 0x04C11DB7)
        await b.step(steps={3: 0})
        assert await b.now() == 0x04C11DB7


@cocotb.test()
async def back_to_back(dut):
    b = Bench(dut)
    await b.start()
    rng = random.Random(SEED + 1)
    await b.load(M32, 0x04C11DB7, 0)
    L = M32
    for k in range(256):
        bit = rng.getrandbits(1)
        await b.step(steps={rng.randrange(4): bit})
        L = ref_step(L, 0x04C11DB7, 0, bit)
    assert await b.now() == L
    # write, step, write, step in consecutive clocks
    await b.step(writes={0: (LO, 0x0001)})
    await b.step(steps={1: 0})
    await b.step(writes={2: (HI, 0x8000)})
    await b.step(steps={3: 0})
    await b.step(writes={1: (CFG, 1)})
    await b.step(steps={0: 1})
    await b.now()
    # idle clocks hold everything
    before = (b.get("lfsr"), b.get("poly"), b.get("right"))
    for _ in range(4):
        await b.step()
    await b.now()
    assert (b.get("lfsr"), b.get("poly"), b.get("right")) == before


@cocotb.test()
async def random_traffic(dut):
    b = Bench(dut)
    await b.start()
    rng = random.Random(SEED + 2)
    for k in range(NCLK):
        ps, pw = ((0.6, 0.02), (0.2, 0.2), (0.9, 0.0), (0.05, 0.5))[k // 500 % 4]
        steps = {e: rng.getrandbits(1) for e in range(4) if rng.random() < ps}
        writes = {e: (rng.randrange(5), rng.getrandbits(16)) for e in range(4) if rng.random() < pw}
        rst_n = int(rng.random() >= 0.002)
        await b.step(steps=steps, writes=writes, rst_n=rst_n)
    want = {(w, s, r) for w in (False, True) for s in (False, True) for r in (0, 1)}
    assert want <= b.cov, sorted(want - b.cov)
