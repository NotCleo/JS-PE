# SPDX-FileCopyrightText: © 2026 NotCleo
# SPDX-License-Identifier: Apache-2.0
# Top level: host link load/read-back, host registers, real programs loaded over the link vs the ISS.

import os
import random
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

ESC = 0x1B
ID = 0x4A10
MEM_WORDS = int(os.environ.get("JSPE_MEM_WORDS", "512"))  # 128 with the flop fallback (JSPE_MEM=flop)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROG = os.path.join(ROOT, "docs", "spec", "programs")
sys.path.insert(0, os.path.join(ROOT, "sw"))


class Host:
    def __init__(self, dut):
        self.dut = dut
        self.ui = 0
        self.B = 16
        self.rx = []
        self.set_ui(3, 1)

    def set_ui(self, bit, v):
        self.ui = (self.ui & ~(1 << bit)) | (v << bit)
        self.dut.ui_in.value = self.ui

    async def send(self, data):
        for b in data:
            for v in [0] + [(b >> k) & 1 for k in range(8)] + [1]:
                self.set_ui(3, v)
                await ClockCycles(self.dut.clk, self.B)

    def h_tx(self):
        return (int(self.dut.uo_out.value) >> 4) & 1

    async def monitor(self):
        while True:
            await FallingEdge(self.dut.clk)
            if self.h_tx():
                continue
            await ClockCycles(self.dut.clk, self.B // 2, rising=False)
            if self.h_tx():
                continue
            b = 0
            for k in range(8):
                await ClockCycles(self.dut.clk, self.B, rising=False)
                b |= self.h_tx() << k
            await ClockCycles(self.dut.clk, self.B, rising=False)
            assert self.h_tx() == 1, "H_TX stop bit low"
            self.rx.append(b)

    async def expect(self, n, timeout=None):
        t = timeout or (n + 4) * 12 * self.B + 200
        for _ in range(t):
            if len(self.rx) >= n:
                break
            await RisingEdge(self.dut.clk)
        got, self.rx = self.rx[:n], self.rx[n:]
        assert len(got) == n, f"expected {n} bytes, got {got}"
        return got

    async def sync(self):
        await self.send([ESC, 0x74])
        assert await self.expect(4) == [ESC, 0xC4, ID & 0xFF, ID >> 8]

    async def wrmem(self, addr, words):
        await self.send([ESC, 0x70, addr & 0xFF, addr >> 8, len(words) - 1])
        await self.send([x for w in words for x in (w & 0xFF, w >> 8)])

    async def rdmem(self, addr, n):
        await self.send([ESC, 0x71, addr & 0xFF, addr >> 8, n - 1])
        r = await self.expect(2 + 2 * n)
        assert r[:2] == [ESC, 0xC1], r[:2]
        return [r[2 + 2 * k] | r[3 + 2 * k] << 8 for k in range(n)]

    async def rdreg(self, reg):
        await self.send([ESC, 0x73, reg])
        r = await self.expect(4)
        assert r[:2] == [ESC, 0xC3], r
        return r[2] | r[3] << 8

    async def wrreg(self, reg, v):
        await self.send([ESC, 0x72, reg, v & 0xFF, v >> 8])


async def start(dut):
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    h = Host(dut)
    dut.ena.value = 1
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    cocotb.start_soon(h.monitor())
    return h


@cocotb.test()
async def test_reset_and_sync(dut):
    h = await start(dut)
    uo = int(dut.uo_out.value)
    assert (uo >> 4) & 1 == 1 and (uo >> 5) & 1 == 0, f"uo_out {uo:#04x}"
    assert int(dut.uio_oe.value) == 0
    await h.sync()
    assert await h.rdreg(0x00) == ID
    assert await h.rdreg(0x07) == 0


@cocotb.test()
async def test_load_readback(dut):
    h = await start(dut)
    n = MEM_WORDS
    rng = random.Random(5)
    img = [rng.randrange(1 << 16) for _ in range(n)]
    img[3] = 0x1B1B
    for a in range(0, n, 256):
        await h.wrmem(a, img[a:a + 256])
    for a in range(0, n, 256):
        assert await h.rdmem(a, min(256, n - a)) == img[a:a + 256]
    # 9-bit addresses wrap at 512 and alias modulo the memory size
    await h.wrmem(510, [0x1111, 0x2222, 0x3333])
    img[510 % n], img[511 % n], img[0] = 0x1111, 0x2222, 0x3333
    assert await h.rdmem(510, 3) == [0x1111, 0x2222, 0x3333]
    assert await h.rdmem(0, 3) == img[0:3]
    if n < 512:
        assert await h.rdmem(n + 1, 2) == img[1:3]


@cocotb.test()
async def test_link_rate_and_pins(dut):
    h = await start(dut)
    dut.uio_in.value = 0xA5
    h.set_ui(0, 1)
    await ClockCycles(dut.clk, 4)
    assert await h.rdreg(0x05) == 0xA5 << 8 | (h.ui & 0xFF)
    for rate, B in ((2, 4), (1, 8), (0, 16)):
        await h.wrreg(0x01, rate)
        await ClockCycles(dut.clk, 2 * h.B)
        h.B = B
        await h.sync()
        assert await h.rdreg(0x01) == rate
        await h.wrmem(100, [0xBEEF ^ rate])
        assert await h.rdmem(100, 1) == [0xBEEF ^ rate]


@cocotb.test()
async def test_errors(dut):
    h = await start(dut)
    await h.send([ESC, 0x7F])          # unknown op
    assert await h.expect(2) == [ESC, 0x8C]
    await h.send([0x55])               # data byte, no stream selected
    assert await h.expect(2) == [ESC, 0x8C]
    await h.send([ESC, 0x62, 0x00, 0x00])  # EXEC with its two argument bytes
    await h.send([ESC, 0x00, 0x55])    # SEL 0, then data: dropped silently
    await h.sync()
    # break abandons a command in progress
    await h.send([ESC, 0x70, 0x00])
    h.set_ui(3, 0)
    await ClockCycles(dut.clk, 12 * h.B)
    h.set_ui(3, 1)
    assert await h.expect(2) == [ESC, 0x8C]
    await ClockCycles(dut.clk, 2 * h.B)
    await h.sync()


@cocotb.test()
async def test_run_after_reset_word0_once(dut):
    # D109: RUN with no EXEC, after chip reset and after RST, runs word 0 exactly once
    from jspe_asm import encode
    h = await start(dut)
    await h.wrmem(0, [encode("evt", c=5), encode("halt")])
    for rst in (False, True):
        if rst:
            await h.send([ESC, 0x51])          # RST e0: PC = 0, FP = 1, FIFOs flushed
        await h.send([ESC, 0x31])              # RUN e0, no EXEC before it
        await ClockCycles(dut.clk, 80 * h.B)
        toks, h.rx = decode_stream(h.rx), []
        assert toks.count(("esc", 0x45)) == 1, toks
        assert ("esc", 0x84) in toks, toks
        est = await h.rdreg(0x08)
        assert est & 0x1FF == 2 and (est >> 13) & 1 == 1, f"EST0 {est:#06x}"


def decode_stream(got):
    out, cur, k = [], None, 0
    while k < len(got):
        b = got[k]
        if b == ESC:
            if k + 1 >= len(got):
                break
            x = got[k + 1]
            k += 2
            if x <= 3:
                cur = x
            elif x == ESC:
                out.append((cur, ESC))
            else:
                out.append(("esc", x))
        else:
            out.append((cur, b))
            k += 1
    return out


@cocotb.test()
async def test_uart_loopback_vs_iss(dut):
    # uart_tx.s on E0 (uo_out[0]) looped to uart_rx.s on E1 (ui_in[7]); loaded over the link; pads == ISS every clock
    from jspe_asm import assemble
    from jspe_iss import Chip, HostLink, play_bin
    img = assemble([os.path.join(PROG, "uart_tx.s"), os.path.join(PROG, "uart_rx.s")])
    chip = Chip(direct=False)
    hl = HostLink(16)
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    dut.ena.value = 1
    dut.ui_in.value = 0x08
    dut.uio_in.value = 0xFF
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)  # rst_n 2-FF sync: ISS cycle 0 is the clock after this edge
    chip.ff1 = chip.ff2 = 0x08 | 0xFF << 8  # DUT pin-sync flops are not reset: they hold the idle pads

    async def run(n, until=None):
        for _ in range(n):
            await FallingEdge(dut.clk)
            got = (int(dut.uo_out.value), int(dut.uio_out.value), int(dut.uio_oe.value))
            exp = (chip.uo, chip.uio_out, chip.uio_oe)
            assert got == exp, f"cycle {chip.cycle}: pads {got} != ISS {exp}"
            ui = hl.level(chip) << 3 | (chip.uo & 1) << 7
            uio = (~chip.uio_oe | chip.uio_out) & 0xFF
            dut.ui_in.value = ui
            dut.uio_in.value = uio
            chip.step(ui, uio)
            hl.sample(chip)
            if until and until():
                return
        assert not until, "timeout"

    hl.send(bytes([ESC, 0x74]))
    await run(2000, lambda: len(hl.got) >= 4)
    assert hl.got == [ESC, 0xC4, ID & 0xFF, ID >> 8]
    hl.send(bytes([ESC, 0x72, 0x01, 2, 0]))  # LINK: 4 clk per bit
    await run(2000, hl.idle)
    await run(40)
    hl.B = 4
    for op in play_bin(img.bin_bytes()):
        hl.send(op)
    await run(100000, hl.idle)
    await run(200)
    hl.got = []
    # 5th byte escaped: at B = 4 it reaches the RX FIFO with 1 free entry (D080)
    data = [0x55, 0xA3, 0x00, 0xFF, ESC, 0x0F, 0x0E]
    hl.send(bytes([ESC, 0x00]))
    for b in data:
        hl.send(bytes([ESC, ESC]) if b == ESC else bytes([b]), is_data=True)
    rx = lambda: [b for s, b in decode_stream(hl.got) if s == 1]
    await run(20000, lambda: len(rx()) >= len(data))
    await run(400)
    assert rx() == data, rx()
    assert not [x for x in decode_stream(hl.got) if x[0] == "esc"], hl.got
    for e in (0, 1):
        g = chip.eng[e]
        assert g.run and not (g.ovr or g.trap or g.halted), e


