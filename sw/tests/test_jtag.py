# jtag_ctrl.s against independent IEEE 1149.1 TAP models (models.JtagTap / JtagChain); commands from host/debug.py.
import os
import sys
import pytest
from harness import prog, boot, simulate, ROOT
from models import JtagTap, JtagChain, level_at

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


# ---------------- jtag_tap.s (TAP emulator) against JtagTap with the same TCK/TMS/TDI ----------------
from harness import PROG, changes                                      # noqa: E402
from jspe_asm import Assembler                                        # noqa: E402
from models import JtagDriver                                         # noqa: E402


def tck_bits(j):
    # host commands -> [(tms, tdi)] per TCK, as jtag_ctrl.s clocks them
    out = []
    for h, v, n, t in j.cmds:
        held, on_tms = h & 1, h >> 1 & 1
        for i in range(n):
            b = v >> i & 1
            out.append((b, held) if on_tms else (held, b))
    return out


def tap_img(n=1, **d):
    src = open(os.path.join(PROG, "jtag_tap.s")).read()
    files = [("jtag_tap.s", src)] + ([("jtag_tap1.s", src)] if n > 1 else [])
    defs = {"jtag_tap.s:%s" % k: str(v) for k, v in d.items()}
    if n > 1:
        defs.update({"jtag_tap1.s:ENG": "1", "jtag_tap1.s:ORG": "184", "jtag_tap1.s:TDIPIN": "10",
                     "jtag_tap1.s:TDOPAD": "7", "jtag_tap1.s:IDCODE": str(ID1), "jtag_tap1.s:USR": "5",
                     "jtag_tap1.s:ULEN": "12", "jtag_tap1.s:IRLEN": "5", "jtag_tap1.s:UINIT": "0xABC"})
    return Assembler(files, defs).run()


def emu_run(bits, T, slot=0, img=None, trst=(), phase=0.0, n=1):
    img = img or tap_img()
    c = boot(img, slot)
    t0 = c.cycle + 40 + phase
    drv = JtagDriver(T, bits, t0=t0, trst=[(a + t0, b + t0) for a, b in trst])

    def inputs(t, ch):
        d0 = (ch.uio_out & ch.uio_oe | ~ch.uio_oe) >> 2 & 1           # E0's TDO pad (released: high)
        return 8, drv.tck(t) << 3 | drv.tms(t) | drv.tdi(t) << 1 | d0 << 2 | drv.trst(t) << 4
    simulate(c, int(drv.end - c.cycle) + 60, inputs)
    pad = 7 if n > 1 else 2
    tdo = changes(c, lambda uo, o, oe: (o & oe | ~oe) >> pad & 1, 1)
    oe = changes(c, lambda uo, o, e: e >> pad & 1, 0)
    return c, drv, drv.sample(tdo), [level_at(oe, t - 1e-9, 0) for t in drv.rise_t]


def model_tdo(taps, bits, trst_at=()):
    # expected TDO (None = released) before each rising edge, and the TAPs after the run
    out = []
    for k, (tms, tdi) in enumerate(bits):
        if k in trst_at:
            for x in taps:
                x.trst()
        out.append(taps[-1].tdo)
        ins = [tdi] + [x.tdo if x.tdo is not None else 1 for x in taps[:-1]]
        for x, d in zip(taps, ins):
            x.rise(tms, d)
        for x in taps:
            x.fall()
    return out


def check_emu(want, got, oe):
    for k, (w, g, e) in enumerate(zip(want, got, oe)):
        assert e == (w is not None), (k, w, e)
        assert w is None or w == g, (k, w, g)


def session():
    j = debug.Jtag().reset()
    j.scan(False, 0, 40)                               # IDCODE (32) + 8 bits passing through
    j.scan(True, 0xA, 4).scan(False, 0xC3, 8).scan(False, 0x3C, 8)
    j.scan(True, 0x3, 4).scan(False, 0b101, 3)         # unknown opcode: BYPASS
    j.goto("PDR").goto("SHDR").clocks(2).goto("E2DR").goto("SHDR").goto("UDR")
    j.scan(True, 0xE, 4).goto("SHDR").clocks(20).goto("PDR").clocks(3).goto("E2DR").goto("SHDR").clocks(20)
    j.goto("PIR").goto("SHIR").goto("RTI").clocks(4).scan(True, 0xEE, 8).reset()
    return j


