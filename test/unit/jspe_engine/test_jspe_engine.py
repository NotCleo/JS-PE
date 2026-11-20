# jspe_engine lockstep against the ISS (VERIF_PLAN 3, 5): ISS scenarios are recorded per clock and replayed on the DUT.
# JSPE_SEEDS / JSPE_CLK size the random run (VERIF: 200 x 20000); JSPE_FULL=1 replays every recorded trace.
import inspect
import os
import random
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
for p in (HERE, os.path.join(ROOT, "sw"), os.path.join(ROOT, "sw", "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import iss_trace as T                                                       # noqa: E402
from iss_trace import L_STIM, L_EXP, L_STATE, STATE, CARE, get              # noqa: E402
from jspe_asm import decode                                                 # noqa: E402
import test_iss, test_blocks, test_timing, test_first                      # noqa: E402
import test_uart, test_spi, test_i2c, test_notdesigned, test_variants      # noqa: E402

FULL = os.environ.get("JSPE_FULL", "") == "1"
SEEDS = int(os.environ.get("JSPE_SEEDS", "16"))
NCLK = int(os.environ.get("JSPE_CLK", "5000"))
CAP = int(os.environ.get("JSPE_CAP", "60000"))      # clocks replayed per scenario function unless FULL

for _m in (test_iss, test_blocks, test_timing, test_first, test_uart, test_spi, test_i2c, test_notdesigned,
           test_variants):
    if hasattr(_m, "record"):
        _m.record = lambda *a, **k: None

INPUTS = ("run", "stop", "step", "exec", "exec_word", "lvl", "sig", "gnt", "rx_rdata", "rx_empty", "rx_ovf",
          "tx_full", "tx_empty", "lfsr", "clr_ovr", "clr_ht")
OUTS = ("req", "tx_wr", "rx_rd", "val", "oe", "sig_set", "sig_clr", "lf_step", "lf_we", "stat", "ovr", "halted",
        "trap")
COND = (("req", ("we", "addr")), ("wd_v", ("wdata",)), ("tx_wr", ("tx_wdata",)), ("lf_step", ("lf_bit",)),
        ("lf_we", ("lf_idx", "lf_wdata")))


class Bench:
    def __init__(self, dut):
        self.dut = dut
        self.h = {n: getattr(dut, n) for n, _ in T.STIM if n != "mem_v"}
        self.h.update({n: getattr(dut, n) for n, _ in T.EXP if n != "wd_v"})
        self.st = []
        for path, _ in STATE:
            a, b = path.split(".")
            self.st.append(getattr(getattr(dut, a), b))
        self.last = {}
        self.rnd = random.Random(1)
        self.clocks = 0

    def drive(self, name, v):
        if self.last.get(name) != v:
            self.last[name] = v
            self.h[name].value = v

    async def start(self):
        cocotb.start_soon(Clock(self.dut.clk, 20, unit="ns").start())
        for n in INPUTS + ("mem_rdata",):
            self.drive(n, 0)
        self.drive("rst", 1)
        await RisingEdge(self.dut.clk)
        await RisingEdge(self.dut.clk)

    def read(self, h, what, tr, k):
        try:
            return int(h.value)
        except ValueError:
            raise AssertionError("%s clk %d: %s is %s" % (tr.label, k, what, h.value)) from None

    def context(self, tr, k):
        out = []
        for j in range(max(0, k - 5), k + 1):
            s, _, st, dep = tr.recs[j]
            g = lambda n: get(L_STATE, st, "u_seq." + n)
            out.append("  clk %d: pc=%03x irv=%d ir=%04x pfv=%d fp=%03x run=%d xm=%d mo=%d fl=%d | gnt=%d rst=%d "
                       "run/stop/step/exec=%d%d%d%d%s" % (
                           j, g("pc"), g("irv"), g("ir"), g("pfv"), g("fp"), g("run"), g("xm"), g("mo_v"), g("fl_v"),
                           get(L_STIM, s, "gnt"), get(L_STIM, s, "rst"), get(L_STIM, s, "run"),
                           get(L_STIM, s, "stop"), get(L_STIM, s, "step"), get(L_STIM, s, "exec"),
                           " (deposit)" if dep else ""))
        return "\n".join(out)

    def fail(self, tr, k, what, exp, got):
        raise AssertionError("%s clk %d: %s expected 0x%x got 0x%x\n%s" % (tr.label, k, what, exp, got,
                                                                          self.context(tr, k)))

    async def replay(self, tr, cold=False):
        # cold: no state deposit after the reset clock, outputs only (nothing may be X or differ)
        dut, h, st = self.dut, self.h, self.st
        clk = dut.clk
        # engine reset between traces
        for n in INPUTS:
            self.drive(n, 0)
        self.drive("rst", 1)
        await RisingEdge(clk)
        for k, (stim, exp, state, dep) in enumerate(tr.recs):
            if cold:
                assert not dep or k == 0, "%s clk %d: state poked in a cold trace" % (tr.label, k)
            elif dep:
                for hd, (off, m) in zip(st, T.STATE_OFF):
                    hd.value = (state >> off) & m
            for n in INPUTS:
                off, m = L_STIM[n]
                self.drive(n, (stim >> off) & m)
            self.drive("rst", stim & 1)
            self.drive("mem_rdata", get(L_STIM, stim, "mem_rdata") if get(L_STIM, stim, "mem_v")
                       else self.rnd.getrandbits(16))
            await FallingEdge(clk)
            for n in OUTS:
                off, m = L_EXP[n]
                got = self.read(h[n], n, tr, k)
                if got != (exp >> off) & m:
                    self.fail(tr, k, n, (exp >> off) & m, got)
            for c, names in COND:
                if get(L_EXP, exp, c):
                    for n in names:
                        got = self.read(h[n], n, tr, k)
                        if got != get(L_EXP, exp, n):
                            self.fail(tr, k, n, get(L_EXP, exp, n), got)
            for hd, (path, _), (off, m) in zip(st, STATE, T.STATE_OFF):
                if cold:
                    break
                want = (state >> off) & m
                if path in CARE and not get(L_STATE, state, CARE[path]):
                    continue
                got = self.read(hd, path, tr, k)
                if got != want:
                    self.fail(tr, k, path, want, got)
            await RisingEdge(clk)
        self.clocks += len(tr.recs)

    async def run(self, label, fn, *a, **kw):
        with T.Recorder(label) as rec:
            fn(*a, **kw)
        total = rec.clocks()
        traces = [t for t in rec.traces if t.recs]
        if not FULL and total > CAP:                 # thin evenly, keep it deterministic
            keep, acc, step = [], 0, total / CAP
            nxt = 0.0
            for t in traces:
                if acc >= nxt:
                    keep.append(t)
                    nxt += step * len(t.recs)
                acc += len(t.recs)
            traces = keep
        for t in traces:
            await self.replay(t)
        n = sum(len(t.recs) for t in traces)
        self.dut._log.info("%s: %d traces, %d of %d clk replayed" % (label, len(traces), n, total))
        return n


def cases(fn):
    marks = getattr(fn, "pytestmark", [])
    if any(m.name == "slow" for m in marks) and not FULL:
        return []
    par = [m for m in marks if m.name == "parametrize"]
    params = list(inspect.signature(fn).parameters)
    if not par:
        return [{}] if not params else []
    names = [n.strip() for n in par[0].args[0].split(",")]
    if set(names) != set(params):
        return []
    out = []
    for v in par[0].args[1]:
        v = getattr(v, "values", v)
        out.append(dict(zip(names, v if len(names) > 1 else (v,))))
    return out


async def run_module(dut, mod, skip=()):
    b = Bench(dut)
    await b.start()
    n = 0
    for name in sorted(dir(mod)):
        fn = getattr(mod, name)
        if not name.startswith("test_") or not callable(fn) or name in skip:
            continue
        for kw in cases(fn):
            n += await b.run("%s.%s%s" % (mod.__name__, name, list(kw.values()) if kw else ""), fn, **kw)
    assert n > 0
    dut._log.info("%s: %d clk in lockstep" % (mod.__name__, n))


@cocotb.test()
async def test_decode_all_words(dut):
    # the undefined predicate for all 65 536 words against the ISS decoder
    b = Bench(dut)
    await b.start()
    dut.rst.value = 0
    await RisingEdge(dut.clk)
    bad = []
    for w in range(65536):
        dut.u_seq.ir.value = w
        await Timer(1, unit="ps")
        if int(dut.u_seq.undef.value) != (decode(w)[0] is None):
            bad.append(w)
    assert not bad, "undef mismatch for %d words, first %s" % (len(bad), [hex(x) for x in bad[:8]])


@cocotb.test()
async def test_s1_instructions(dut):
    await run_module(dut, test_iss)


@cocotb.test()
async def test_blocks_s2_s9_t2(dut):
    await run_module(dut, test_blocks)


@cocotb.test()
async def test_timing_t1_t3_t4_t6(dut):
    b = Bench(dut)
    await b.start()
    for m, ph, off in test_timing.CASES:
        await b.run("timing %02x/%d/%d" % (m, ph, off), test_timing.run, m, ph, off)
    await b.run("t6 isolation", test_timing.iso_trace, True)


@cocotb.test()
async def test_first_vectors(dut):
    await run_module(dut, test_first)


def reset_mid_operation():
    # engine reset in every clock of fetch / ld / st, then a reload on the same chip (back to back)
    img = test_first.assemble_text(test_first.MEMFSM)
    for ph in range(4):
        for s in range(8, 64, 1 if FULL else 3):
            c = test_first.boot(img, ph)
            for _ in range(s):
                c.step(8, 0)
            c.pulse(0, "rst")
            c.step(8, 0)
            c.boot(img, (ph + s) & 3)
            test_first.simulate(c, 200)


@cocotb.test()
async def test_reset_mid_operation(dut):
    b = Bench(dut)
    await b.start()
    assert await b.run("reset mid-operation", reset_mid_operation) > 0


@cocotb.test()
async def test_cold_start(dut):
    # after reset only, data registers unwritten (X): a loaded program must give clean, equal outputs
    b = Bench(dut)
    await b.start()
    n = 0
    for label, fn, args in (("uart_tx", test_uart.test_tx_rates, (1000000, 0)),
                            ("uart_rx", test_uart.test_rx_rates, (1000000, 1)),
                            ("spi_ctrl", test_spi.test_ctrl_modes, (0, 0)),
                            ("spi_tgt", test_spi.test_tgt_miso_drive_release, ()),
                            ("i2c_tgt", test_i2c.test_tgt_write, (0,)),
                            ("i2c_ctrl", test_i2c.test_ctrl_repeated_start_and_nack, ()),
                            ("ws2812", test_notdesigned.test_ws2812_frame, (21, 0)),
                            ("manchester", test_notdesigned.test_manchester_loopback, ())):
        with T.Recorder("cold " + label) as rec:
            fn(*args)
        for t in rec.traces[:2]:
            await b.replay(t, cold=True)
            n += len(t.recs)
    dut._log.info("cold start: %d clk" % n)


@cocotb.test()
async def test_paper_uart(dut):
    await run_module(dut, test_uart)


@cocotb.test()
async def test_paper_spi(dut):
    await run_module(dut, test_spi)


@cocotb.test()
async def test_paper_i2c(dut):
    await run_module(dut, test_i2c)


@cocotb.test()
async def test_paper_notdesigned(dut):
    await run_module(dut, test_notdesigned)
    await run_module(dut, test_variants)


@cocotb.test()
async def test_random_lockstep(dut):
    b = Bench(dut)
    await b.start()
    for seed in range(SEEDS):
        with T.Recorder("random seed %d" % seed) as rec:
            T.random_scenario(seed, NCLK, undef=False)
        for t in rec.traces:
            await b.replay(t)
    for seed in range(1000, 1000 + max(1, SEEDS // 4)):
        with T.Recorder("random+undef seed %d" % seed) as rec:
            T.random_scenario(seed, NCLK, undef=True)
        for t in rec.traces:
            await b.replay(t)
    dut._log.info("random: %d clk in lockstep" % b.clocks)