# ---------------------------------------------------------------- random system lockstep (VERIF_PLAN 8: RND1, H2, H4, H5)
class AliasMem(list):
    # ISS memory with the build's aliasing (ARCH 2.3: the flop build ignores addr[8:7])
    def __init__(self, n):
        super().__init__([None] * n)
        self.n = n

    def __getitem__(self, a):
        return list.__getitem__(self, a % self.n)

    def __setitem__(self, a, v):
        list.__setitem__(self, a % self.n, v)


G_W = [62, 4, 3, 3, 3, 2, 2, 1, 3, 3, 2, 2, 3, 3, 2, 2]


def rnd_word(r, undef=False):
    # same mix as test/unit/jspe_engine/iss_trace.py
    from jspe_asm import decode
    while True:
        f = r.choices(range(9), [28, 7, 12, 20, 10, 6, 5, 8, 4])[0]
        g = r.getrandbits
        w = (g(15), 0x8000 | g(14), 0xC000 | g(12), 0xD000 | g(12), 0xE000 | g(12), 0xF000 | g(10), 0xF400 | g(10),
             0xF800 | g(8) << 1, 0xFA00 | g(9))[f]
        if f in (0, 1, 3):
            w = (w & ~15) | r.choices(range(16), G_W)[0]
        if f == 0 and r.random() < 0.6:
            w &= ~0x30
        if undef and r.random() < 0.03:
            w = g(16)
        if decode(w)[0] is not None or undef:
            return w


