# jspe_arb block test (VERIF_PLAN 5): every clock all outputs against the two arbitration expressions of the ISS
# chip step (sw/jspe_iss.py: owner = slotmap >> 2*slot & 3, host only when the owner does not request).
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

MAPS = (0xE4, 0x00, 0x44, 0x24)
HOST = 4
SEED = int(os.environ.get("JSPE_SEED", "1"))
NCLK = int(os.environ.get("JSPE_CLK", "20000"))


def owner(slotmap, slot):
    return (slotmap >> (2 * slot)) & 3


def model(i, slot):
    o = owner(i["slotmap"], slot)
    if i["e_req"] >> o & 1:
        c = o
        mem = (i["e_we"] >> o & 1, i["e_addr"][o], i["e_wdata"][o])
    elif i["h_req"]:
        c = HOST
        mem = (i["h_we"], i["h_addr"], i["h_wdata"])
    else:
        c = None
        mem = (0, 0, 0)
    return c, mem


class Bench:
    def __init__(self, dut):
        self.dut = dut
        self.slot = 0
        self.clk_n = 0
        self.cov = set()
        self.i = dict(rst_n=1, slotmap=0xE4, e_req=0, e_we=0, e_addr=[0] * 4, e_wdata=[0] * 4,
                      h_req=0, h_we=0, h_addr=0, h_wdata=0)

    def drive(self):
        d, i = self.dut, self.i
        for n in ("rst_n", "slotmap", "e_req", "e_we", "h_req", "h_we", "h_addr", "h_wdata"):
            getattr(d, n).value = i[n]
        d.e_addr.value = sum(a << (9 * k) for k, a in enumerate(i["e_addr"]))
        d.e_wdata.value = sum(w << (16 * k) for k, w in enumerate(i["e_wdata"]))

    async def start(self):
        cocotb.start_soon(Clock(self.dut.clk, 20, unit="ns").start())
        await self.reset()

    async def reset(self, n=2):
        self.i["rst_n"] = 0
        for _ in range(n):
            await FallingEdge(self.dut.clk)
            self.drive()
            await RisingEdge(self.dut.clk)
        self.i["rst_n"] = 1
        self.slot = 0

    def get(self, name):
        try:
            return int(getattr(self.dut, name).value)
        except ValueError:
            raise AssertionError("clk %d: %s is %s" % (self.clk_n, name, getattr(self.dut, name).value)) from None

    # one clock: drive, check the combinational outputs, clock edge. Returns the granted client (0-3, HOST, None)
    async def step(self, **kw):
        self.i.update(kw)
        i = self.i
        await FallingEdge(self.dut.clk)
        self.drive()
        await Timer(1, unit="ns")
        c, (we, addr, wdata) = model(i, self.slot)
        exp = dict(slot=self.slot, e_gnt=(1 << c) if c is not None and c != HOST else 0, h_gnt=int(c == HOST),
                   mem_en=int(c is not None), mem_we=we, mem_addr=addr, mem_wdata=wdata)
        got = {n: self.get(n) for n in exp}
        assert got == exp, "clk %d slot %d in %s: got %s, expected %s" % (self.clk_n, self.slot, i, got, exp)
        assert bin(got["e_gnt"]).count("1") + got["h_gnt"] <= 1
        assert got["e_gnt"] & ~i["e_req"] == 0 and got["h_gnt"] <= i["h_req"]
        self.cov.add((self.slot, owner(i["slotmap"], self.slot), c))
        await RisingEdge(self.dut.clk)
        self.slot = (self.slot + 1) & 3 if i["rst_n"] else 0
        self.clk_n += 1
        return c

    async def align(self, phase):
        while self.slot != phase:
            await self.step()

    def rand_data(self, rng):
        self.i.update(e_we=rng.getrandbits(4), e_addr=[rng.getrandbits(9) for _ in range(4)],
                      e_wdata=[rng.getrandbits(16) for _ in range(4)], h_we=rng.getrandbits(1),
                      h_addr=rng.getrandbits(9), h_wdata=rng.getrandbits(16))


