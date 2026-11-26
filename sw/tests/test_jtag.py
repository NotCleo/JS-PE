# jtag_ctrl.s against independent IEEE 1149.1 TAP models (models.JtagTap / JtagChain); commands from host/debug.py.
import os
import sys
import pytest
from harness import prog, boot, simulate, ROOT
from models import JtagTap, JtagChain

sys.path.insert(0, os.path.join(ROOT, "host"))
import debug                                                          # noqa: E402

ID0, ID1 = 0x1C1E0001, 0x0BADC0DF


class Watch:
    # feeds the chip's TCK/TMS/TDI pads to a TAP chain; TDO back on uio_in[2]
    def __init__(self, chain):
        self.chain, self.seen, self.log = chain, 0, []

    def update(self, c):
        tr = c.pad_trace
        while self.seen < len(tr):
            t, uo, out, oe = tr[self.seen]
            self.seen += 1
            tck, tms, tdi = out >> 3 & 1, out & 1, out >> 1 & 1
            if self.chain.prev is not None and tck != self.chain.prev:
                self.log.append((t, tck, tms, tdi))
            self.chain.step(t, tck, tms, tdi)


def run(j, taps, bit=12, smp=6, slot=0, delay=1.0, clk=None, img=None):
    img = img or prog("jtag_ctrl", BIT=bit, SMP=smp)
    c = boot(img, slot)
    ch = JtagChain(taps, delay)
    w = Watch(ch)
    c.host_in[0] += list(j.data())

    def inputs(t, chip):
        w.update(chip)
        return 8, ch.level(t) << 2
    simulate(c, clk or 400 + len(j.cmds) * (8 * bit + 90), inputs)
    rx = c.host_bytes(0)
    assert len(rx) == len(j.cmds) and c.eng[0].ovr == 0
    return c, j.result(rx), w.log


def tap(**kw):
    return JtagTap(irlen=kw.pop("irlen", 4), idcode=kw.pop("idcode", ID0), idc_op=kw.pop("idc_op", 0xE),
                   regs=kw.pop("regs", {0xA: [8, 0x5A]}))


@pytest.mark.parametrize("slot", [0, 1, 2, 3])
def test_ctrl_idcode_and_user_dr(slot):
    t = tap()
    j = debug.Jtag().reset()
    j.scan(False, 0, 32)                              # IDCODE selected by Test-Logic-Reset
    j.scan(True, 0xA, 4).scan(False, 0xC3, 8).scan(False, 0x00, 8)
    c, res, log = run(j, [t], slot=slot)
    assert res[0] == ID0 and res[1] == 0b0001 and res[2] == 0x5A and res[3] == 0xC3
    assert t.updates == [(0xA, 0xC3), (0xA, 0x00)] and t.state == "RTI"


@pytest.mark.parametrize("bit,smp", [(12, 6), (8, 6), (6, 4), (50, 25)])
def test_ctrl_chain_bypass(bit, smp):
    # two TAPs: IR scan of 4 + 5 bits, BYPASS in the first, user DR (12 bits) in the second; chain length found by
    # flushing ones through all-BYPASS
    a = tap()
    b = JtagTap(irlen=5, idcode=ID1, idc_op=0x01, regs={0x07: [12, 0xABC]})
    j = debug.Jtag().reset()
    j.scan(False, 0, 64)                              # both IDCODEs
    j.scan(True, 0x07 | 0x0F << 5, 9)                 # b gets the first 5 bits (0x07), a BYPASS
    j.scan(False, 0x123, 13)                          # b's 12-bit DR, then a's bypass bit
    j.scan(True, 0x1FF, 9).scan(False, 0, 8)          # all BYPASS, then shift zeros: 2 bypass bits
    c, res, log = run(j, [a, b], bit, smp, delay=smp - 3.5)
    assert res[0] == ID0 << 32 | ID1                  # b's IDCODE comes out first
    assert res[1] == 1 | 1 << 5                       # IR captures ..01: b's 5 bits first, then a's 4
    assert res[2] == 0xABC and b.updates == [(0x07, 0x123)]
    assert a.ir == 0xF and b.ir == 0x1F


def test_ctrl_tms_walks_and_timing():
    # every state reached by goto, back to Test-Logic-Reset with 5 TMS = 1; TCK low/high exactly SMP / BIT - SMP
    # inside a command; TMS/TDI change only while TCK is low
    t = tap()
    j = debug.Jtag().reset()
    for st in ("PDR", "E2DR", "SHDR", "UIR", "PIR", "SIR", "RTI", "UDR", "TLR"):
        j.goto(st)
    c, res, log = run(j, [t], 10, 4)
    assert t.state == "TLR"
    rises = [x[0] for x in log if x[1]]
    falls = [x[0] for x in log if not x[1]]
    assert all(min(f for f in falls if f > r) - r == 6 for r in rises)          # high = BIT - SMP, every bit
    lows = [min(r for r in rises if r > f) - f for f in falls if any(r > f for r in rises)]
    assert min(lows) == 4 and sum(x == 4 for x in lows) >= len(rises) - len(j.cmds)   # low = SMP inside a command
    prev = None
    for tt, uo, out, oe in c.pad_trace:                                         # TMS/TDI move only while TCK is low
        if prev is not None and (out ^ prev) & 3:
            assert out >> 3 & 1 == 0, tt
        prev = out


@pytest.mark.parametrize("smp", [4, 6, 8])
def test_ctrl_tdo_window(smp):
    # header: TDO valid (SMP - 3) clk - 10 ns after the falling edge at the pad is sampled correctly; 1 clk later fails
    j = debug.Jtag().reset().scan(False, 0, 32)
    _, res, _ = run(j, [tap()], 12, smp, delay=smp - 3 - 0.5)
    assert res[0] == ID0
    _, res, _ = run(j, [tap()], 12, smp, delay=smp - 2 + 0.1)
    assert res[0] != ID0


def test_ctrl_trst_like_reset_from_any_state():
    # negative: a TAP left in Shift-DR (host walked there) recovers by the 5 x TMS = 1 reset; IR back to IDCODE
    t = tap()
    j = debug.Jtag().reset().scan(True, 0xA, 4).goto("SHDR")
    j.reset().scan(False, 0, 32)
    _, res, _ = run(j, [t])
    assert res[1] == ID0 and t.ir == 0xE


def scenario(img, slot, pp=0):
    # T5/T6: IDCODE + IR + user DR round trip at the image's BIT (12 as written)
    t = tap()
    j = debug.Jtag().reset().scan(False, 0, 32).scan(True, 0xA, 4).scan(False, 0x3C + pp, 8).clocks(3)
    c, res, _ = run(j, [t], slot=slot, img=img)
    assert res[0] == ID0 and t.updates == [(0xA, 0x3C + pp)]
    return c
