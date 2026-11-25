# Fault-injection variants (docs/spec/programs/fault_design.md, EI1 at the ISS level): each fault is reported
# once, at its position, by the partner receiver/model; default images unchanged.
import hashlib
import os
import re
import pytest
from harness import PROG, prog, assemble, boot, simulate, changes
from models import uart_decode
from models import I2cControllerModel
from test_i2c import i2c_run

MAP = {k: int(v) for k, v in re.findall(r"^\.equ\s+(\w+),\s*(\d+)", open(os.path.join(PROG, "cleo_mem.inc")).read(), re.M)}
ARMED = 0x8000


def fword(typ, pos=0, arg=0):
    return ARMED | typ << 12 | pos << 8 | arg


def digest(img):
    return hashlib.sha1(repr(sorted(img.words.items())).encode()).hexdigest()[:12]


@pytest.mark.parametrize("name,sha", [("uart_tx", "5d021c0da04e"), ("i2c_tgt", "b6ae2a376afe")])
def test_default_images_unchanged(name, sha):
    assert digest(prog(name)) == sha and digest(prog(name, FAULTS=0)) == sha


# ---- uart_tx (E0) looped back into uart_rx (E1) and the independent decoder
def uart_loop(chars, faults, baud=1000000, phase=0, **defs):
    # faults: {index: word} written to the fault word while the line is idle before character `index`
    fw = MAP["FAULT_W"]
    d = {"uart_tx.s:FAULTS": "1", "uart_rx.s:ENG": "1", "uart_rx.s:ORG": "64", "BAUD": str(baud)}
    d.update({k: str(v) for k, v in defs.items()})
    img = assemble([os.path.join(PROG, "uart_tx.s"), os.path.join(PROG, "uart_rx.s")], d)
    c = boot(img, phase)
    loop = lambda t, ch: (((ch.uo & 1) << 7) | 8, 0)
    bit = 50e6 / baud
    simulate(c, 30, loop)
    fired = []
    for k, ch in enumerate(chars):
        if k in faults:
            c.mem[fw] = faults[k]
        c.host_in[0].append(ch)
        simulate(c, int(bit * 40), loop)                 # character (or break) + idle
        fired.append(c.mem[fw])
    nom = 50e6 // baud
    line = [x for x in changes(c, lambda uo, a, b: uo & 1, 1) if x[0] > 25]
    return c, [v for _, v in c.host_out[1]], uart_decode(line, nom, parity=defs.get("PARITY", 0)), fired


@pytest.mark.parametrize("phase", [0, 1, 2, 3])
@pytest.mark.parametrize("baud", [1000000, 1086956])                 # BIT 50 and the minimum FBIT = 46
def test_uart_tx_faults(baud, phase):
    data = [0x41, 0x42, 0x43, 0x44, 0x45, 0x46]
    # stop bit 0 at character 1, break of 20 bits at 3 (its byte dropped), the rest clean
    c, rx, dec, fired = uart_loop(data, {1: fword(2), 3: fword(3, arg=20)}, baud, phase)
    assert rx == [0x41, 0x42, 0x101, 0x43, 0x00, 0x101, 0x45, 0x46], [hex(x) for x in rx]
    assert [d[3] for d in dec] == [True, False, True, False, True, True] and dec[3][1] == 0
    assert fired == [0] * len(data) and c.mem[MAP["FAULT_W"]] == 0
    assert c.eng[0].late == 0 and c.eng[1].ovr == 0


@pytest.mark.parametrize("parity", [1, 2])
def test_uart_tx_parity_fault(parity):
    data = [0x11, 0x22, 0x33, 0x00]
    c, rx, dec, fired = uart_loop(data, {2: fword(1)}, PARITY=parity)
    assert rx == [0x11, 0x22, 0x33, 0x102, 0x00], [hex(x) for x in rx]
    assert [d[2] for d in dec] == [True, True, False, True] and [d[1] for d in dec] == data
    assert all(d[3] for d in dec) and c.eng[0].late == 0


