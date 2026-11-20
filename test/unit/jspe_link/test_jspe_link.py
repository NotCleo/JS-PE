# jspe_link block test (VERIF_PLAN 5): every clock against the ISS Link (sw/jspe_iss.py) on a stand-in chip
# (model FIFOs, engines, memory, signal flags); a host UART driver produces H_RX.
import os
import random
from collections import deque
from types import SimpleNamespace

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

from jspe_iss import ESC, Fifo, Link

SEED = int(os.environ.get("JSPE_SEED", "1"))
NCLK = int(os.environ.get("JSPE_CLK", "150000"))
DBG = int(os.environ.get("JSPE_DBG", "-1"))
BT = {0: 16, 1: 8, 2: 4, 3: 16}


class FakeChip:
    def __init__(self, rng):
        self.rx = [Fifo() for _ in range(4)]
        self.tx = [Fifo() for _ in range(4)]
        self.slotmap, self.sig, self.sig_host, self.filt = 0xE4, 0, None, 0
        self.pad_wr, self.lvl_raw, self.snap = [], 0xFFFF, []
        self.eng = [SimpleNamespace(ovr=1, halted=1, trap=1) for _ in range(4)]
        self.rng = rng


class Eng:
    def __init__(self):
        self.run = self.xm = self.ovr = self.halted = self.trap = 0
        self.left = 0
        self.stat = 0


