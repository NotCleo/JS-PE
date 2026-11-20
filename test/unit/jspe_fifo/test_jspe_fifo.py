# jspe_fifo block test (VERIF_PLAN 5): both widths every clock against the ISS Fifo (sw/jspe_iss.py).
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

from jspe_iss import Fifo

WIDTHS = (8, 9)
OUTS = ("rdata", "empty", "full", "free")
SEED = int(os.environ.get("JSPE_SEED", "1"))
NCLK = int(os.environ.get("JSPE_CLK", "20000"))


class Bench:
    def __init__(self, dut):
        self.dut = dut
        self.m = {w: Fifo() for w in WIDTHS}
        self.out = {w: [] for w in WIDTHS}      # words popped from the DUT
        self.cov = set()
        self.clk_n = 0

    def sig(self, name, w):
        return getattr(self.dut, "%s%d" % (name, w))

    def get(self, name, w):
        try:
            return int(self.sig(name, w).value)
        except ValueError:
            raise AssertionError("clk %d: %s%d is %s" % (self.clk_n, name, w, self.sig(name, w).value)) from None

    async def start(self):
        cocotb.start_soon(Clock(self.dut.clk, 20, unit="ns").start())
        for w in WIDTHS:
            for n in ("wr", "rd", "wdata"):
                self.sig(n, w).value = 0
            self.sig("flush", w).value = 1
        await RisingEdge(self.dut.clk)
        await RisingEdge(self.dut.clk)

    # one clock on both FIFOs: wr = None or a 9-bit word (W = 8 gets the low byte)
    async def step(self, wr=None, rd=0, flush=0):
        await FallingEdge(self.dut.clk)
        pre = {w: [str(self.sig(n, w).value) for n in OUTS] for w in WIDTHS}
        for w in WIDTHS:
            self.sig("wr", w).value = int(wr is not None)
            self.sig("wdata", w).value = (wr or 0) & ((1 << w) - 1)
            self.sig("rd", w).value = rd
            self.sig("flush", w).value = flush
        await Timer(1, unit="ns")
        for w in WIDTHS:
            m = self.m[w]
            post = [str(self.sig(n, w).value) for n in OUTS]
            assert post == pre[w], "clk %d W%d: outputs follow the inputs inside a clock" % (self.clk_n, w)
            got = (self.get("empty", w), self.get("full", w), self.get("free", w))
            exp = (int(m.empty()), int(m.full()), m.free())
            assert got == exp, "clk %d W%d: empty/full/free %s, expected %s" % (self.clk_n, w, got, exp)
            if not m.empty():
                assert self.get("rdata", w) == m.head(), "clk %d W%d: head %x, expected %x" % (
                    self.clk_n, w, self.get("rdata", w), m.head())
                if rd and not flush:
                    self.out[w].append(self.get("rdata", w))
            if w == 8:
                self.cov.add((len(m.q), int(wr is not None), rd, flush))
        await RisingEdge(self.dut.clk)
        for w in WIDTHS:
            self.m[w].commit(None if wr is None else wr & ((1 << w) - 1), bool(rd), bool(flush))
        self.clk_n += 1

    # model-independent level check, right after the clock edge of the last step
    async def level(self, n):
        await Timer(1, unit="ns")
        for w in WIDTHS:
            got = (self.get("empty", w), self.get("full", w), self.get("free", w))
            assert got == (int(n == 0), int(n == 4), 4 - n), "clk %d W%d: level %d, got %s" % (self.clk_n, w, n, got)

    def popped(self):
        o = self.out
        self.out = {w: [] for w in WIDTHS}
        return o

    async def drain(self):
        for _ in range(4):
            await self.step(rd=1)
        return self.popped()


def both(seq):
    return {8: [v & 0xFF for v in seq], 9: list(seq)}


@cocotb.test()
async def fill_drain(dut):
    b = Bench(dut)
    await b.start()
    seq = [0x1A5, 0x05A, 0x1FF, 0x100]
    for k, v in enumerate(seq):
        await b.level(k)
        await b.step(wr=v)
    await b.level(4)
    for k in range(4):
        await b.step(rd=1)
        await b.level(3 - k)
    assert b.popped() == both(seq)


@cocotb.test()
async def limits_ignored(dut):
    b = Bench(dut)
    await b.start()
    for _ in range(3):
        await b.step(rd=1)
        await b.level(0)
    seq = [0x111, 0x022, 0x133, 0x044]
    for v in seq:
        await b.step(wr=v)
    for v in (0x1EE, 0x0DD, 0x1CC):
        await b.step(wr=v)
        await b.level(4)
    assert await b.drain() == both(seq)
    await b.level(0)
    await b.step(rd=1)
    await b.step(wr=0x155)
    await b.level(1)
    assert (await b.drain()) == both([0x155])