@pytest.mark.parametrize("T,slot", [(100, 0), (50, 1), (40, 2), (36, 3)])
def test_tap_session(T, slot):
    bits = tck_bits(session())
    want = model_tdo([tap()], bits)
    c, drv, got, oe = emu_run(bits, T, slot, phase=slot * 0.3)
    check_emu(want, got, oe)
    assert c.host_bytes(0) == [0xC3, 0x3C] and c.eng[0].ovr == 0


@pytest.mark.parametrize("d", [{"IRLEN": 2, "IDC": 1, "USR": 2, "ULEN": 1, "UINIT": 1},
                               {"IRLEN": 16, "IDC": 0x20, "USR": 0x41, "ULEN": 16, "UINIT": 0xBEEF},
                               {"IRLEN": 6, "IDC": 9, "USR": 0x30, "ULEN": 13, "UINIT": 0x1234, "TRST": 0}])
def test_tap_lengths(d):
    irlen, ulen = d["IRLEN"], d["ULEN"]
    ref = JtagTap(irlen, ID0, d["IDC"], {d["USR"]: [ulen, d["UINIT"]]})
    j = debug.Jtag().reset().scan(False, 0, 33).scan(True, d["USR"], irlen)
    j.scan(False, 0x5A5A & ((1 << ulen) - 1), ulen).scan(False, 0x0FF0, ulen + 3)
    j.scan(True, (1 << irlen) - 1, irlen + 2).scan(False, 1, 4)
    bits = tck_bits(j)
    want = model_tdo([ref], bits)
    c, drv, got, oe = emu_run(bits, 50, 1, img=tap_img(**d))
    check_emu(want, got, oe)
    v = [b for _, b in c.host_out[0]]
    assert len(ref.updates) == 2 and c.eng[0].ovr == 0
    vals = [v[k] | v[k + 1] << 8 for k in range(0, len(v), 2)] if ulen > 8 else v
    assert vals == [u for _, u in ref.updates]


def test_tap_chain_two_engines():
    # E0 -> E1 (E1's TDI = E0's TDO pad uio[2], TDO on uio[7]); same session against two JtagTap models
    a = tap()
    b = JtagTap(5, ID1, 0x1, {0x5: [12, 0xABC]})
    j = debug.Jtag().reset().scan(False, 0, 64).scan(True, 0x05 | 0xF << 5, 9).scan(False, 0x123, 13)
    j.scan(True, 0x1FF, 9).scan(False, 0x1, 6).reset()
    bits = tck_bits(j)
    want = model_tdo([a, b], bits)
    c, drv, got, oe = emu_run(bits, 60, 0, img=tap_img(2), n=2)
    check_emu(want, got, oe)
    assert b.updates == [(5, 0x123)] and [x for _, x in c.host_out[1]] == [0x23, 0x01]


def test_tap_trst_and_tlr():
    # TRST low mid-Shift-DR (user DR) -> Test-Logic-Reset at once, TDO released, IR = IDCODE; held low over 6 TCKs
    # with TMS 0 keeps the TAP in reset; 5 x TMS = 1 from Shift-IR also resets
    j = debug.Jtag().reset().scan(True, 0xA, 4).goto("SHDR").clocks(5)
    k0 = len(tck_bits(j))
    j.clocks(6)
    j.state = "TLR"
    j.goto("RTI").scan(False, 0, 32).scan(True, 0xA, 4).goto("SHIR").clocks(3).reset().scan(False, 0, 32)
    bits = tck_bits(j)
    for k in range(k0, k0 + 6):
        bits[k] = (0, 1)
    T = 50
    trst = [(k0 * T + 5, (k0 + 6) * T)]
    ref = tap()
    want = model_tdo([ref], bits, trst_at=set(range(k0, k0 + 6)))
    c, drv, got, oe = emu_run(bits, T, 2, trst=trst)
    check_emu(want, got, oe)
    assert c.host_bytes(0) == [] and c.eng[0].ovr == 0


def test_tap_too_fast_fails():
    # negative: below the stated minimum (TCK high/low < HALF) the IDCODE read breaks in some slot phase
    bits = tck_bits(session())
    want = model_tdo([tap()], bits)
    bad = 0
    for slot in range(4):
        try:
            check_emu(want, *emu_run(bits, 16, slot)[2:])
        except AssertionError:
            bad += 1
    assert bad