class Bench:
    def __init__(self, dut, seed):
        self.dut = dut
        self.rng = random.Random(seed)
        self.chip = FakeChip(self.rng)
        self.link = Link(self.chip)
        self.eng = [Eng() for _ in range(4)]
        self.mem = [self.rng.getrandbits(16) for _ in range(512)]
        self.rd_data = None
        self.clk_n = 0
        self.exp = dict(h_tx=1, h_rdy=0, pulses={}, slotmap=0xE4, filt=0)
        self.line = deque()
        self.items = deque()
        self.host_rate = 0
        self.gnt_p = 0.6
        self.push_p = 0.02
        self.pop_p = 0.05
        self.cov = set()
        self._wrap()

    def _wrap(self):
        lk, cov = self.link, self.cov
        cmd, err, nseq, parse = lk.command, lk.err, lk.next_sequence, lk.parse

        def command(op, a, env):
            cov.add(("op", op if op >= 0x60 else op & 0xF0))
            if op == 0x72:
                cov.add(("wrreg", a[0]))
            if op == 0x73:
                cov.add(("rdreg", a[0]))
            return cmd(op, a, env)

        def e():
            cov.add(("err",))
            return err()

        def ns(env):
            hdr = lk.reply[0] if lk.reply and len(lk.reply) > lk.reply_data else None
            s = nseq(env)
            if s and hdr in (0xC1, 0xC3, 0xC4):
                cov.add(("tx", hdr))
            elif s and len(s) == 1 and not lk.reply_pending and hdr is None:
                cov.add(("tx", "data"))
            elif s and len(s) == 2:
                c = s[1]
                cov.add(("tx", "lit" if c == ESC else "stream" if c < 4 else c & 0xC0 if c < 0x80 else c & 0xFC))
            return s

        def pa(b, env):
            if lk.pstate == "wrdata" and lk.wr_lo is not None and lk.wr_pending is not None:
                cov.add(("lost",))
            return parse(b, env)
        lk.command, lk.err, lk.next_sequence, lk.parse = command, e, ns, pa

    async def start(self):
        d = self.dut
        cocotb.start_soon(Clock(d.clk, 20, unit="ns").start())
        d.rst_n.value = 0
        self.drive(1, 0)
        for _ in range(3):
            await RisingEdge(d.clk)
        d.rst_n.value = 1

    # ---------------------------------------------------------------- host side
    def reply_busy(self):
        lk = self.link
        return bool(lk.reply or lk.reply_pending or lk.rd_queue or lk.rd_flight is not None or lk.outq)

    def next_level(self):
        while not self.line:
            if not self.items:
                return 1
            it = self.items.popleft()
            B = BT[self.host_rate]
            if it[0] == "byte":
                v, stop = it[1], it[2] if len(it) > 2 else 1
                bits = [0] + [v >> i & 1 for i in range(8)] + [stop]
                for x in bits:
                    self.line.extend([x] * B)
                if not stop:
                    self.line.extend([1] * B)
            elif it[0] == "idle":
                self.line.extend([1] * it[1])
            elif it[0] == "break":
                self.line.extend([0] * it[1] + [1] * B)
            elif it[0] == "glitch":
                self.line.extend([0] * it[1] + [1] * B)
            elif it[0] == "rate":
                self.host_rate = it[1]
            elif it[0] == "wait":
                if self.reply_busy() or self.link.rx_state != "idle":
                    self.items.appendleft(it)
                    return 1
        return self.line.popleft()

    def send(self, bs):
        for b in bs:
            self.items.append(("byte", b))
        if self.rng.random() < 0.3:
            self.items.append(("idle", self.rng.randrange(1, 40)))

    def cmd_sel(self, e):
        self.send([ESC, e])

    def cmd_data(self, data):
        out = []
        for x in data:
            out += [ESC, ESC] if x == ESC else [x]
        self.send(out)

    def cmd_reply(self, bs):
        self.items.append(("wait",))
        self.send(bs)

    def cmd_wrreg(self, r, v):
        self.send([ESC, 0x72, r, v & 0xFF, v >> 8])
        if r == 0x01:
            self.items.append(("wait",))
            self.items.append(("rate", v & 3))

    def cmd_wrmem(self, addr, words):
        bs = [ESC, 0x70, addr & 0xFF, addr >> 8, len(words) - 1]
        for w in words:
            bs += [w & 0xFF, w >> 8]
        self.send(bs)

    # ---------------------------------------------------------------- one clock
    def drive(self, rxl, gnt):
        d = self.dut
        d.h_rx_s.value = rxl
        d.h_gnt.value = gnt

    def snap(self):
        return [SimpleNamespace(run=g.run, xm=g.xm, ovr=g.ovr, halted=g.halted, trap=g.trap,
                                est=(g.stat & 0x1FF) | g.run << 9 | (g.stat & 0x400) | g.ovr << 11 | g.trap << 12 |
                                g.halted << 13 | int(not self.chip.rx[e].empty()) << 14 |
                                int(not self.chip.tx[e].empty()) << 15)
                for e, g in enumerate(self.eng)]

    def rd(self, n):
        try:
            return int(getattr(self.dut, n).value)
        except ValueError:
            raise AssertionError("clk %d: %s is %s" % (self.clk_n, n, getattr(self.dut, n).value)) from None

    def check(self, name, exp):
        got = self.rd(name)
        assert got == exp, "clk %d: %s = %x, expected %x" % (self.clk_n, name, got, exp)

    async def step(self):
        d, ch, lk, rng = self.dut, self.chip, self.link, self.rng
        await FallingEdge(d.clk)
        rxl = self.next_level()
        lvl = (rng.getrandbits(16) & ~8) | rxl << 3
        snap = self.snap()
        d.h_rx_s.value = rxl
        d.lvl.value = lvl
        d.rx_free.value = sum(ch.rx[e].free() << (3 * e) for e in range(4))
        d.tx_rdata.value = sum(ch.tx[e].head() << (9 * e) for e in range(4))
        d.tx_empty.value = sum(int(ch.tx[e].empty()) << e for e in range(4))
        d.e_stat.value = sum(snap[e].est << (16 * e) for e in range(4))
        d.e_ovr.value = sum(g.ovr << e for e, g in enumerate(self.eng))
        d.e_halt.value = sum(g.halted << e for e, g in enumerate(self.eng))
        d.e_trap.value = sum(g.trap << e for e, g in enumerate(self.eng))
        d.e_xm.value = sum(g.xm << e for e, g in enumerate(self.eng))
        d.sig.value = ch.sig
        d.mem_rdata.value = self.rd_data if self.rd_data is not None else rng.getrandbits(16)
        d.h_gnt.value = 0
        await Timer(1, unit="ns")
        # registered outputs: decided in the previous clock
        x = self.exp
        self.check("h_tx", x["h_tx"])
        self.check("h_rdy", x["h_rdy"])
        for kind in ("run", "stop", "step", "rst", "exec"):
            self.check("e_" + kind, sum(1 << e for e, p in x["pulses"].items() if kind in p))
        ex = [p["exec"] for p in x["pulses"].values() if "exec" in p]
        if ex:
            self.check("exec_word", ex[0])
        self.check("slotmap", x["slotmap"])
        self.check("filt", x["filt"])
        # memory client
        hreq = lk.mem_req()
        self.check("h_req", int(hreq is not None))
        if hreq is not None:
            self.check("h_we", int(hreq[0] == "w"))
            self.check("h_addr", hreq[1])
            if hreq[0] == "w":
                self.check("h_wdata", hreq[2])
        gnt = int(hreq is not None and rng.random() < self.gnt_p)
        d.h_gnt.value = gnt
        await Timer(1, unit="ns")
        # ISS link step on the same inputs
        ch.snap, ch.lvl_raw = snap, lvl
        for g in ch.eng:
            g.ovr = g.halted = g.trap = 1
        ch.sig_host, ch.pad_wr = None, []
        env = SimpleNamespace(lvl_raw=lvl, rx_wr={}, tx_rd={}, mem=self.mem, mem_write_h=None)
        lk.pulses_next = {}
        lk.step(env, bool(gnt), hreq)
        # combinational outputs of this clock
        rx_wr = sum(1 << e for e in env.rx_wr)
        self.check("rx_wr", rx_wr)
        if env.rx_wr:
            self.check("rx_wdata", list(env.rx_wr.values())[0])
        rx_ovf = sum(1 << e for e in env.rx_wr if ch.rx[e].full())
        self.check("rx_ovf", rx_ovf)
        if rx_ovf:
            self.cov.add(("rx_ovf",))
        self.check("tx_rd", sum(1 << e for e in env.tx_rd))
        e_clr = sum((1 - g.ovr) << e | (1 - g.halted) << (4 + e) for e, g in enumerate(ch.eng))
        self.check("e_clr", e_clr)
        self.check("sig_we", int(ch.sig_host is not None))
        if ch.sig_host is not None:
            self.check("sig_wdata", ch.sig_host)
        self.check("pad_we", int(bool(ch.pad_wr)))
        if ch.pad_wr:
            self.check("pad_idx", ch.pad_wr[0][0])
            self.check("pad_wdata", ch.pad_wr[0][1])
        self.exp = dict(h_tx=lk.h_tx_next, h_rdy=lk.h_rdy_next, pulses=lk.pulses_next, slotmap=ch.slotmap,
                        filt=ch.filt)
        if lk.h_rdy_next:
            self.cov.add(("h_rdy",))
        if 0 <= DBG - self.clk_n < 60:
            u = d.u_link if hasattr(d, "u_link") else d
            print("clk %d rx=%s ps=%s reply=%s rp=%d rd=%d q=%d fl=%s nt=%s outq=%s cur=%s sv=%d txb=%s txc=%d gnt=%d"
                  " htx_n=%d | dut busy=%d b2v=%d rp_act=%d rp_have=%d rp_hdr=%d nt=%x" % (
                      self.clk_n, lk.rx_state, lk.pstate, lk.reply[:4], lk.reply_pending, lk.reply_data,
                      len(lk.rd_queue), lk.rd_flight, lk.notices, lk.outq, lk.cur, lk.served,
                      lk.tx_bits is not None, lk.tx_cnt, gnt, lk.h_tx_next, int(d.tx_busy.value), int(d.b2v.value),
                      int(d.rp_act.value), int(d.rp_have.value), int(d.rp_hdr.value), int(d.nt.value)))
        pul = x["pulses"]
        await RisingEdge(d.clk)
        # commit the stand-in chip
        self.rd_data = None
        if hreq is not None and gnt:
            if hreq[0] == "w":
                self.mem[hreq[1]] = hreq[2]
            else:
                self.rd_data = self.mem[hreq[1]]
        if ch.sig_host is not None:
            ch.sig = ch.sig_host
        elif rng.random() < 0.01:
            ch.sig = rng.getrandbits(8)
        for e in range(4):
            g, p = self.eng[e], pul.get(e, {})
            fl = "rst" in p
            pop = not ch.rx[e].empty() and rng.random() < self.pop_p
            ch.rx[e].commit(env.rx_wr.get(e), pop, fl)
            push = None
            if rng.random() < self.push_p and (not ch.tx[e].full() or rng.random() < 0.2):
                r = rng.random()
                push = 0x100 | rng.getrandbits(6) if r < 0.15 else ESC if r < 0.3 else rng.getrandbits(8)
            ch.tx[e].commit(push, e in env.tx_rd, fl)
            self.eng_step(g, p, e_clr >> e & 1, e_clr >> (4 + e) & 1, rx_ovf >> e & 1)
        self.clk_n += 1

    def eng_step(self, g, p, c_ovr, c_ht, ovf):
        rng = self.rng
        if "rst" in p:
            g.run = g.xm = g.ovr = g.halted = g.trap = g.left = 0
            return
        if "stop" in p:
            g.run = g.xm = 0
        if "run" in p:
            g.run, g.halted, g.left = 1, 0, 0
        if "step" in p:
            g.run, g.left = 1, rng.randrange(1, 4)
        if "exec" in p:
            g.xm, g.left = 1, rng.randrange(1, 300)    # an injected guarded instruction may wait long
        if g.left:
            g.left -= 1
            if not g.left:
                if g.xm:
                    g.xm = 0
                else:
                    g.run = 0
        if g.run and rng.random() < 0.002:
            g.run = 0
            if rng.random() < 0.5:
                g.halted = 1
            else:
                g.trap = 1
        if rng.random() < 0.002:
            g.ovr = 1
        if c_ovr:
            g.ovr = 0
        if c_ht:
            g.halted = g.trap = 0
        if ovf:
            g.ovr = 1
        if rng.random() < 0.05:
            g.stat = rng.getrandbits(16)

    async def run_items(self, limit=200000):
        n = 0
        while self.items or self.line or self.reply_busy() or self.link.rx_state != "idle":
            await self.step()
            n += 1
            assert n < limit, "host items not drained"
        for _ in range(40):
            await self.step()


