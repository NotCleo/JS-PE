# EI1 (VERIF_PLAN, fault_design.md): seeded random fault campaigns from host/fault.py; every injected fault is
# reported exactly once, at its transaction and position, by the matching receiver or model; the rest stay clean.
import os
import sys
import pytest
from harness import ROOT, prog, boot, simulate, changes
from models import I2cControllerModel, SpiController, CanNode, can_host_tx
from test_first import Seq
from test_i2c import i2c_run
from test_can import run as can_run, frame_clk, tx_events
from test_faults import MAP, uart_loop, wr, scl_lows, spi_expect, TAB, CS_SETUP

sys.path.insert(0, os.path.join(ROOT, "host"))
import fault                                                     # noqa: E402

SEEDS = [2026, 7, 0xC1E0]


def fields(w):
    return w >> 12 & 7, w >> 8 & 15, w & 0xFF


def test_host_api():
    assert fault.FAULT_W == MAP["FAULT_W"]
    assert fault.word("i2c_tgt", 1, 2, 3) == 0x9203 and fault.check(0) == 0
    for bad in [lambda: fault.word("spi_tgt", 2, 0, 8), lambda: fault.word("uart_tx", 3, 0, 1),
                lambda: fault.word("can", 1, 0, 0), lambda: fault.check(0x10A5)]:
        with pytest.raises(ValueError):
            bad()
    assert fault.campaign(5, "spi_tgt", 20, 6) == fault.campaign(5, "spi_tgt", 20, 6)    # reproducible
    assert fault.campaign(5, "spi_tgt", 20, 6) != fault.campaign(6, "spi_tgt", 20, 6)


@pytest.mark.parametrize("seed", SEEDS)
def test_ei1_uart(seed):
    n = 24
    data = [(seed * 7 + 13 * k) & 0xFF for k in range(n)]
    camp = dict(fault.campaign(seed, "uart_tx", n, 6, arg_range={3: (12, 40)}))
    c, rx, dec, fired = uart_loop(data, camp, PARITY=1)
    want = []
    for k, ch in enumerate(data):
        typ = fields(camp[k])[0] if k in camp else 0
        want += {0: [ch], 1: [ch, 0x102], 2: [ch, 0x101], 3: [0x00, 0x101]}[typ]
    assert rx == want, (seed, camp)
    assert fired == [0] * n and c.eng[0].late == 0


@pytest.mark.parametrize("seed", SEEDS)
def test_ei1_i2c(seed):
    n = 12
    camp = dict(fault.campaign(seed, "i2c_tgt", n, 5, pos_max=1, arg_range={1: (1, 1), 2: (40, 120), 4: (30, 60)}))
    c = boot(prog("i2c_tgt", FAULTS=1), seed % 4)
    m = I2cControllerModel(wr(0xA5, 0xC3), 60, 65)
    base = max(scl_lows(i2c_run(c, m, 60000, 0, done=m.done)[1]))       # a clean transfer first
    for k in range(n):
        if k in camp:
            c.mem[MAP["FAULT_W"]] = camp[k]
        m = I2cControllerModel(wr(0xA5, 0xC3), 60, 65)
        mon, hist, drv = i2c_run(c, m, 60000, 0, done=m.done)
        lows = scl_lows(hist)
        typ, pos, arg = fields(camp[k]) if k in camp else (0, 0, 0)
        acks = {0: [0, 0, 0], 1: [0, 0, 0], 2: [0, 0, 0], 3: [0] * pos + [1] * (3 - pos), 4: [0] * (pos + 1) + [1] * (2 - pos)}
        want = list(acks[typ])
        if typ == 1:
            want[pos] = 1
            if pos == 0:
                want = [1, 1, 1]                                            # NACKed address: idle
        assert m.acks == want, (seed, k, hex(camp.get(k, 0)), m.acks)
        assert (m.errs != []) == (typ == 4) and all(e[0] == pos + 1 for e in m.errs), (k, m.errs)
        if typ == 2:
            assert max(lows) >= base + 16 * arg, (k, max(lows), base)
        assert c.mem[MAP["FAULT_W"]] == 0


@pytest.mark.parametrize("seed", SEEDS)
def test_ei1_spi(seed):
    n = 16
    camp = dict(fault.campaign(seed, "spi_tgt", n, 6, pos_max=4))
    c = boot(prog("spi_tgt", FAULTS=1), seed % 4)
    for k in range(n):
        cm = k % 3
        if k in camp:
            c.mem[MAP["FAULT_W"]] = camp[k]
        x = SpiController(50, 0, [cm] + [0] * 5, t0=c.cycle + 40, cs_setup=CS_SETUP)
        ctrls = [x]

        def inputs(tt, ch):
            s = Seq(ctrls)
            miso = (ch.uio_out >> 2) & (ch.uio_oe >> 2) & 1
            return 8, (s.sck(tt) if tt >= x.cs_lo else 0) << 3 | s.cs(tt) | s.mosi(tt) << 1 | miso << 2
        t0 = c.cycle
        simulate(c, int(x.cs_hi - t0) + 40, inputs)
        miso = [e for e in changes(c, lambda uo, o, oe: (o >> 2) & (oe >> 2) & 1) if e[0] >= t0]
        got = x.sample(miso)
        if k in camp:
            typ, pos, arg = fields(camp[k])
            assert got == spi_expect(cm, 5, pos, typ, arg) != spi_expect(cm, 5), (seed, k, hex(camp[k]), got)
        else:
            assert got == spi_expect(cm, 5), (seed, k, got)
        assert c.mem[MAP["FAULT_W"]] == 0


@pytest.mark.parametrize("seed", SEEDS)
def test_ei1_can(seed):
    n, bit = 10, 100
    camp = dict(fault.campaign(seed, "can", n, 4))
    c, node = boot(prog("can", FAULTS=1, BIT=bit), seed % 4), CanNode(bit)
    can_run(c, node, 12 * bit)
    for k in range(n):
        ident, data = (0x100 + 17 * k) & 0x7FF, [(seed + k) & 0xFF, k]
        if k in camp:
            c.mem[MAP["FAULT_W"]] = camp[k]
        nf, ne = len(node.frames), len(node.errors)
        c.host_in[0] += can_host_tx(ident, data)
        can_run(c, node, frame_clk(data, bit) + 30 * bit)
        if k in camp:
            assert node.errors[ne:] == ["crc"] and len(node.frames) == nf, (seed, k)     # detected, not delivered
            assert tx_events(c)[-1] == 0x104 and c.mem[MAP["FAULT_W"]] == 0
            c.host_in[0] += can_host_tx(ident, data)                                     # host retry
            can_run(c, node, frame_clk(data, bit) + 30 * bit)
        assert node.frames[-1] == (ident, data, 0) and len(node.errors) == ne + (k in camp), (seed, k)