def test_uart_tx_fault_fires_once_and_unarmed_ignored():
    # a word without ARMED does nothing; an armed word fires on the next character only
    data = [0x61, 0x62, 0x63]
    c, rx, dec, fired = uart_loop(data, {0: fword(2) & ~ARMED, 1: fword(2)})
    assert rx == [0x61, 0x62, 0x101, 0x63] and fired == [fword(2) & ~ARMED, 0, 0]


# ---- i2c_tgt against the clocked controller model (writes to 0x42)
def i2c_fault(script, word, slot=0):
    c = boot(prog("i2c_tgt", FAULTS=1), slot)
    c.mem[MAP["FAULT_W"]] = word                          # written before the first transfer
    m = I2cControllerModel(script, 60, 65)
    mon, hist, drv = i2c_run(c, m, 60000, 0, done=m.done)
    return c, m, hist, drv


def wr(*data):
    return [("S",), ("W", 0x84)] + [("W", d) for d in data] + [("P",)]


def scl_lows(hist):
    out, lo = [], None
    for t, scl, sda in hist:
        if not scl and lo is None:
            lo = t
        elif scl and lo is not None:
            out.append(t - lo)
            lo = None
    return out


@pytest.mark.parametrize("slot", [0, 3])
def test_i2c_nack_at_position(slot):
    c, m, hist, drv = i2c_fault(wr(0x11, 0x22, 0x33), fword(1, pos=2, arg=1), slot)
    assert m.acks == [0, 0, 1, 0] and c.host_bytes(0) == [0x84, 0x11, 0x22, 0x33]
    assert c.mem[MAP["FAULT_W"]] == 0 and m.errs == []


def test_i2c_nack_count_and_unarmed():
    c, m, hist, drv = i2c_fault(wr(0x11) + wr(0x22) + wr(0x33), fword(1, pos=0, arg=3))
    assert m.acks == [1, 1, 1, 0, 0, 0] and c.mem[MAP["FAULT_W"]] == 0
    c, m, hist, drv = i2c_fault(wr(0x11), fword(1) & ~ARMED)
    assert m.acks == [0, 0] and c.mem[MAP["FAULT_W"]] == fword(1) & ~ARMED


@pytest.mark.parametrize("slot", [1, 2])
def test_i2c_ignore_address(slot):
    c, m, hist, drv = i2c_fault(wr(0x11) + wr(0x22), fword(3), slot)
    assert m.acks == [1, 1, 0, 0] and c.host_bytes(0) == [0x84, 0x22] and c.mem[MAP["FAULT_W"]] == 0


def test_i2c_extra_stretch():
    base = scl_lows(i2c_fault(wr(0x11, 0x22), 0)[2])
    c, m, hist, drv = i2c_fault(wr(0x11, 0x22), fword(2, pos=1, arg=100))
    lows = scl_lows(hist)
    assert m.acks == [0, 0, 0] and c.host_bytes(0) == [0x84, 0x11, 0x22] and len(lows) == len(base)
    extra = [b - a for a, b in zip(base, lows)]
    k = max(range(len(extra)), key=lambda i: extra[i])
    assert 1600 <= extra[k] <= 1600 + 80, extra                                   # + the decode (< 1.6 us)
    assert all(0 <= x <= 60 for i, x in enumerate(extra) if i != k), extra      # a counted-down slot: + its st
    assert c.mem[MAP["FAULT_W"]] == 0


def test_i2c_stuck_sda():
    c, m, hist, drv = i2c_fault(wr(0x11, 0x5A) + wr(0x33), fword(4, pos=1, arg=50))
    held, lo = [], None                                                           # target SDA low intervals
    for t, cs, cd in drv:
        if cd and lo is None:
            lo = t
        elif not cd and lo is not None:
            held.append(t - lo)
            lo = None
    assert max(held) >= 800, held
    assert m.errs and all(e[0] == 2 for e in m.errs), m.errs                       # only 0x5A runs into it
    assert m.acks[:3] == [0, 0, 1] and m.acks[3:] == [0, 0]                       # then idle; next transfer fine
    assert c.host_bytes(0)[-2:] == [0x84, 0x33] and c.mem[MAP["FAULT_W"]] == 0