@cocotb.test()
async def sync_regs_and_memory(dut):
    b = Bench(dut, SEED)
    await b.start()
    b.cmd_reply([ESC, 0x74])
    for r in list(range(0x00, 0x0C)) + [0x10, 0x18, 0x20, 0xFF]:
        b.cmd_reply([ESC, 0x73, r])
    b.cmd_wrreg(0x02, 0x1B44)
    b.cmd_wrreg(0x03, 0xA5)
    b.cmd_wrreg(0x04, 0x1234)
    b.cmd_wrreg(0x06, 0xFF)
    for j in range(16):
        b.cmd_wrreg(0x10 + j, 0x40 | j)
    b.cmd_wrreg(0x07, 0xFFFF)
    b.cmd_wrmem(0x1F0, [0x1B1B, 0x0000, 0xFFFF] + [b.rng.getrandbits(16) for _ in range(29)])
    b.cmd_reply([ESC, 0x71, 0xF0, 0x01, 31])
    b.cmd_reply([ESC, 0x71, 0x00, 0x00, 0])
    for r in (1, 2, 3, 0):
        b.cmd_wrreg(0x01, r)
        b.cmd_reply([ESC, 0x74])
        b.cmd_reply([ESC, 0x71, 0x10, 0x00, 3])
    await b.run_items()
    for c in [("op", 0x74), ("op", 0x71), ("op", 0x70), ("tx", 0xC4), ("tx", 0xC3), ("tx", 0xC1)]:
        assert c in b.cov, c