def rnd_exec(r):
    # a word a host could inject: no relative jump, no call, loop bits clear
    from jspe_asm import decode
    while True:
        w = rnd_word(r)
        i, _ = decode(w)
        if i["format"] not in ("JF", "JP") and i["name"] != "call":
            return w & ~0x30 if i["format"] == "E" else w


def li16(rd, v):
    return [0xE000 | rd << 8 | (v & 255), 0xE400 | rd << 8 | (v >> 8 & 255)]


def init_words(r, e):
    # registers that RTL leaves X and the ISS starts at 0: P, C, T, R0-R2, S; engine 0 also LFSR and POLY
    q = li16(0, r.getrandbits(16)) + [0xD200] + li16(0, r.getrandbits(16)) + [0xD280]
    q += li16(0, r.randrange(1, 40)) + [0xD880, 0xD810]
    if e == 0:
        for c in (16, 17, 18, 19):
            q += li16(0, r.getrandbits(16)) + [0xFA00 | c << 3]
    for rd in range(4):
        q += li16(rd, r.getrandbits(16))
    return q


def config_words(r):
    q = []
    for _ in range(r.choice((1, 2, 3, 4))):
        k = r.randrange(8)
        if k == 0:
            q += li16(0, r.getrandbits(14)) + [0xFA00 | r.choice((3, 4)) << 3]               # BIND
        elif k == 1:
            q += li16(0, r.getrandbits(14)) + [0xFA00 | r.choice((5, 6)) << 3]               # CC
            q += li16(0, r.getrandbits(9)) + [0xFA00 | r.choice((7, 8)) << 3, 0xDA80 | r.getrandbits(2) << 4]
        elif k == 2:
            q += li16(0, r.choice((0, 1, 3, 7, 7, 8, 15)) | r.getrandbits(7) << 4) + [0xFA00]  # SHCFG
        elif k == 3:
            q += li16(0, r.getrandbits(8)) + [0xFA08]                                         # TAPCFG
        elif k == 4:
            q += li16(0, r.getrandbits(8) | r.choice((0, 0, 1, 2)) << 8 | r.getrandbits(5) << 10) + [0xFA10]
        elif k == 5:
            q += [r.choice((0xD800, 0xD810, 0xD830))]
        else:
            q += [rnd_exec(r)]
    return q


