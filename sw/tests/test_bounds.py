# T5 (D054): assembler bound >= ISS-measured arming time for every .deadline path,
# slot maps 0xE4, 0x00, 0x44, 0x24 x 4 slot phases x 4 partner phases.
import os
import pytest
from harness import PROG, assemble_text, boot, simulate, deadline_delays, record
from models import UartSender, ManchesterSender
from test_first import run_spi_tgt

MAPS = (0xE4, 0x00, 0x44, 0x24)
PH = (0, 1, 2, 3)


def build(name, m, **defs):
    # the slot map is a source directive: append it so the bound is computed for this map
    src = open(os.path.join(PROG, name + ".s")).read()
    src += "\n.slots %s\n" % ", ".join(str((m >> (2 * s)) & 3) for s in range(4))
    return assemble_text(src, {k: str(v) for k, v in defs.items()}, name=os.path.join(PROG, name + ".s"))


def sc_uart_rx(img, ph, pp, eng):
    c = boot(img, ph)
    data = [0x55, 0x00, 0xFF, 0xA3, 0x3C] * 2
    s = UartSender(15, data, t0=c.cycle + 20 + pp * 0.25)
    simulate(c, int(s.end) + 60, lambda t, ch: (s.level(t) << 7 | 8, 0))
    assert c.host_bytes(eng) == data
    return c


def sc_uart_tx(img, ph, pp, eng):
    c = boot(img, ph)
    simulate(c, 30 + pp)
    data = [0x55, 0xA3, 0x00, 0xFF, 0x81]
    c.host_in[eng] += data
    simulate(c, 11 * 10 * (len(data) + 2))
    return c


def sc_spi_tgt(img, ph, pp, eng):
    c, ctrls, got, _ = run_spi_tgt(img, 25, [0, 1, 3], 3, ph, phase=pp * 0.25)
    tab = [0x00A5, 0x005A, 1, 2, 3, 4, 5, 6]
    assert got == [[0] + tab[cm:cm + 3] for cm in (0, 1, 3)]
    return c


def sc_ws2812(img, ph, pp, eng):
    c = boot(img, ph)
    simulate(c, 30 + pp)
    c.host_in[eng] += [0x00, 0xFF, 0x5A, 0x81]
    simulate(c, 63 * 8 * 5)
    return c


def sc_manchester(bit):
    def sc(img, ph, pp, eng, et=0, er=1):
        c = boot(img, ph)
        simulate(c, 30)
        payload = [0x55, 0x55, 0xD5, 0x12, 0xA5]
        s = ManchesterSender(bit, payload, t0=c.cycle + 30 + pp * 0.25)
        c.host_in[et] += [0x55, 0xD5, 0x3C]
        simulate(c, int(s.end) + 6 * bit, lambda t, ch: (s.level(t) << 4 | 8, 0))
        if er is not None:
            assert c.host_bytes(er) == payload[3:]
        return c
    return sc


def sc_jtag_ctrl(img, ph, pp, eng):
    from test_jtag import scenario
    return scenario(img, ph, pp)


CASES = [("uart_rx", {"BIT": 15}, sc_uart_rx, "ENG"), ("uart_tx", {"BIT": 11}, sc_uart_tx, "ENG"),
         ("spi_tgt", {}, sc_spi_tgt, "ENG"), ("ws2812", {}, sc_ws2812, "ENG"),
         ("manchester", {"BIT": 50}, sc_manchester(50), None), ("manchester", {"BIT": 22}, sc_manchester(22), None),
         ("jtag_ctrl", {}, sc_jtag_ctrl, "ENG")]


@pytest.mark.parametrize("name,defs,sc,engdef", CASES, ids=[c[0] + str(c[1].get("BIT", "")) for c in CASES])
def test_t5_bound_covers_measured(name, defs, sc, engdef):
    rows, bad = [], []
    for m in MAPS:
        if engdef:
            runs = [(build(name, m, **dict(defs, **{engdef: 0})), 0, {})]
        elif m == 0x00:                                # one engine has slots: measure TX and RX in turn
            runs = [(build(name, m, ENG_TX=0, ENG_RX=1, **defs), 0, {"et": 0, "er": None}),
                    (build(name, m, ENG_TX=1, ENG_RX=0, **defs), 0, {"et": 1, "er": 0})]
        else:
            runs = [(build(name, m, **defs), 0, {})]
        for img, eng, kw in runs:
            live = {0x00: {0}}.get(m)
            chips = [sc(img, ph, pp, eng, **kw) for ph in PH for pp in PH]
            for d in img.deadlines:
                if live is not None and d["engine"] not in live:
                    continue
                ms = [x for c in chips for x in deadline_delays(c, d)]
                ok = bool(ms) and max(ms) <= d["bound"]
                rows.append((m, d["line"], d["bound"], max(ms) if ms else None))
                if not ok:
                    bad.append(rows[-1])
    record("T5 %s %s: bound >= measured, 4 maps x 16 phases" % (name, defs), not bad,
           " ".join("%02X:L%d %s/%s" % r for r in rows))
    assert not bad, bad