@cocotb.test()
async def streams_and_engine_ops(dut):
    b = Bench(dut, SEED + 1)
    await b.start()
    b.push_p, b.pop_p = 0.05, 0.0
    b.cmd_wrreg(0x01, 2)
    for e in range(4):
        b.cmd_sel(e)
        b.cmd_data([b.rng.getrandbits(8) for _ in range(6)] + [ESC])
    b.cmd_sel(1)
    b.cmd_data([0x55] * 12)                       # overruns RX of engine 1 (pops are rare)
    for kind in (0x20, 0x30, 0x40, 0x50):
        for m in (0x1, 0x6, 0xF):
            b.send([ESC, kind | m])
            b.items.append(("idle", 30))
    for e in range(4):
        b.send([ESC, 0x60 | e, 0x34, 0x12])
        b.send([ESC, 0x60 | e, 0x78, 0x56])       # second one usually refused (xm)
        b.send([ESC, 0x30 | 1 << e])
        b.send([ESC, 0x60 | e, 0x00, 0xF0])       # refused: running
        b.cmd_wrreg(0x02, 0x00)                   # refused: engine running
        b.send([ESC, 0x20 | 1 << e])
    b.items.append(("idle", 3000))
    await b.run_items()
    for c in [("rx_ovf",), ("err",), ("tx", "stream"), ("tx", 0x40), ("tx", "data"), ("h_rdy",), ("op", 0x30),
              ("op", 0x60)]:
        assert c in b.cov, c


@cocotb.test()
async def errors(dut):
    b = Bench(dut, SEED + 2)
    await b.start()
    b.cmd_data([0x11])                            # no stream selected
    for op in (0x04, 0x1A, 0x1C, 0x64, 0x6F, 0x75, 0x80, 0xFF):
        b.send([ESC, op])
    b.items.append(("byte", 0x5A, 0))             # framing error
    b.items.append(("break", 16 * 25))
    b.send([ESC, 0x70, 0x00])                     # command abandoned by the break below
    b.items.append(("break", 16 * 12))
    b.cmd_sel(2)
    for w in (1, 3, 8):
        b.items.append(("glitch", w))
    b.cmd_data([0x22])
    b.items.append(("idle", 50))
    await b.run_items()
    b.gnt_p = 0.0                                 # memory never free: second word loses the first
    b.cmd_wrmem(0x40, [1, 2, 3])
    await b.run_items()
    b.gnt_p = 0.6
    b.items.append(("idle", 200))
    await b.run_items()
    for c in [("err",), ("tx", 0x8C), ("lost",)]:
        assert c in b.cov, c


