# MON1 (VERIF_PLAN 8, monitor_design.md) at the ISS level: a monitor watches a live bus between two other engines (or
# an engine and a model) and host/monitor.py reproduces the traffic exactly; the bus is identical without the monitor.
import os
import sys
from harness import ROOT, PROG, assemble, boot, simulate
from models import UartSender, I2cTargetModel, pad_levels
from test_i2c import i2c_run

sys.path.insert(0, os.path.join(ROOT, "host"))
import monitor  # noqa: E402


def img(files, **defs):
    # defines for a program that is not loaded are dropped (the run without the monitor)
    names = [f + ".s" for f in files]
    eq = "".join(open(os.path.join(PROG, n)).read() for n in names)
    keep = {k: str(v) for k, v in defs.items()
            if (k.split(":")[0] in names if ":" in k else (".equ " + k + ",") in eq.replace("  ", " ").replace("  ", " "))}
    return assemble([os.path.join(PROG, n) for n in names], keep)


def pads(c):
    return [(t - c.t_run, uo, uout, uoe) for t, uo, uout, uoe in c.pad_trace if t >= c.t_run]


def own_pads(image, engines):
    return {p for e in engines for p in image.engines[e]["pads"]}


# ---- SPI: spi_ctrl (E0) <-> spi_tgt (E1) over uio[3:0]; spi_mon on E2 (MOSI) and E3 (MISO)
SPI = {"spi_tgt.s:ENG": 1, "ENG_MOSI": 2, "ENG_MISO": 3, "BIT": 16}
CMDS = [[0, 0, 0, 0], [3, 0, 0], [7, 0], [1, 0, 0, 0, 0, 0]]


def spi_live(with_mon):
    files = ["spi_ctrl", "spi_tgt"] + (["spi_mon"] if with_mon else [])
    image = img(files, **SPI)
    c = boot(image, 0)
    sent = []
    for k, cm in enumerate(CMDS):
        c.host_in[0] += [len(cm)] + cm
        sent.append(cm)

    def inputs(t, ch):
        return 8, pad_levels(ch.uio_out, ch.uio_oe)
    simulate(c, 9000, inputs)
    return c, image, sent


def test_mon1_spi_between_two_engines():
    c, image, sent = spi_live(True)
    c0, image0, _ = spi_live(False)
    assert pads(c) == pads(c0)                                     # the monitor changes nothing on the bus
    assert own_pads(image, (2, 3)) == set()
    got = c.host_bytes(0)                                          # what spi_ctrl read on MISO
    fr = monitor.spi(monitor.iss_entries(c, 2), monitor.iss_entries(c, 3))
    assert [f["mosi"] for f in fr] == sent
    k = 0
    for f, cm in zip(fr, sent):
        assert f["miso"] == got[k:k + len(cm)] and not f["partial"] and not f["lost"]
        k += len(cm)
    assert monitor.log_spi(fr)[0] == "CS 00/00 00/a5 00/5a 00/01"


# ---- UART: uart_tx (E2) -> uo_out[0] -> ui_in[7] -> uart_rx (E3); uart_mon (E0 on ui_in[7], E1 on ui_in[6] from a
# model sending the other direction)
def uart_live(with_mon, chars, other):
    files = ["uart_tx", "uart_rx"] + (["uart_mon"] if with_mon else [])
    image = img(files, **{"uart_tx.s:ENG": 2, "uart_tx.s:ORG": 64, "uart_rx.s:ENG": 3, "BAUD": 1000000})
    c = boot(image, 1)
    c.host_in[2] += chars
    b = UartSender(50, other, t0=c.cycle + 300.4)

    def inputs(t, ch):
        return 0x08 | (ch.uo & 1) << 7 | b.level(t) << 6, 0
    simulate(c, int(b.end - c.cycle) + 600 + 520 * len(chars), inputs)
    return c, image


def test_mon1_uart_both_directions():
    chars = [0x55, 0x00, 0xFF, 0x1B, 0x7E] + list(range(0x30, 0x40))
    other = [0xA5, 0x5A, 0x0D, 0x0A] * 4
    c, image = uart_live(True, chars, other)
    c0, _ = uart_live(False, chars, other)
    assert pads(c) == pads(c0)
    assert own_pads(image, (0, 1)) == set()
    assert c.host_bytes(3) == chars                                # the real receiver
    a = monitor.uart(monitor.iss_entries(c, 0))
    b = monitor.uart(monitor.iss_entries(c, 1))
    assert [x[1] for x in a] == chars and [x[1] for x in b] == other
    assert not any(x[2] for x in a + b)
    assert monitor.log_uart(a, b)[0] == "A 55"


# ---- I2C: i2c_ctrl (E0, open-drain uio[6:5]) <-> target model; i2c_mon on E1
START, BYTE, REL, STOP = 1, 2, 4, 8


def i2c_live(with_mon):
    files = ["i2c_ctrl"] + (["i2c_mon"] if with_mon else [])
    image = img(files, **{"i2c_mon.s:ENG": 1})
    c = boot(image, 2)
    steps = [(START | BYTE | REL, 0xA0), (BYTE | REL, 0x11), (BYTE | REL | STOP, 0x22),
             (START | BYTE | REL, 0xA0), (BYTE | REL, 0x07), (START | BYTE | REL, 0xA1), (BYTE, 0xFF),
             (BYTE | REL | STOP, 0xFF), (START | BYTE | REL | STOP, 0xB4)]
    for fl, d in steps:
        c.host_in[0] += [fl, d]
    tgt = I2cTargetModel(0x50, tx=[0x9C, 0x42])
    mon, hist, drv = i2c_run(c, tgt, 80000, tail=300,
                             done=lambda: not c.host_in[0] and c.eng[0].waiting and c.cycle > 30000)
    return c, image, tgt, [(t - c.t_run, a, b) for t, a, b in hist if t >= c.t_run]


def test_mon1_i2c_controller_and_target():
    c, image, tgt, hist = i2c_live(True)
    c0, _, _, hist0 = i2c_live(False)
    assert hist == hist0 and pads(c) == pads(c0)
    assert own_pads(image, (1,)) == set()
    tok = monitor.i2c(monitor.iss_entries(c, 1))
    assert tok == [("S",), ("A", 0x50, 0, 1), ("D", 0x11, 1), ("D", 0x22, 1), ("P",),
                   ("S",), ("A", 0x50, 0, 1), ("D", 0x07, 1), ("Sr",), ("A", 0x50, 1, 1), ("D", 0x9C, 1),
                   ("D", 0x42, 0), ("P",), ("S",), ("A", 0x5A, 0, 0), ("P",)]
    assert tgt.rx == [0x11, 0x22, 0x07]
    assert monitor.log_i2c(tok)[0] == "[ 50W 11 22 ]"
