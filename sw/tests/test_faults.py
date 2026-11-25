# Fault-injection variants (docs/spec/programs/fault_design.md, EI1 at the ISS level): each fault is reported
# once, at its position, by the partner receiver/model; default images unchanged.
import hashlib
import os
import re
import pytest
from harness import PROG, prog, assemble, boot, simulate, changes
from models import uart_decode
from models import I2cControllerModel, i2c_timing, i2c_violations
from test_i2c import i2c_run
from models import SpiController
from test_first import Seq

MAP = {k: int(v) for k, v in re.findall(r"^\.equ\s+(\w+),\s*(\d+)", open(os.path.join(PROG, "cleo_mem.inc")).read(), re.M)}
ARMED = 0x8000


def fword(typ, pos=0, arg=0):
    return ARMED | typ << 12 | pos << 8 | arg


def digest(img):
    # memory image, .init lists, entries, pads, SLOTMAP, FILT (digests of the sources before the fault variants)
    eng = [(e, [w for w, _, _ in d["init"]], d["entry"], sorted(d["pads"].items())) for e, d in sorted(img.engines.items())]
    return hashlib.sha1(repr((sorted(img.words.items()), eng, img.slotmap, img.filt)).encode()).hexdigest()[:12]


@pytest.mark.parametrize("name,sha", [("uart_tx", "4a5f676c239e"), ("i2c_tgt", "e58566083db3"), ("spi_tgt", "150e13190d62"),
                                      ("can", "ba9003c5e793")])
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
def i2c_fault(script, word, slot=0, thigh=60, tlow=65, rise=0, host_tx=()):
    c = boot(prog("i2c_tgt", FAULTS=1), slot)
    c.mem[MAP["FAULT_W"]] = word                          # written before the first transfer
    c.host_in[0] += list(host_tx)
    m = I2cControllerModel(script, thigh, tlow)
    mon, hist, drv = i2c_run(c, m, 200000, rise, done=m.done)
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
    # a NACKed address returns to idle (its data byte is NACKed too, nothing pushed): slots 0-2 = the 3 addresses
    c, m, hist, drv = i2c_fault(wr(0x11) + wr(0x22) + wr(0x33) + wr(0x44), fword(1, pos=0, arg=3))
    assert m.acks == [1, 1, 1, 1, 1, 1, 0, 0] and c.host_bytes(0) == [0x84, 0x44] and c.mem[MAP["FAULT_W"]] == 0
    c, m, hist, drv = i2c_fault(wr(0x11), fword(1) & ~ARMED)
    assert m.acks == [0, 0] and c.mem[MAP["FAULT_W"]] == fword(1) & ~ARMED


@pytest.mark.parametrize("slot", [0, 3])
def test_i2c_nack_read_address_then_write(slot):
    # NACK at a read's address: the target goes idle (no SCL hold, no transmit); the next write works
    script = [("S",), ("W", 0x85), ("P",)] + wr(0x11)
    c, m, hist, drv = i2c_fault(script, fword(1, arg=1), slot, host_tx=[0x5A])
    assert m.acks == [1, 0, 0] and m.errs == [] and c.host_bytes(0) == [0x84, 0x11] and c.mem[MAP["FAULT_W"]] == 0


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


