# i2c_mon.s (monitor_design.md, MON1 at the ISS level): passive decode of a bus between a controller model and a
# target model; the bus history and its UM10204 timing are identical with and without the monitor.
import pytest
from harness import prog
from jspe_iss import Chip
from models import I2cLine, I2cMonitor, I2cControllerModel, I2cTargetModel, i2c_timing, i2c_violations

SCL, SDA = 6, 5
EV = 0x100
STEP = 16                                    # timer step of the stretch measurement (clk)


def boot(img, slot=0):
    # load with the bus idle (SCL, SDA high) as on the board, then run in slot phase `slot`
    c = Chip()
    for e in c.eng:
        e.trace = True
    c.boot(img, run=False)
    for _ in range(8):
        c.step(8, 1 << SCL | 1 << SDA)
    while c.slot != slot:
        c.step(8, 1 << SCL | 1 << SDA)
    c.start(sorted(img.engines), slot)
    return c


def bus_run(chip, ctrl, tgt, ncyc, rise=0, tail=400):
    # chip = None: the same bus without the monitor
    ls, ld = I2cLine(rise), I2cLine(rise)
    mon = I2cMonitor()
    hist, t, end = [], 0, None
    for _ in range(ncyc):
        t = chip.cycle if chip else t + 1
        scl = ls.update(t, not ctrl.scl_rel or not tgt.scl_rel)
        sda = ld.update(t, not ctrl.sda_rel or not tgt.sda_rel)
        if chip:
            assert chip.uio_oe == 0
            chip.step(0x08, scl << SCL | sda << SDA)
        ctrl.step(t, scl, sda)
        tgt.step(t, scl, sda)
        mon.step(t, scl, sda)
        if not hist or hist[-1][1:] != (scl, sda):
            hist.append((t, scl, sda))
        if end is None and ctrl.done():
            end = t + tail
        if end is not None and t >= end:
            break
    return mon, hist


def session(script, tx=(), stretch=None, slot=0, thigh=60, tlow=65, rise=0, ncyc=200000, nack_after=None, **defs):
    c = boot(prog("i2c_mon", **defs), slot)
    t0 = c.cycle
    mk = lambda: (I2cControllerModel(script, thigh, tlow), I2cTargetModel(0x42, tx, stretch, nack_after))
    ctrl, tgt = mk()
    mon, hist = bus_run(c, ctrl, tgt, ncyc, rise)
    ctrl2, tgt2 = mk()
    _, hist2 = bus_run(None, ctrl2, tgt2, ncyc, rise)
    assert [(t - t0, s, d) for t, s, d in hist] == [(t - 1, s, d) for t, s, d in hist2]     # monitor never affects it
    return c, ctrl, mon, hist


def stream(c, e=0):
    # events as "eN"; evt 5 becomes ("e5", length in steps)
    out, s, k = [], c.host_out[e], 0
    while k < len(s):
        v = s[k][1]
        if v == EV | 5:
            out.append(("e5", s[k + 1][1] | s[k + 2][1] << 8))
            k += 2
        else:
            out.append("e%d" % (v & 0x3F) if v & EV else v)
        k += 1
    return out


def expect(mon):
    out, open_ = [], False
    for ev in mon.events:
        if ev[0] == "S":
            out.append("e2" if open_ else "e1")
            open_ = True
        elif ev[0] == "P":
            out.append("e3")
            open_ = False
        else:
            out += [ev[1]] + (["e4"] if ev[2] else [])
    return out


def plain(s):
    return [x for x in s if not isinstance(x, tuple)]


W = [("S",), ("W", 0x84), ("W", 0x10), ("W", 0xA5), ("W", 0x1B), ("P",)]
R = [("S",), ("W", 0x84), ("W", 0x02), ("S",), ("W", 0x85), ("R", 0), ("R", 0), ("R", 1), ("P",)]


@pytest.mark.parametrize("slot", [0, 1, 2, 3])
@pytest.mark.parametrize("rate,thigh,tlow", [(400000, 60, 65), (100000, 240, 260)])
def test_write_read_sr(slot, rate, thigh, tlow):
    c, ctrl, mon, hist = session(W + R, tx=[0x3C, 0x00, 0xFF], slot=slot, thigh=thigh, tlow=tlow, RATE=rate)
    s = stream(c)
    assert s == expect(mon)
    assert s[:2] == ["e1", 0x84] and "e2" in s and s[-2:] == ["e4", "e3"]
    assert ctrl.read == [0x3C, 0x00, 0xFF]
    assert c.eng[0].ovr == 0


def test_nack_address_and_data():
    # wrong address (NACK, no target), then a write NACKed after two bytes
    script = [("S",), ("W", 0x90), ("P",), ("S",), ("W", 0x84), ("W", 1), ("W", 2), ("W", 3), ("P",)]
    c, ctrl, mon, hist = session(script, nack_after=2)
    s = stream(c)
    assert s == expect(mon)
    assert s[:4] == ["e1", 0x90, "e4", "e3"] and s[-4:] == [2, "e4", 3, "e4", "e3"][-4:]


