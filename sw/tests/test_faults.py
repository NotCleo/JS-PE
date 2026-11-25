# Fault-injection variants (docs/spec/programs/fault_design.md, EI1 at the ISS level): each fault is reported
# once, at its position, by the partner receiver/model; default images unchanged.
import hashlib
import os
import re
import pytest
from harness import PROG, prog, assemble, boot, simulate, changes
from models import uart_decode

MAP = {k: int(v) for k, v in re.findall(r"^\.equ\s+(\w+),\s*(\d+)", open(os.path.join(PROG, "cleo_mem.inc")).read(), re.M)}
ARMED = 0x8000


def fword(typ, pos=0, arg=0):
    return ARMED | typ << 12 | pos << 8 | arg


def digest(img):
    return hashlib.sha1(repr(sorted(img.words.items())).encode()).hexdigest()[:12]


@pytest.mark.parametrize("name,sha", [("uart_tx", "5d021c0da04e")])
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