@cocotb.test()
async def write_and_pop_in_one_clock(dut):
    b = Bench(dut)
    await b.start()
    # empty: the write is taken, the pop ignored
    await b.step(wr=0x101, rd=1)
    await b.level(1)
    assert b.popped() == both([])
    # 1, 2, 3 entries: both honoured, level unchanged
    nxt = 0x102
    exp = [0x101]
    for n in (1, 2, 3):
        for _ in range(3):
            await b.step(wr=nxt, rd=1)
            await b.level(n)
            exp.append(nxt)
            nxt += 0x11
        await b.step(wr=nxt)
        exp.append(nxt)
        nxt += 0x11
    await b.level(4)
    # full: the pop is taken, the write dropped (D046)
    await b.step(wr=0x1EE, rd=1)
    await b.level(3)
    got = b.popped()
    rest = await b.drain()
    assert {w: got[w] + rest[w] for w in WIDTHS} == both(exp)


@cocotb.test()
async def flush_every_level(dut):
    b = Bench(dut)
    await b.start()
    for n in range(5):
        for wr, rd in ((None, 0), (0x1F0, 0), (None, 1), (0x1F1, 1)):
            for hold in (1, 2):
                for k in range(n):
                    await b.step(wr=0x80 + 16 * n + k)
                await b.level(n)
                for _ in range(hold):
                    await b.step(wr=wr, rd=rd, flush=1)
                    await b.level(0)
                b.popped()
                await b.step(wr=0x1C3)
                await b.level(1)
                assert await b.drain() == both([0x1C3])


@cocotb.test()
async def reset_mid_operation(dut):
    b = Bench(dut)
    await b.start()
    for at in range(3, 12):
        await b.step(wr=0x100)
        await b.step(wr=0x101)
        b.popped()
        sent = []
        for k in range(20):
            if k == at:
                await b.step(wr=0x1AA, rd=1, flush=1)
                await b.level(0)
                b.popped()
                sent = []
                continue
            v = (at * 32 + k) & 0x1FF
            await b.step(wr=v, rd=1)
            sent.append(v)
        got = b.popped()
        rest = await b.drain()
        # after the flush the first clock only writes, then one word out per clock in order
        assert {w: got[w] + rest[w] for w in WIDTHS} == both(sent), at


@cocotb.test()
async def back_to_back(dut):
    b = Bench(dut)
    await b.start()
    rng = random.Random(SEED)
    for n in (1, 2, 3):
        seq = [rng.getrandbits(9) for _ in range(n + 64)]
        for v in seq[:n]:
            await b.step(wr=v)
        for v in seq[n:]:
            await b.step(wr=v, rd=1)
            await b.level(n)
        got = b.popped()
        rest = await b.drain()
        assert {w: got[w] + rest[w] for w in WIDTHS} == both(seq)
    seq = [rng.getrandbits(9) for _ in range(6)]
    for k, v in enumerate(seq):
        await b.step(wr=v)
        await b.level(min(k + 1, 4))
    for k in range(6):
        await b.step(rd=1)
        await b.level(max(3 - k, 0))
    assert b.popped() == both(seq[:4])


@cocotb.test()
async def width_bits(dut):
    b = Bench(dut)
    await b.start()
    pats = [1 << k for k in range(9)] + [0x1FF ^ (1 << k) for k in range(9)] + [0x000, 0x1FF, 0x0FF, 0x100]
    for off in range(4):
        seq = pats[off:] + pats[:off]
        for i in range(0, len(seq) - 3, 4):
            for v in seq[i:i + 4]:
                await b.step(wr=v)
            got = await b.drain()
            assert got == both(seq[i:i + 4])
            assert all(x >> 8 == y >> 8 for x, y in zip(got[9], seq[i:i + 4]))


@cocotb.test()
async def random_traffic(dut):
    b = Bench(dut)
    await b.start()
    rng = random.Random(SEED + 1)
    pw = pr = 0.5
    for k in range(NCLK):
        if k % 200 == 0:
            pw, pr = rng.choice(((0.5, 0.5), (0.9, 0.2), (0.2, 0.9), (0.95, 0.95), (0.6, 0.4), (0.4, 0.6)))
        await b.step(wr=rng.getrandbits(9) if rng.random() < pw else None, rd=int(rng.random() < pr),
                     flush=int(rng.random() < 0.02))
    want = {(n, w, r, f) for n in range(5) for w in (0, 1) for r in (0, 1) for f in (0, 1)}
    assert want <= b.cov, sorted(want - b.cov)