@cocotb.test()
async def reset_and_counter(dut):
    b = Bench(dut)
    await b.start()
    seq = [await b.step(e_req=0xF, h_req=1) for _ in range(12)]
    assert seq == [0, 1, 2, 3] * 3, seq
    seq = [await b.step(e_req=0, h_req=0) for _ in range(4)]
    assert seq == [None] * 4, seq


@cocotb.test()
async def maps_all_request_combinations(dut):
    b = Bench(dut)
    await b.start()
    rng = random.Random(SEED)
    for m in MAPS:
        for req in range(32):
            for _ in range(4):
                b.rand_data(rng)
                await b.step(slotmap=m, e_req=req & 0xF, h_req=req >> 4)
    for m in MAPS:
        for s in range(4):
            o = owner(m, s)
            assert {(s, o, o), (s, o, HOST), (s, o, None)} <= b.cov


@cocotb.test()
async def all_clients_at_once(dut):
    b = Bench(dut)
    await b.start()
    rng = random.Random(SEED + 1)
    # grants per engine in 16 clocks = 4 x the slots it owns; the host gets nothing
    exp = {0xE4: [4, 4, 4, 4], 0x00: [16, 0, 0, 0], 0x44: [8, 8, 0, 0], 0x24: [8, 4, 4, 0]}
    for m in MAPS:
        n = [0] * 5
        for _ in range(16):
            b.rand_data(rng)
            n[await b.step(slotmap=m, e_req=0xF, h_req=1)] += 1
        assert n == exp[m] + [0], (hex(m), n)


@cocotb.test()
async def host_only_in_unused_slots(dut):
    b = Bench(dut)
    await b.start()
    rng = random.Random(SEED + 2)
    H = HOST
    # (map, e_req, h_req) -> granted client per slot 0..3
    cases = (
        (0xE4, 0b0010, 1, [H, 1, H, H]),
        (0xE4, 0b0010, 0, [None, 1, None, None]),
        (0xE4, 0b1101, 1, [0, H, 2, 3]),
        (0xE4, 0b0000, 1, [H, H, H, H]),
        (0x24, 0b0101, 1, [0, H, 2, 0]),
        (0x24, 0b1010, 1, [H, 1, H, H]),
        (0x24, 0b1000, 0, [None, None, None, None]),
        (0x44, 0b0001, 1, [0, H, 0, H]),
        (0x44, 0b1110, 0, [None, 1, None, 1]),
        (0x00, 0b1110, 1, [H, H, H, H]),
        (0x00, 0b1111, 1, [0, 0, 0, 0]),
        (0x00, 0b1110, 0, [None, None, None, None]),
    )
    for m, er, hr, exp in cases:
        await b.align(0)
        seq = []
        for _ in range(8):
            b.rand_data(rng)
            seq.append(await b.step(slotmap=m, e_req=er, h_req=hr))
        assert seq == exp * 2, (hex(m), bin(er), hr, seq)
        b.i.update(e_req=0, h_req=0)


@cocotb.test()
async def wait_bound(dut):
    b = Bench(dut)
    await b.start()
    # worst wait of a held request = largest gap between the engine's slots - 1 (ARCH 2.2)
    exp = {0xE4: [3, 3, 3, 3], 0x00: [0], 0x44: [1, 1], 0x24: [2, 3, 3]}
    for m in MAPS:
        for e, bound in enumerate(exp[m]):
            worst = 0
            for ph in range(4):
                b.i.update(slotmap=m, e_req=0, h_req=1)
                await b.align(ph)
                w = 0
                while await b.step(e_req=1 << e) != e:
                    w += 1
                    assert w < 4
                assert await b.step(e_req=0) == HOST
                worst = max(worst, w)
            assert worst == bound, (hex(m), e, worst)
    # the host never waits when no engine requests
    b.i.update(slotmap=0xE4, e_req=0, h_req=0)
    for ph in range(4):
        await b.align(ph)
        assert await b.step(h_req=1) == HOST
        await b.step(h_req=0)


