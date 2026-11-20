# jspe_sig block test (VERIF_PLAN 5): every clock against the flag update of the ISS chip step
# (sw/jspe_iss.py: sig = (sig | set) & ~clr, then a host write replaces it).
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

SEED = int(os.environ.get("JSPE_SEED", "1"))
NCLK = int(os.environ.get("JSPE_CLK", "20000"))


class Bench:
    def __init__(self, dut):
        self.dut = dut
        self.sig = 0
        self.cov = set()
        self.clk_n = 0

    async def start(self):
        d = self.dut
        cocotb.start_soon(Clock(d.clk, 20, unit="ns").start())
        d.rst_n.value = 0
        d.e_set.value = 0
        d.e_clr.value = 0
        d.sig_we.value = 0
        d.sig_wdata.value = 0
        await RisingEdge(d.clk)
        await RisingEdge(d.clk)

    def get(self):
        try:
            return int(self.dut.sig.value)
        except ValueError:
            raise AssertionError("clk %d: sig is %s" % (self.clk_n, self.dut.sig.value)) from None

    # one clock: st, cl = four 8-bit request masks (engine 0..3); host = None or the byte written
    async def step(self, st=(0, 0, 0, 0), cl=(0, 0, 0, 0), host=None, rst_n=1):
        d = self.dut
        await FallingEdge(d.clk)
        pre = self.get()
        d.rst_n.value = rst_n
        d.e_set.value = sum(m << (8 * e) for e, m in enumerate(st))
        d.e_clr.value = sum(m << (8 * e) for e, m in enumerate(cl))
        d.sig_we.value = int(host is not None)
        d.sig_wdata.value = host or 0
        await Timer(1, unit="ns")
        assert self.get() == pre == self.sig, "clk %d: sig %02x (%02x before the inputs), expected %02x" % (
            self.clk_n, self.get(), pre, self.sig)
        s = st[0] | st[1] | st[2] | st[3]
        c = cl[0] | cl[1] | cl[2] | cl[3]
        for n in range(8):
            self.cov.add((self.sig >> n & 1, s >> n & 1, c >> n & 1, int(host is not None), (host or 0) >> n & 1))
        await RisingEdge(d.clk)
        self.sig = (self.sig | s) & ~c & 0xFF
        if host is not None:
            self.sig = host
        if not rst_n:
            self.sig = 0
        self.clk_n += 1

    async def now(self):
        await Timer(1, unit="ns")
        return self.get()


def spread(combo):
    # flag n gets its own 8-bit combination {clr e3..e0, set e3..e0}; each flag sees all 256 as combo counts
    st, cl = [0] * 4, [0] * 4
    for n in range(8):
        k = (combo + 37 * n) & 0xFF
        for e in range(4):
            st[e] |= (k >> e & 1) << n
            cl[e] |= (k >> (4 + e) & 1) << n
    return st, cl


@cocotb.test()
async def reset_value_and_hold(dut):
    b = Bench(dut)
    await b.start()
    for _ in range(4):
        await b.step()
    assert await b.now() == 0
    await b.step(st=(0x01, 0x80, 0, 0))
    for _ in range(4):
        await b.step()
    assert await b.now() == 0x81


@cocotb.test()
async def all_set_clear_combinations(dut):
    b = Bench(dut)
    await b.start()
    for g in (0x00, 0xFF, 0x5A):
        for combo in range(256):
            await b.step(host=g)
            st, cl = spread(combo)
            await b.step(st=st, cl=cl)
            s = st[0] | st[1] | st[2] | st[3]
            c = cl[0] | cl[1] | cl[2] | cl[3]
            # clear wins over set, whoever asks
            assert await b.now() == (g | s) & ~c & 0xFF


@cocotb.test()
async def one_hot_requests_same_flag(dut):
    b = Bench(dut)
    await b.start()
    for n in range(8):
        m = 1 << n
        for setters in range(16):
            for clearers in range(16):
                for g in (0, m):
                    await b.step(host=g ^ 0xFF ^ m if g else 0xFF ^ m)
                    before = b.sig
                    await b.step(st=[m * (setters >> e & 1) for e in range(4)],
                                 cl=[m * (clearers >> e & 1) for e in range(4)])
                    exp = 0 if clearers else (1 if setters else before >> n & 1)
                    got = await b.now()
                    assert got >> n & 1 == exp and got & ~m == before & ~m & 0xFF, (n, setters, clearers, g)


@cocotb.test()
async def host_write_wins(dut):
    b = Bench(dut)
    await b.start()
    for g in (0x00, 0xFF):
        for wd in (0x00, 0xFF, 0xA5, 0x3C):
            for combo in range(256):
                await b.step(host=g)
                st, cl = spread(combo)
                await b.step(st=st, cl=cl, host=wd)
                assert await b.now() == wd


@cocotb.test()
async def reset_mid_operation(dut):
    b = Bench(dut)
    await b.start()
    for hold in (1, 2, 3):
        for host in (None, 0xFF):
            await b.step(host=0xC3)
            await b.step(st=(0x0F, 0, 0, 0))
            for _ in range(hold):
                await b.step(st=(0xFF, 0xFF, 0xFF, 0xFF), host=host, rst_n=0)
                assert await b.now() == 0
            await b.step()
            assert await b.now() == 0
            await b.step(st=(0, 0, 0x10, 0))
            assert await b.now() == 0x10


@cocotb.test()
async def back_to_back(dut):
    b = Bench(dut)
    await b.start()
    # set, clear, set of one flag in consecutive clocks by different engines
    seq = []
    for k in range(16):
        e = k & 3
        st, cl = [0] * 4, [0] * 4
        (st if k % 2 == 0 else cl)[e] = 0x04
        await b.step(st=st, cl=cl)
        seq.append(await b.now())
    assert seq == [0x04, 0x00] * 8
    # host writes in consecutive clocks, then a set and a clear right behind
    for v in (0x01, 0xFE, 0x00, 0xFF, 0x55):
        await b.step(host=v)
        assert await b.now() == v
    await b.step(st=(0, 0x0A, 0, 0))
    assert await b.now() == 0x5F
    await b.step(cl=(0, 0, 0, 0x0F), st=(0x80, 0, 0, 0))
    assert await b.now() == 0xD0


@cocotb.test()
async def random_traffic(dut):
    b = Bench(dut)
    await b.start()
    rng = random.Random(SEED)
    for k in range(NCLK):
        dens = (0.02, 0.1, 0.3)[k // 500 % 3]
        st = [sum((rng.random() < dens) << n for n in range(8)) for _ in range(4)]
        cl = [sum((rng.random() < dens) << n for n in range(8)) for _ in range(4)]
        await b.step(st=st, cl=cl, host=rng.getrandbits(8) if rng.random() < 0.1 else None,
                     rst_n=int(rng.random() >= 0.005))
    want = {(g, s, c, 1, w) for g in (0, 1) for s in (0, 1) for c in (0, 1) for w in (0, 1)}
    want |= {(g, s, c, 0, 0) for g in (0, 1) for s in (0, 1) for c in (0, 1)}
    assert want <= b.cov, sorted(want - b.cov)