@pytest.mark.parametrize("mode,thigh,tlow,rise", [("Fm", 30, 65, 15), ("Fm", 60, 65, 0), ("Sm", 250, 250, 50)])
@pytest.mark.parametrize("word", [0, fword(1, pos=9)])           # unarmed; armed but only counting down
def test_i2c_faults_build_um10204(mode, thigh, tlow, rise, word):
    # the per-ACK check only stretches SCL: every transfer stays UM10204-compliant (tVD;DAT waived on stretched lows)
    script = wr(0x11, 0x22) + [("S",), ("W", 0x85), ("R", 0), ("R", 1), ("P",)]
    for ph in range(4):
        c, m, hist, drv = i2c_fault(script, word, ph, thigh, tlow, rise, host_tx=[0xA5, 0x3C])
        assert m.acks == [0, 0, 0, 0] and m.read == [0xA5, 0x3C] and m.errs == []
        assert c.host_bytes(0) == [0x84, 0x11, 0x22, 0x85]
        assert not i2c_violations(i2c_timing(hist, low_max=tlow + 5, rise=rise), mode), (ph, mode)
    lows = scl_lows(hist)
    base = sorted(lows)[len(lows) // 2]                                # an unstretched low phase
    extra = [x - base for x in lows if x > base + 5]
    assert len(extra) <= 4 and max(extra, default=0) <= (20 if word == 0 else 75), (base, extra)  # ACK slots only


# ---- spi_tgt against the SPI controller model (register device: command, then table[cmd], table[cmd + 1], ...)
TAB = [0xA5, 0x5A, 1, 2, 3, 4, 5, 6]
CS_SETUP = 160                         # FAULTS rule: first sampling edge >= 160 clk after CS falls, SCK <= 1 MHz


def spi_fault(frames, word, T=50, mode=0, slot=0, cs_setup=CS_SETUP):
    c = boot(prog("spi_tgt", FAULTS=1), slot)
    c.mem[MAP["FAULT_W"]] = word
    ctrls, t = [], c.cycle + 40
    for cm, n in frames:
        x = SpiController(T, mode, [cm] + [0] * n, t0=t, cs_setup=cs_setup)
        ctrls.append(x)
        t = x.cs_hi + 40
    cpol = mode >> 1

    def inputs(tt, ch):
        x = Seq(ctrls)
        miso = (ch.uio_out >> 2) & (ch.uio_oe >> 2) & 1
        sck = x.sck(tt) if tt >= ctrls[0].cs_lo else cpol
        return 8, sck << 3 | x.cs(tt) | x.mosi(tt) << 1 | miso << 2
    simulate(c, int(ctrls[-1].cs_hi) + 40, inputs)
    miso = changes(c, lambda uo, o, oe: (o >> 2) & (oe >> 2) & 1)
    return c, [x.sample(miso) for x in ctrls]


def spi_expect(cm, n, pos=None, typ=0, arg=0):
    # target bit stream: 0x00 during the command, then the responses; word pos changed by the fault
    words = [(0, 8)]
    for i in range(min(n + 1, len(TAB) - cm)):                        # one more word: a short word pulls it in
        v, w = TAB[cm + i], 8
        if i == pos:
            v, w = (v ^ (arg & 0xFF), 8) if typ == 1 else (v, 8 - arg if typ == 2 else 8 + arg)
        words.append((v, w))
    bits = [(v >> (w - 1 - k)) & 1 for v, w in words for k in range(w)]
    return [sum(b << (7 - k) for k, b in enumerate(bits[8 * j:8 * j + 8])) for j in range(n + 1)]


@pytest.mark.parametrize("slot", [0, 1, 2, 3])
@pytest.mark.parametrize("mode", [0, 3])
def test_spi_bit_flip(mode, slot):
    c, got = spi_fault([(1, 5), (1, 5)], fword(1, pos=2, arg=0x81), mode=mode, slot=slot)
    assert got[0] == spi_expect(1, 5, 2, 1, 0x81) and got[1] == spi_expect(1, 5), got
    assert c.mem[MAP["FAULT_W"]] == 0 and c.eng[0].ovr == 0


@pytest.mark.parametrize("slot", [0, 3])
@pytest.mark.parametrize("typ,arg", [(2, 3), (2, 1), (2, 7), (3, 2), (3, 7), (3, 8)])
def test_spi_short_long_word(typ, arg, slot):
    c, got = spi_fault([(0, 6), (2, 4)], fword(typ, pos=1, arg=arg), slot=slot)
    assert got[0] == spi_expect(0, 6, 1, typ, arg) and got[1] == spi_expect(2, 4), got
    assert c.mem[MAP["FAULT_W"]] == 0


@pytest.mark.parametrize("typ,pos", [(3, 1), (2, 1), (3, 4), (1, 6)])
def test_spi_fault_cut_by_cs(typ, pos):
    # the fault byte is the last clocked one, or beyond the frame: the next frames are clean (SHCFG restored in idle)
    c, got = spi_fault([(0, 2), (1, 3), (2, 3)], fword(typ, pos=pos, arg=4))
    assert got[1:] == [spi_expect(1, 3), spi_expect(2, 3)], got
    assert got[0] == spi_expect(0, 2, pos, typ, 4)[:3] and c.mem[MAP["FAULT_W"]] == 0   # lost if past the frame


def test_spi_unarmed_and_first_byte():
    c, got = spi_fault([(3, 4)], fword(1, arg=0x10) & ~ARMED, cs_setup=60)        # unarmed: shorter setup is enough
    assert got[0] == spi_expect(3, 4)
    c, got = spi_fault([(3, 4)], fword(1, pos=0, arg=0xFF))
    assert got[0] == spi_expect(3, 4, 0, 1, 0xFF)


# ---- can (E_TX 0 + E_RX 1) against the independent CAN node model
def can_fault(frames, word, bit=100, phase=0):
    from models import CanNode, can_host_tx
    from test_can import run, frame_clk, rx_stream, tx_events
    c, node = boot(prog("can", FAULTS=1, BIT=bit), phase), CanNode(bit)
    run(c, node, 12 * bit)
    c.mem[MAP["FAULT_W"]] = word                                     # E_TX = engine 0
    seen = []
    for ident, data in frames:
        c.host_in[0] += can_host_tx(ident, data)
        run(c, node, frame_clk(data, bit) + 30 * bit)
        seen.append((list(node.frames), list(node.errors), c.mem[MAP["FAULT_W"]]))
    return c, node, seen, rx_stream(c), tx_events(c)


@pytest.mark.parametrize("phase", [0, 1, 2, 3])
@pytest.mark.parametrize("bit,arg", [(80, 0x5A), (100, 0x01), (100, 0xA5), (400, 0x80)])   # 625, 500, 125 kbit/s
def test_can_crc_fault_then_clean_retry(bit, arg, phase):
    f = (0x123, [0x00, 0xFF, 0xA5])
    c, node, seen, rx, tx = can_fault([f, f], fword(1, arg=arg), bit, phase)
    frames1, errs1, fw1 = seen[0]
    assert frames1 == [] and errs1 == ["crc"] and fw1 == 0, seen[0]                     # node: CRC error, not delivered
    assert node.frames == [(0x123, [0x00, 0xFF, 0xA5], 0)] and node.errors == ["crc"]    # host retry: delivered once
    assert tx == [0x104, 0x101]                     # no ACK from a CRC-error receiver: E_TX ACK error, then sent
    assert [v for v in rx if v & 0x100] == [0x101, 0x101]          # own E_RX shares the corrupted LFSR: OK
    assert c.eng[0].ovr == 0 and c.eng[1].ovr == 0


@pytest.mark.parametrize("word", [0, 0x10A5, 0x00FF, 0x7FFF])            # 0 and non-zero unarmed words
def test_can_unarmed_frames_clean(word):
    f = (0x7F0, [0x55])
    c, node, seen, rx, tx = can_fault([f, f], word)
    assert node.frames == [(0x7F0, [0x55], 0)] * 2 and node.errors == [] and tx == [0x101, 0x101]
    assert c.mem[MAP["FAULT_W"]] == word                             # left as written


def test_two_faulted_engines():
    # per-engine fault words: i2c_tgt FAULTS on E0 and uart_tx FAULTS on E2 in one image, each fires in its protocol
    img = assemble([os.path.join(PROG, "i2c_tgt.s"), os.path.join(PROG, "uart_tx.s")],
                   {"FAULTS": "1", "uart_tx.s:ENG": "2"})
    c = boot(img, 1)
    c.mem[MAP["FAULT_W"] + 0] = fword(1, pos=1, arg=1)                 # i2c: NACK the first data byte
    c.mem[MAP["FAULT_W"] + 2] = fword(2)                               # uart: low stop bit on the next character
    c.host_in[2] += [0x41, 0x42]
    m = I2cControllerModel(wr(0x11, 0x22), 60, 65)
    i2c_run(c, m, 60000, 0, done=m.done)
    simulate(c, 3000)
    dec = uart_decode(changes(c, lambda uo, a, b: uo & 1, 1), 50)
    assert m.acks == [0, 1, 0] and [d[1] for d in dec] == [0x41, 0x42] and [d[3] for d in dec] == [False, True]
    assert c.mem[MAP["FAULT_W"]] == 0 and c.mem[MAP["FAULT_W"] + 2] == 0
