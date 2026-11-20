# i2c_ctrl.s and i2c_tgt.s against independent bus models (open-drain wired-AND, pull-up rise, stretching).
import pytest
from harness import prog, boot, record
from models import I2cLine, I2cMonitor, I2cControllerModel, I2cTargetModel

SCL, SDA = 6, 5


def low(chip, b):
    return (chip.uio_oe >> b) & 1 and not (chip.uio_out >> b) & 1


def i2c_run(chip, model, ncyc, rise=0, tail=400, done=None):
    ls, ld = I2cLine(rise), I2cLine(rise)
    mon = I2cMonitor()
    hist, drv = [], []
    end = None
    for _ in range(ncyc):
        t = chip.cycle
        cs, cd = low(chip, SCL), low(chip, SDA)
        scl = ls.update(t, cs or not model.scl_rel)
        sda = ld.update(t, cd or not model.sda_rel)
        if (chip.uio_out >> SCL) & (chip.uio_oe >> SCL) & 1 or (chip.uio_out >> SDA) & (chip.uio_oe >> SDA) & 1:
            raise AssertionError("open-drain pad driven high at %d" % t)
        chip.step(0x08, scl << SCL | sda << SDA)
        model.step(t, scl, sda)
        mon.step(t, scl, sda)
        if not hist or hist[-1][1:] != (scl, sda):
            hist.append((t, scl, sda))
        if not drv or drv[-1][1:] != (cs, cd):
            drv.append((t, cs, cd))
        if done and end is None and done():
            end = t + tail
        if end is not None and t >= end:
            break
    return mon, hist, drv


# ---------------------------------------------------------------- target (i2c_tgt.s)
def tgt_session(script, host_tx=(), slot=0, thigh=60, tlow=65, rise=0, ncyc=40000, **defs):
    img = prog("i2c_tgt", **defs)
    c = boot(img, slot)
    c.host_in[0] += list(host_tx)
    m = I2cControllerModel(script, thigh, tlow)
    mon, hist, drv = i2c_run(c, m, ncyc, rise, done=m.done)
    return c, m, mon, hist, drv


@pytest.mark.parametrize("slot", [0, 3])
def test_tgt_write(slot):
    c, m, mon, hist, drv = tgt_session([("S",), ("W", 0x84), ("W", 0x11), ("W", 0xFE), ("P",)], slot=slot)
    assert m.acks == [0, 0, 0]
    assert c.host_bytes(0) == [0x84, 0x11, 0xFE]
    assert c.eng[0].ovr == 0


@pytest.mark.parametrize("slot,rise", [(1, 0), (2, 15)])
def test_tgt_read(slot, rise):
    c, m, mon, hist, drv = tgt_session([("S",), ("W", 0x85), ("R", 0), ("R", 0), ("R", 1), ("P",)],
                                       host_tx=[0xA5, 0x3C, 0x81], slot=slot, rise=rise)
    assert m.acks == [0]
    assert m.read == [0xA5, 0x3C, 0x81]
    assert c.host_bytes(0) == [0x85]
    assert (c.uio_oe >> SDA) & 1 == 0 and (c.uio_oe >> SCL) & 1 == 0


@pytest.mark.parametrize("addr", [0x43, 0x00, 0x7F])
def test_tgt_other_address(addr):
    c, m, mon, hist, drv = tgt_session([("S",), ("W", addr << 1), ("W", 0x11), ("P",)])
    assert m.acks[0] == 1 and c.host_bytes(0) == []


def test_tgt_own_address_variant():
    c, m, mon, hist, drv = tgt_session([("S",), ("W", 0x20 << 1), ("W", 0x5A), ("P",)], ADDR=0x20)
    assert m.acks == [0, 0] and c.host_bytes(0) == [0x40, 0x5A]


def test_tgt_repeated_start_and_stop_mid_byte():
    # STOP after 3 bits of a written byte, then a full transfer; repeated START inside a byte
    img = prog("i2c_tgt")
    c = boot(img, 0)
    m = I2cControllerModel([("S",), ("W", 0x84)], 60, 65)
    i2c_run(c, m, 6000, done=m.done, tail=10)
    m.script = [("S",), ("W", 0x84), ("W", 0x77), ("P",)]
    m.ops = [("sda", 1), ("wait", 40), ("scl", 1), ("waithigh",), ("wait", 30), ("sda", 0), ("wait", 30),
             ("scl", 0), ("wait", 30)]
    i2c_run(c, m, 20000, done=m.done)
    assert c.host_bytes(0) == [0x84, 0x84, 0x77]
    assert m.acks[-2:] == [0, 0]