async def random_system(dut, seed, nclk, undef):
    dbg = int(os.environ.get("JSPE_SYS_DBG", "0"))
    from jspe_iss import Chip, HostLink
    r = random.Random(seed)
    log = []
    ext = r.getrandbits(16) | 0x08
    ptog = [r.choice((0.1, 0.02, 0.005, 0.0)) for _ in range(16)]
    st = dict(brk=0)
    cov = dict(clk=0, active=0, ovr=0, halted=0, trap=0, xm=0)
    chip = hl = None

    async def chip_reset(n):
        # rst_n low for n >= 3 edges; mid-run, the edge after rst_n falls is still a normal one (2-FF reset sync)
        nonlocal chip, hl
        old = None
        if chip is not None:
            old = chip.mem
            dut.rst_n.value = 0
            await run(1)
        sync = ext | 0x08
        dut.ui_in.value = sync & 0xFF
        dut.uio_in.value = sync >> 8
        dut.rst_n.value = 0
        await ClockCycles(dut.clk, n)
        dut.rst_n.value = 1
        await ClockCycles(dut.clk, 2)
        chip, hl = Chip(direct=False), HostLink(16)
        chip.mem = AliasMem(MEM_WORDS)
        if old is not None:
            for a in range(MEM_WORDS):
                chip.mem[a] = old[a]
        chip.ff1 = chip.ff2 = sync
        chip.fhist = [sync, sync]

    def pads_ok():
        try:
            got = (int(dut.uo_out.value), int(dut.uio_out.value), int(dut.uio_oe.value))
        except ValueError:
            got = (str(dut.uo_out.value), str(dut.uio_out.value), str(dut.uio_oe.value))
        return got, (chip.uo, chip.uio_out, chip.uio_oe)

    async def run(n, until=None, must=True):
        nonlocal ext
        for _ in range(n):
            await FallingEdge(dut.clk)
            got, exp = pads_ok()
            if dbg and dbg - 120 <= chip.cycle <= dbg:
                lk, u = chip.link, dut.user_project.u_link
                print("cyc %d iss: txb=%s cnt=%d outq=%s reply=%s rpend=%d rdata=%d rdq=%d fl=%s nt=%s cur=%s ps=%s htx_n=%d"
                      " | dut: busy=%s n=%s q=%s b2v=%s act=%s have=%s hdr=%s hi=%s left=%s rdfl=%s nt=%s ps=%s htx=%s"
                      % (chip.cycle, lk.tx_bits is not None, lk.tx_cnt, lk.outq, lk.reply[:4], lk.reply_pending,
                         lk.reply_data, len(lk.rd_queue), lk.rd_flight, lk.notices, lk.cur, lk.pstate, lk.h_tx_next,
                         u.tx_busy.value, u.tx_n.value, u.tx_q.value, u.b2v.value, u.rp_act.value, u.rp_have.value,
                         u.rp_hdr.value, u.rp_hi.value, u.rd_left.value, u.rd_fl.value, u.nt.value, u.p_st.value,
                         u.h_tx.value))
            assert got == exp, f"seed {seed} cycle {chip.cycle}: pads {got} != ISS {exp}; last ops {log[-6:]}"
            for k in range(16):
                if r.random() < ptog[k]:
                    ext ^= 1 << k
            if st["brk"]:
                st["brk"] -= 1
                rx = 0 if st["brk"] > 2 * hl.B else 1
            else:
                rx = hl.level(chip)
            ui = (ext & 0xF7) | rx << 3
            uio = ((chip.uio_oe & chip.uio_out) | (~chip.uio_oe & (ext >> 8))) & 0xFF
            dut.ui_in.value = ui
            dut.uio_in.value = uio
            prev = [(g.ovr, g.halted, g.trap) for g in chip.eng]
            chip.step(ui, uio)
            hl.sample(chip)
            cov["clk"] += 1
            for g, (o, h, t) in zip(chip.eng, prev):
                cov["active"] += g.run
                cov["xm"] += g.xm
                cov["ovr"] += g.ovr > o
                cov["halted"] += g.halted > h
                cov["trap"] += g.trap > t
            if until and until():
                return True
        assert not (until and must), f"seed {seed}: timeout, last ops {log[-6:]}"
        return False

    def busy():
        lk = chip.link
        return bool(lk.reply or lk.reply_pending or lk.rd_queue or lk.rd_flight is not None)

    async def send(bs, reply=False):
        log.append((chip.cycle, bytes(bs).hex()))
        hl.send(bytes(bs))
        await run(400000, lambda: hl.idle() and chip.link.rx_state == "idle" and not (reply and busy()))

    async def send_data(unit):
        # a stream whose engine does not pull stays blocked (H_RDY high): the host gives up (HOSTIF 1)
        log.append((chip.cycle, "d" + bytes(unit).hex()))
        hl.send(bytes(unit), is_data=True)
        if await run(64 * 10 * hl.B, hl.idle, must=False):
            return True
        stuck = [b for b, _ in hl.q]
        hl.q.clear()
        if len(unit) == 2 and len(stuck) == 1:
            hl.send(bytes([ESC]))             # second ESC of a literal, sent regardless of H_RDY
        await run(400000, hl.idle)
        return False

    async def exec_all(e, words):
        for w in words:
            await send([ESC, 0x60 | e, w & 0xFF, w >> 8])

    async def setup(load):
        await send([ESC, 0x74], reply=True)
        rate = r.choice((2, 2, 1))
        await send([ESC, 0x72, 0x01, rate, 0])
        await run(400000, lambda: chip.link.tx_bits is None and not chip.link.outq)
        await run(40)
        hl.B = {1: 8, 2: 4}[rate]
        await send([ESC, 0x72, 0x02, r.choice((0xE4, 0xE4, 0x00, 0x44, 0x24, 0x90, r.getrandbits(8))), 0])
        if load:
            img = [rnd_word(r, undef) for _ in range(MEM_WORDS)]
            for a in range(0, MEM_WORDS, 256):
                ch = img[a:a + 256]
                await send([ESC, 0x70, a & 0xFF, a >> 8, len(ch) - 1] + [x for w in ch for x in (w & 0xFF, w >> 8)])
        for e in range(4):
            await exec_all(e, init_words(r, e) + config_words(r) + [0xF400 | r.randrange(MEM_WORDS)])
        await send([ESC, 0x72, 0x04, r.getrandbits(8) if r.random() < 0.3 else 0, 0])
        for _ in range(r.randrange(4, 16)):
            p = r.choice([i for i in range(16) if i not in (4, 5)])
            await send([ESC, 0x72, 0x10 | p, (r.random() < 0.9) << 6 | r.getrandbits(6), 0])
        await send([ESC, r.randrange(4)])
        await send([ESC, 0x30 | r.randrange(1, 16)])

    await chip_reset(10)
    await setup(True)
    end = chip.cycle + nclk
    while chip.cycle < end:
        x = r.random()
        e = r.randrange(4)
        if x < 0.25:
            await send([ESC, e, ESC, e])
            for b in [r.choice((ESC, r.getrandbits(8))) for _ in range(r.randrange(1, 7))]:
                if not await send_data((ESC, ESC) if b == ESC else (b,)):
                    break
        elif x < 0.37:
            await send([ESC, 0x73, r.choice((0, 1, 2, 3, 5, 7, 8, 9, 10, 11, 0x10, r.getrandbits(8)))], reply=True)
        elif x < 0.42:
            await send([ESC, 0x71, r.randrange(512) & 0xFF, r.getrandbits(1), r.randrange(8)], reply=True)
        elif x < 0.45:
            a = r.randrange(512)
            ws = [rnd_word(r, undef) for _ in range(r.randrange(1, 4))]
            await send([ESC, 0x70, a & 0xFF, a >> 8, len(ws) - 1] + [y for w in ws for y in (w & 0xFF, w >> 8)])
        elif x < 0.55:
            reg = r.choice((0x02, 0x03, 0x03, 0x04, 0x06, 0x06, 0x10 | r.randrange(16), 0x10 | r.randrange(16)))
            await send([ESC, 0x72, reg, r.getrandbits(8), 0])
        elif x < 0.70:
            await send([ESC, r.choice((0x20, 0x30, 0x30, 0x40)) | r.randrange(1, 16)])
        elif x < 0.75:
            m = r.randrange(1, 16)
            if r.random() < 0.5:
                # TX FIFO entries held back by a reply, then RST: the flush must drop them (HOSTIF 3)
                k = max(i for i in range(4) if m >> i & 1)
                await send([ESC, 0x71, r.randrange(256), 0, 7])
                await send([ESC, 0x20 | m])
                await exec_all(k, [0xD400, 0xDC00 | r.getrandbits(6) << 4])
            await send([ESC, 0x20 | m, ESC, 0x50 | m])
            await run(400000, lambda: not busy())
            for k in range(4):
                if m >> k & 1 and r.random() < 0.7:
                    await exec_all(k, config_words(r) + [0xF400 | r.randrange(MEM_WORDS)])
            await send([ESC, 0x30 | m])
        elif x < 0.85:
            await exec_all(e, [rnd_exec(r) if r.random() < 0.8 else rnd_word(r, undef)])
        elif x < 0.88:
            await send([ESC, 0x74], reply=True)
        elif x < 0.90:
            st["brk"] = r.choice((11, 15, 25)) * hl.B + 2 * hl.B
            log.append((chip.cycle, "break"))
            await run(st["brk"])
            await send([ESC, 0x74], reply=True)
        elif x < 0.905:
            await run(400000, lambda: chip.link.wr_pending is None)
            await run(r.randrange(1, 30))
            log.append((chip.cycle, "rst_n"))
            await chip_reset(r.randrange(3, 7))
            await setup(r.random() < 0.5)
        else:
            await run(r.randrange(1, 400))
    await run(200)
    return cov


@cocotb.test()
async def test_random_system_vs_iss(dut):
    # random images on four engines, random host traffic and pads, chip reset mid-run; pads == ISS every clock
    # RTL only (VERIF_PLAN 8 RND1 is an R test); JSPE_SYS_SEEDS / JSPE_SYS_CLK / JSPE_SYS_SEED for longer runs
    seeds = int(os.environ.get("JSPE_SYS_SEEDS", "2"))
    nclk = int(os.environ.get("JSPE_SYS_CLK", "30000"))
    base = int(os.environ.get("JSPE_SYS_SEED", "1"))
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    dut.ena.value = 1
    for s in range(base, base + seeds):
        cov = await random_system(dut, s, nclk, undef=bool(s & 1))
        cocotb.log.info("seed %d: %s", s, cov)