@cocotb.test()
async def mux_walking_bits(dut):
    b = Bench(dut)
    await b.start()
    for c in range(5):
        for inv in (0, 1):
            for bit in range(26):
                v = (1 << bit) ^ (inv * 0x3FFFFFF)
                mine = (v & 1, v >> 1 & 0x1FF, v >> 10 & 0xFFFF)
                rest = (~v & 1, ~v >> 1 & 0x1FF, ~v >> 10 & 0xFFFF)
                sel = [mine if k == c else rest for k in range(5)]
                got = await b.step(slotmap=0x55 * (c & 3), e_req=0 if c == HOST else 0xF, h_req=1,
                                   e_we=sum(sel[k][0] << k for k in range(4)),
                                   e_addr=[sel[k][1] for k in range(4)], e_wdata=[sel[k][2] for k in range(4)],
                                   h_we=sel[4][0], h_addr=sel[4][1], h_wdata=sel[4][2])
                assert got == c
                assert (b.get("mem_we"), b.get("mem_addr"), b.get("mem_wdata")) == mine


@cocotb.test()
async def reset_mid_operation(dut):
    b = Bench(dut)
    await b.start()
    rng = random.Random(SEED + 3)
    for m in (0xE4, 0x24, 0x1B):
        for ph in range(4):
            for n in (1, 2, 3):
                b.i.update(slotmap=m, e_req=0xF, h_req=1)
                await b.align(ph)
                for _ in range(n):
                    b.rand_data(rng)
                    await b.step(rst_n=0)
                seq = [await b.step(rst_n=1) for _ in range(5)]
                assert seq == [owner(m, s & 3) for s in range(5)], (hex(m), ph, n, seq)


@cocotb.test()
async def back_to_back(dut):
    b = Bench(dut)
    await b.start()
    rng = random.Random(SEED + 4)
    H = HOST
    # solo: E0 has every clock
    for k in range(16):
        b.rand_data(rng)
        assert await b.step(slotmap=0x00, e_req=1, h_req=k & 1) == 0
        assert b.get("mem_addr") == b.i["e_addr"][0]
    # hand-over engine -> host -> engine in consecutive clocks
    b.i.update(slotmap=0xE4, e_req=0, h_req=0)
    await b.align(0)
    seq = []
    for er, hr in ((0b0001, 1), (0b0001, 1), (0b0100, 0), (0b0000, 1), (0b0001, 1), (0b0001, 1), (0b1010, 0),
                   (0b1000, 0)):
        b.rand_data(rng)
        seq.append(await b.step(e_req=er, h_req=hr))
    assert seq == [0, H, 2, H, 0, H, None, 3], seq
    # host alone: a different word every clock
    addrs = []
    for k in range(8):
        b.rand_data(rng)
        assert await b.step(e_req=0, h_req=1, h_addr=k * 37 & 0x1FF) == H
        addrs.append(b.get("mem_addr"))
    assert addrs == [k * 37 & 0x1FF for k in range(8)]


@cocotb.test()
async def random_traffic(dut):
    b = Bench(dut)
    await b.start()
    rng = random.Random(SEED + 5)
    dens = 0.5
    for k in range(NCLK):
        if rng.random() < 0.02:
            b.i["slotmap"] = rng.choice(MAPS) if rng.random() < 0.5 else rng.getrandbits(8)
        if k % 500 == 0:
            dens = rng.choice((0.05, 0.3, 0.5, 0.8, 0.97))
        b.rand_data(rng)
        await b.step(e_req=sum((rng.random() < dens) << e for e in range(4)), h_req=int(rng.random() < dens),
                     rst_n=int(rng.random() >= 0.003))
    want = {(s, o, c) for s in range(4) for o in range(4) for c in (o, HOST, None)}
    assert want <= b.cov, sorted(want - b.cov, key=str)
    assert not {x for x in b.cov if x[2] not in (x[1], HOST, None)}
