# SHOULD variants marked I in VERIF_PLAN: SC5, ST6, IC6, IT7 (U9 partly: 3 Mbaud in test_uart / test_first).
import os
import pytest
from harness import PROG, assemble_text, boot, simulate, changes, record
from models import SpiController, I2cControllerModel, I2cTargetModel, level_at
from test_first import Seq
from test_spi import OnlineTarget, TAB
from test_i2c import i2c_run, START, BYTE, REL, STOP


def img_with_slots(name, slots, **defs):
    src = open(os.path.join(PROG, name + ".s")).read()
    for k, v in defs.items():
        src = src.replace(".equ %s," % k, ".equ %s, %s ;" % (k, v), 1)
    if slots:
        src += "\n.slots %s\n" % slots
    return assemble_text(src, name=name + ".s")


@pytest.mark.parametrize("bit,smp,slots,slot", [(6, 4, None, 0), (6, 4, None, 3), (4, 2, "0, 0, 0, 0", 1)])
def test_sc5_fast_controller(bit, smp, slots, slot):
    img = img_with_slots("spi_ctrl", slots, BIT=bit, SMP=smp)
    c = boot(img, slot)
    out_b = [0x3C, 0xA5, 0x01, 0xFE]
    tgt = OnlineTarget(0, [0x81, 0x7E, 0x55, 0xC3], delay=0.0)
    c.host_in[0] += [len(out_b)] + out_b

    def inputs(t, ch):
        tgt.update(ch)
        return 8, tgt.level(t) << 2
    simulate(c, 600 + 150 * len(out_b), inputs)
    sck = changes(c, lambda uo, o, oe: (o >> 3) & 1, 0)
    mosi = changes(c, lambda uo, o, oe: (o >> 1) & 1, 0)
    lead = [t for t, v in sck if v == 1]
    assert len(lead) == 32
    got = [level_at(mosi, t - 1e-6, 0) for t in lead]
    assert got == [(b >> (7 - i)) & 1 for b in out_b for i in range(8)]
    per = {b - a for k in range(0, 32, 8) for a, b in zip(lead[k:k + 8], lead[k + 1:k + 8])}
    ok = per == {bit}
    if bit >= 6:                                    # read only where the answer window (SMP - 3) clk - 10 ns > 0
        ok = ok and c.host_bytes(0) == [0x81, 0x7E, 0x55, 0xC3]
    record("SC5 spi_ctrl BIT %d SMP %d slots %s phase %d" % (bit, smp, slots or "default", slot), ok,
           "SCK period %s clk, MOSI correct, read %s" % (sorted(per), c.host_bytes(0)))
    assert ok


def test_st6_target_6mhz_solo():
    T = 50 / 6
    img = img_with_slots("spi_tgt", "0, 0, 0, 0")
    rows = []
    for slot in range(4):
        for ph in (0.0, 0.25, 0.5, 0.75):
            c = boot(img, slot)
            ctrls, t = [], c.cycle + 40
            for cm in (0, 1, 6):
                x = SpiController(T, 0, [cm, 0, 0], phase=ph, t0=t)
                ctrls.append(x)
                t = x.cs_hi + 40 - ph

            def inputs(tt, ch, ctrls=ctrls):
                x = Seq(ctrls)
                miso = (ch.uio_out >> 2) & (ch.uio_oe >> 2) & 1
                sck = x.sck(tt) if tt >= ctrls[0].cs_lo else 0
                return 8, sck << 3 | x.cs(tt) | x.mosi(tt) << 1 | miso << 2
            simulate(c, int(ctrls[-1].cs_hi) + 40, inputs)
            miso = changes(c, lambda uo, o, oe: (o >> 2) & (oe >> 2) & 1)
            got = [x.sample(miso) for x in ctrls]
            rows.append(got == [[0] + TAB[cm:cm + 2] for cm in (0, 1, 6)] and c.eng[0].ovr == 0)
    ok = all(rows)
    record("ST6 spi_tgt 6 MHz, slot map 0x00, 4 slot x 4 partner phases", ok, "%d/16 correct" % sum(rows))
    assert ok


def test_ic6_controller_fm_plus():
    t = I2cTargetModel(0x50, tx=[0x5A])
    img = img_with_slots("i2c_ctrl", None, TLOW=20, THIGH=16, THD=0)
    c = boot(img, 1)
    steps = [(START | BYTE | REL, 0xA0), (BYTE | REL | STOP, 0x55), (START | BYTE | REL, 0xA1), (BYTE | REL | STOP, 0xFF)]
    for fl, d in steps:
        c.host_in[0] += [fl, d]
    mon, hist, drv = i2c_run(c, t, 20000, rise=6,
                             done=lambda: not c.host_in[0] and c.rx[0].empty() and c.eng[0].waiting and
                             c.eng[0].PC == img.label("next") + 1, tail=200)
    out = c.host_bytes(0)
    words = [(out[k] >> 1 | (out[k + 1] & 1) << 7, out[k] & 1) for k in range(0, len(out) - 1, 2)]
    sc = [(t_, s) for t_, s, d in hist]
    ch = [x for k, x in enumerate(sc) if k == 0 or x[1] != sc[k - 1][1]]
    lows = [b[0] - a[0] for a, b in zip(ch, ch[1:]) if a[1] == 0]
    highs = [b[0] - a[0] for a, b in zip(ch, ch[1:]) if a[1] == 1][1:-1]
    per = [b[0] - a[0] for a, b in zip(ch, ch[2:]) if a[1] == 0]
    common = max(set(per), key=per.count)
    ok = (t.rx == [0x55] and words[-1] == (0x5A, 1) and min(lows) >= 25 and min(highs) >= 13 and min(per) >= 50)
    record("IC6 i2c_ctrl Fm+ TLOW/THIGH 20/16 THD 0, rise 120 ns", ok,
           "tLOW min %d (>= 25), tHIGH min %d (>= 13), SCL period min %d, typical %d clk (>= 50: <= 1 MHz)" %
           (min(lows), min(highs), min(per), common))
    assert ok


def test_it7_target_at_fm_minimum():
    img = img_with_slots("i2c_tgt", None)
    rows = []
    for slot in range(4):
        for k in range(4):
            c = boot(img, slot)
            for _ in range(k):
                c.step(8, 1 << 5 | 1 << 6)
            c.host_in[0] += [0xA5, 0x3C]
            m = I2cControllerModel([("S",), ("W", 0x84), ("W", 0x11), ("P",), ("S",), ("W", 0x85), ("R", 0), ("R", 1),
                                    ("P",)], 30, 65)
            i2c_run(c, m, 40000, rise=15, done=m.done)
            rows.append(m.acks == [0, 0, 0] and m.read == [0xA5, 0x3C] and c.host_bytes(0) == [0x84, 0x11, 0x85] and
                        c.eng[0].ovr == 0)
    ok = all(rows)
    record("IT7 i2c_tgt with controller at tHIGH/tLOW 30/65 clk, rise 300 ns, 4 slot x 4 partner phases", ok,
           "%d/16 correct" % sum(rows))
    assert ok