@cocotb.test()
async def reply_replaced(dut):
    # a reply op while an RDMEM reply is still running replaces it, in RTL and ISS alike (HOSTIF 3, D085)
    b = Bench(dut, SEED + 4)
    await b.start()
    b.cmd_wrreg(0x01, 2)
    for k, d in enumerate(range(0, 700, 9)):
        b.send([ESC, 0x71, b.rng.getrandbits(8), b.rng.getrandbits(1), b.rng.choice((3, 15, 255))])
        b.items.append(("idle", d))
        b.send(([ESC, 0x73, 0x00], [ESC, 0x74], [ESC, 0x71, 0x10, 0x00, 2])[k % 3])
        b.items.append(("wait",))
    await b.run_items()
    for c in [("tx", 0xC1), ("tx", 0xC3), ("tx", 0xC4)]:
        assert c in b.cov, c


@cocotb.test()
async def random_lockstep(dut):
    b = Bench(dut, SEED + 3)
    await b.start()
    rng = b.rng
    while b.clk_n < NCLK:
        if not b.items:
            r = rng.random()
            if r < 0.25:
                b.cmd_data([rng.getrandbits(8) if rng.random() < 0.8 else ESC for _ in range(rng.randrange(1, 13))])
            elif r < 0.33:
                b.cmd_sel(rng.randrange(4))
            elif r < 0.45:
                b.send([ESC, rng.choice((0x20, 0x20, 0x30, 0x30, 0x30, 0x40, 0x40, 0x50)) | rng.getrandbits(4)])
            elif r < 0.52:
                b.send([ESC, 0x60 | rng.randrange(4), rng.getrandbits(8), rng.getrandbits(8)])
            elif r < 0.58:
                b.cmd_wrmem(rng.getrandbits(9), [rng.getrandbits(16) for _ in range(rng.randrange(1, 6))])
            elif r < 0.63:
                b.cmd_reply([ESC, 0x71, rng.getrandbits(8), rng.getrandbits(1), rng.randrange(8)])
            elif r < 0.70:
                b.cmd_reply([ESC, 0x73, rng.choice(list(range(0x0C)) + [0x10, 0x30])])
            elif r < 0.73:
                b.cmd_reply([ESC, 0x74])
            elif r < 0.83:
                reg = rng.choice((0x01, 0x02, 0x03, 0x04, 0x06, 0x10 + rng.randrange(16), 0x09))
                b.cmd_wrreg(reg, rng.getrandbits(16) if reg != 0x01 else rng.randrange(4))
            elif r < 0.86:
                b.send([ESC, rng.choice((0x05, 0x1C, 0x66, 0x7F, 0x90))])
            elif r < 0.88:
                b.items.append(("byte", rng.getrandbits(8), 0))
            elif r < 0.89:
                b.items.append(("break", 16 * 12))
            elif r < 0.91:
                b.items.append(("glitch", rng.randrange(1, 3)))
            else:
                b.items.append(("idle", rng.randrange(1, 400)))
            if rng.random() < 0.03:
                b.gnt_p = rng.choice((0.0, 0.3, 0.6, 1.0))
                b.push_p = rng.choice((0.0, 0.01, 0.05, 0.2))
                b.pop_p = rng.choice((0.0, 0.002, 0.05, 0.5))
        await b.step()
    need = [("op", 0x00), ("op", 0x20), ("op", 0x30), ("op", 0x40), ("op", 0x50), ("op", 0x70), ("op", 0x71),
            ("op", 0x72), ("op", 0x73), ("op", 0x74), ("err",), ("rx_ovf",), ("h_rdy",), ("tx", "stream"),
            ("tx", 0x40), ("tx", 0x80), ("tx", 0x84), ("tx", 0x88), ("tx", 0x8C), ("tx", "lit"), ("tx", 0xC1),
            ("tx", 0xC3), ("tx", 0xC4)]
    missing = [c for c in need if c not in b.cov]
    assert not missing, "not covered: %s" % missing