@pytest.mark.parametrize("slot", [0, 2])
def test_stretch_detected(slot):
    # target holds SCL 800 clk after the address byte's 8th fall (before ACK) and 1200 after a data ACK
    st = {("addr", 8, "data"): 800, ("wr", 8, "ack"): 1200}
    c, ctrl, mon, hist = session(W, stretch=st, slot=slot)
    s = stream(c)
    assert plain(s) == expect(mon)
    got = [x[1] * STEP for x in s if isinstance(x, tuple)]
    assert len(got) == 2, s
    assert abs(got[0] - 800) <= 3 * STEP + 65 and abs(got[1] - 1200) <= 3 * STEP + 65, got
    i = s.index(next(x for x in s if isinstance(x, tuple)))
    assert s[i - 1] == 0x84                          # reported after the address byte, before its ACK clock ends


def test_no_stretch_reported_at_nominal_rate():
    for slot in range(4):
        c, ctrl, mon, hist = session(W + R, tx=[1, 2, 3], slot=slot)
        assert not any(isinstance(x, tuple) for x in stream(c))


@pytest.mark.parametrize("rise", [0, 10])
def test_timing_checker_unchanged(rise):
    # bus history identical to the run without the chip (session asserts it); Fm-compliant at rise 0
    rd = [("S",), ("W", 0x85), ("R", 0), ("R", 0), ("R", 1), ("P",)]        # no Sr: the model's Sr low phase is tsu
    c, ctrl, mon, hist = session(W + rd + W + R, tx=[1, 2, 3, 4, 5], thigh=40, tlow=80, rise=rise)
    if not rise:
        p2 = [e[0] for e in mon.events].index("P", 8)                       # STOP that ends W + rd
        n = next(i for i, h in enumerate(hist) if h[0] > mon.events[p2][1] + 100)
        assert i2c_violations(i2c_timing(hist[:n]), "Fm") == []
    assert stream(c) == expect(mon)


@pytest.mark.parametrize("slot", [0, 1, 2, 3])
def test_multibyte_long(slot):
    data = [(i * 29 + 7) & 0xFF for i in range(40)]
    c, ctrl, mon, hist = session([("S",), ("W", 0x84)] + [("W", b) for b in data] + [("P",)], slot=slot)
    assert stream(c) == expect(mon) == ["e1", 0x84] + data + ["e3"]


def test_stop_and_start_mid_byte():
    # START, 3 bits, STOP; then a repeated START 5 bits into a byte: unfinished bytes are dropped, framing kept
    c = boot(prog("i2c_mon"), 0)
    ctrl, tgt = I2cControllerModel([("S",), ("W", 0x84)], 60, 65), I2cTargetModel(0x42)
    ops = []
    for b in (1, 0, 1):
        ops += [("sda", b), ("wait", 30), ("scl", 1), ("waithigh",), ("wait", 60), ("scl", 0), ("wait", 30)]
    ops += [("sda", 0), ("wait", 30), ("scl", 1), ("waithigh",), ("wait", 40), ("sda", 1), ("wait", 200)]
    ctrl.script = [("S",), ("W", 0x84), ("W", 0x55)] + [("P",)]
    ctrl.ops = [("sda", 1), ("wait", 30), ("scl", 1), ("waithigh",), ("wait", 60), ("sda", 0), ("wait", 60),
                ("scl", 0), ("wait", 30)] + ops
    bus_run(c, ctrl, tgt, 60000)
    assert stream(c) == ["e1", "e3", "e1", 0x84, 0x55, "e3"]


def test_fast_mode_plus_1mhz():
    # 1 MHz: SCL high 13 clk, low 25 clk
    for slot in range(4):
        c, ctrl, mon, hist = session(W + R, tx=[0x3C, 0x00, 0xFF], slot=slot, thigh=13, tlow=25, RATE=1000000)
        assert stream(c) == expect(mon), slot


def test_host_stall_lost():
    c = boot(prog("i2c_mon"), 1)
    c.tx_block[0] = True
    ctrl, tgt = I2cControllerModel([("S",), ("W", 0x84)] + [("W", i) for i in range(8)] + [("P",)], 60, 65), \
        I2cTargetModel(0x42)
    bus_run(c, ctrl, tgt, 100000)                  # its STOP is dropped too
    c.tx_block[0] = False
    ctrl.script = [("S",), ("W", 0x84), ("W", 0x99), ("P",)]
    bus_run(c, ctrl, tgt, 100000)
    s = stream(c)
    assert s == ["e1", 0x84, 0, 1, "e63", "e1", 0x84, 0x99, "e3"], s       # LOST before the next START


def test_words():
    assert len(prog("i2c_mon").words) == 39


def test_stretch_detected_1mhz():
    st = {("addr", 8, "data"): 400, ("wr", 8, "ack"): 600}
    c, ctrl, mon, hist = session(W, stretch=st, slot=1, thigh=13, tlow=25, RATE=1000000)
    s = stream(c)
    assert plain(s) == expect(mon)
    got = [x[1] * STEP for x in s if isinstance(x, tuple)]
    assert len(got) == 2 and abs(got[0] - 400) <= 3 * STEP + 25 and abs(got[1] - 600) <= 3 * STEP + 25, got