def test_tgt_hold_timing():
    # IT6: SCL held <= 4 clk after the fall at the pad; own SDA changes THD + 3..4 clk after the fall (D081); never
    # while SCL is high
    c, m, mon, hist, drv = tgt_session([("S",), ("W", 0x84), ("W", 0x5A), ("S",), ("W", 0x85), ("R", 0),
                                        ("R", 1), ("P",)], host_tx=[0xC3, 0x3C], slot=1)
    assert m.read == [0xC3, 0x3C]
    falls = [t for (t, s, d), (t0, s0, d0) in zip(hist[1:], hist) if s0 == 1 and s == 0]
    scl_hold, sda_d = [], []
    for k, (t, cs, cd) in enumerate(drv[1:], 1):
        pcs, pcd = drv[k - 1][1], drv[k - 1][2]
        f = max([x for x in falls if x <= t], default=None)
        if cs and not pcs:
            scl_hold.append(t - f)
        if cd != pcd:
            scl_now = [h[1] for h in hist if h[0] <= t][-1]
            assert scl_now == 0, "chip changed SDA while SCL high at %d" % t
            sda_d.append(t - f)
    hold_ok = max(scl_hold) <= 4
    near = [x for x in sda_d if x <= 18]
    late = sorted(set(x for x in sda_d if 18 < x <= 30))
    ok = hold_ok and min(sda_d) >= 16 and min(near) >= 16 and max(near) <= 17
    record("i2c_tgt IT6: SCL hold, SDA change after SCL fall (THD 13)", ok,
           "SCL hold %d..%d clk; tick-timed SDA changes %d..%d clk after the fall; bit 7 of a read byte %s clk "
           "(after pull); all >= 300 ns" % (min(scl_hold), max(scl_hold), min(near), max(near), late))
    assert ok


# ---------------------------------------------------------------- controller (i2c_ctrl.s)
def ctrl_session(steps, tgt, slot=0, rise=0, ncyc=60000, tx_block=False, **defs):
    img = prog("i2c_ctrl", **defs)
    c = boot(img, slot)
    c.tx_block[0] = tx_block
    for fl, d in steps:
        c.host_in[0] += [fl, d]
    nsteps = len(steps)
    mon, hist, drv = i2c_run(c, tgt, ncyc, rise,
                             done=lambda: not c.host_in[0] and c.rx[0].empty() and c.eng[0].waiting and
                             c.eng[0].PC == img.label("next") + 1, tail=200)
    out = c.host_bytes(0)
    words = [(out[k] >> 1 | (out[k + 1] & 1) << 7, out[k] & 1) for k in range(0, len(out) - 1, 2)]
    c.drv = drv
    return c, img, mon, hist, words


START, BYTE, REL, STOP = 1, 2, 4, 8


@pytest.mark.parametrize("slot,defs", [(0, {}), (2, {"THD": 0})])
def test_ctrl_write_read(slot, defs):
    t = I2cTargetModel(0x50, tx=[0x9C, 0x42])
    steps = [(START | BYTE | REL, 0xA0), (BYTE | REL, 0x11), (BYTE | REL | STOP, 0x22),
             (START | BYTE | REL, 0xA1), (BYTE, 0xFF), (BYTE | REL | STOP, 0xFF)]
    c, img, mon, hist, words = ctrl_session(steps, t, slot, **defs)
    assert t.rx == [0x11, 0x22]
    assert words == [(0xA0, 0), (0x11, 0), (0x22, 0), (0xA1, 0), (0x9C, 0), (0x42, 1)]
    assert mon.summary() == ["S", (0xA0, 0), (0x11, 0), (0x22, 0), "P", "S", (0xA1, 0), (0x9C, 0), (0x42, 1), "P"]


def test_ctrl_repeated_start_and_nack():
    t = I2cTargetModel(0x50, tx=[0x77], nack_after=1)
    steps = [(START | BYTE | REL, 0xA0), (BYTE | REL, 0x01), (START | BYTE | REL, 0xA1), (BYTE | REL | STOP, 0xFF),
             (START | BYTE | REL | STOP, 0xB4)]
    c, img, mon, hist, words = ctrl_session(steps, t, 1)
    assert words == [(0xA0, 0), (0x01, 1), (0xA1, 0), (0x77, 1), (0xB4, 1)]
    assert mon.summary()[:4] == ["S", (0xA0, 0), (0x01, 1), "S"]


def test_ctrl_timing_400k():
    # IC2 (Fm): tLOW >= 65, tHIGH >= 30 clk, SDA changes >= 15 clk (300 ns) after SCL driven low, rise 300 ns
    t = I2cTargetModel(0x50, tx=[0x5A])
    steps = [(START | BYTE | REL, 0xA0), (BYTE | REL | STOP, 0x55), (START | BYTE | REL, 0xA1),
             (BYTE | REL | STOP, 0xFF)]
    c, img, mon, hist, words = ctrl_session(steps, t, 3, rise=15)
    assert words[-1] == (0x5A, 1)
    lows, highs = [], []
    for (ta, sa, da), (tb, sb, db) in zip(hist, hist[1:]):
        pass
    sc = [(t_, s) for t_, s, d in hist]
    ch = [x for k, x in enumerate(sc) if k == 0 or x[1] != sc[k - 1][1]]
    for (ta, va), (tb, vb) in zip(ch, ch[1:]):
        (lows if va == 0 else highs).append(tb - ta)
    sda_after = []
    drv = [(t_, d) for t_, s, d in c.drv]
    falls = [b[0] for a, b in zip(ch, ch[1:]) if a[1] == 1 and b[1] == 0]
    sdach = [b[0] for a, b in zip(drv, drv[1:]) if a[1] != b[1]]
    for x in sdach:
        f = max([y for y in falls if y <= x], default=None)
        scl_now = [v for t_, v in ch if t_ <= x][-1]
        if f is not None and scl_now == 0:
            sda_after.append(x - f)
    ok = min(lows) >= 65 and min(highs[:-1]) >= 30 and min(sda_after) >= 15
    record("i2c_ctrl IC2 400 kHz, rise 300 ns, THD 12", ok, "tLOW min %d clk, tHIGH min %d clk, SDA %d clk after "
           "SCL low (>= 15)" % (min(lows), min(highs[:-1]), min(sda_after)))
    assert ok


def test_ctrl_stretch():
    t = I2cTargetModel(0x50, stretch={("wr", 3): 100, ("addr", 8): 400})
    steps = [(START | BYTE | REL, 0xA0), (BYTE | REL | STOP, 0x3C)]
    c, img, mon, hist, words = ctrl_session(steps, t, 0)
    assert t.rx == [0x3C] and words == [(0xA0, 0), (0x3C, 0)]


def test_ctrl_stretch_timeout():
    t = I2cTargetModel(0x50, stretch={("wr", 2): 3000})
    steps = [(START | BYTE | REL, 0xA0), (BYTE | REL, 0x3C), (STOP, 0)]
    c, img, mon, hist, words = ctrl_session(steps, t, 0, ncyc=20000, DEADLINE=2000)
    ev = [v & 63 for _, v in c.host_out[0] if v & 0x100]
    assert ev == [3]
    assert mon.summary()[-1] == "P"


def test_ctrl_host_tx_blocked():
    # SCL stays low while the returned bytes cannot be pushed; nothing lost when the host reads again
    t = I2cTargetModel(0x50)
    img = prog("i2c_ctrl")
    c = boot(img, 0)
    c.tx_block[0] = True
    for fl, d in [(START | BYTE | REL, 0xA0), (BYTE | REL, 1), (BYTE | REL, 2), (BYTE | REL | STOP, 3)]:
        c.host_in[0] += [fl, d]
    mon, hist, drv = i2c_run(c, t, 12000)
    assert len(t.rx) == 2 and (c.uio_oe >> SCL) & 1      # address + byte 1 fill the FIFO, byte 2 waits
    c.tx_block[0] = False
    mon2, hist2, drv2 = i2c_run(c, t, 20000)
    out = c.host_bytes(0)
    assert t.rx == [1, 2, 3] and len(out) == 8 and c.eng[0].ovr == 0
