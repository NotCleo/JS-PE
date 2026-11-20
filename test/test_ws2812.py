# SPDX-FileCopyrightText: © 2026 NotCleo
# SPDX-License-Identifier: Apache-2.0
# WS2812 system tests (VERIF_PLAN 7.6 W1, W2; program not designed in, zero RTL diff): ws2812.s on E0 (uo_out[2])
# loaded through the host link; independent pulse decoder (sw/tests/models.py); memory pre-filled with random words;
# pads == ISS every clock (test_uart Bench). W1 at 256 LEDs also covers W3 at R. GATES=yes: the G subset (8 LEDs).

import os

import cocotb
from cocotb.clock import Clock

from test_uart import Bench, GL, MEM_WORDS, PROG                     # noqa: E402 (sets sys.path)
from models import ws2812_decode                                     # noqa: E402
from jspe_asm import Assembler                                       # noqa: E402

NS = 20.0
ESC = 0x1B
# REQUIREMENTS 4: nominal ns, each +-150 ns
NOM = {"T0H": 400, "T1H": 800, "T0L": 850, "T1L": 450}
TOL = 150
RESET = 280_000 // 20                  # 280 us in clk
ORGS = {"ws2812": 48, "manchester": (64, 96)} if MEM_WORDS == 128 else {}


# ---------------- bench, images
async def nbench(dut, seed, link=2, cls=Bench):
    # memory filled with random words first, as on silicon (an unwritten word fetched would fail the lockstep)
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    b = cls(dut, seed)
    await b.reset()
    await b.sync()
    if link:
        await b.set_link(link)
    b.fill = [b.r.getrandbits(16) for _ in range(MEM_WORDS)]
    for a in range(0, MEM_WORDS, 256):
        await b.wrmem(a, b.fill[a:a + 256])
    return b


def image(name, txt=None, **d):
    # one program file, optionally with edited text; ORG relocated below 128 for the flop build
    txt = txt if txt is not None else open(os.path.join(PROG, name + ".s")).read()
    org = ORGS.get(name)
    if name == "ws2812" and org:
        d.setdefault("ORG", org)
    elif org:
        d.setdefault("ORG_TX", org[0])
        d.setdefault("ORG_RX", org[1])
    return Assembler([(name + ".s", txt)], {"%s.s:%s" % (name, k): str(v) for k, v in d.items()}).run()


async def ws_load(b):
    await b.load(image("ws2812"))
    await b.run(50)
    assert b.ws_lv == 0, "DOUT not low after load"
    b.ws_ch = []


# ---------------- checks
def ws_check(ch, data, gaps=0):
    # decoded bytes; every bit's high / low time in its REQUIREMENTS window; bit period 63 clk across byte
    # boundaries (no underrun) except `gaps` host pauses, each at a byte boundary with the line low throughout
    got, pulses = ws2812_decode(ch, 31.5)
    assert got == data, "decoded %s, sent %s" % ([hex(x) for x in got], [hex(x) for x in data])
    assert len(pulses) == 8 * len(data), (len(pulses), len(data))
    m = {k: [] for k in NOM}
    long_lo = []
    for k, (t, hi, lo) in enumerate(pulses):
        v = 1 if hi > 31.5 else 0
        assert hi == 21 * (1 + v), ("high time %d clk at bit %d (t %d): not on the 21-clk ticks" % (hi, k, t))
        m["T%dH" % v].append(hi * NS)
        if lo is None or k == len(pulses) - 1:
            continue
        if hi + lo != 63:
            assert k % 8 == 7 and lo > 63, ("bit period %d clk at bit %d (t %d): underrun" % (hi + lo, k, t))
            long_lo.append((k, lo))
            continue
        assert lo >= 21, (lo, k, t)
        m["T%dL" % v].append(lo * NS)
    assert len(long_lo) == gaps, ("low gaps inside the frame", long_lo)
    for k, xs in m.items():
        bad = [x for x in xs if abs(x - NOM[k]) > TOL]
        assert not bad, (k, sorted(set(bad)), NOM[k])
    return {k: (min(xs), max(xs)) for k, xs in m.items() if xs}, long_lo


def leds_of(b, n):
    # n LEDs (G, R, B): one all-0, one all-1, ESC bytes, the rest random
    d = [0, 0, 0, 0xFF, 0xFF, 0xFF] + [b.r.getrandbits(8) for _ in range(3 * n - 6)]
    for k in range(7, len(d), 41):
        d[k] = ESC
    return d[:3 * n]


async def ws_frame(b, data, what, before=0):
    # send data; run until its last bit has ended (`before` bytes of the frame already on the line)
    b.send(0, data)
    n = before + len(data)
    await b.run(64 * 8 * len(data) + 10 ** 4, lambda: len(b.ws_ch) >= 16 * n, what)


# ---------------- W1: 64 LEDs, timing, reset, second frame after the reset
@cocotb.test()
async def test_w1_stream(dut):
    # link at reset rate B = 16 (slowest: 160 clk per byte, 320 for an escaped ESC, LED byte 504 clk): the host
    # streams the frame behind H_RDY with no gap; then the line stays low >= 280 us; a second frame follows
    b = await nbench(dut, 81, link=0)
    await ws_load(b)
    n = 8 if GL else 256
    d1 = leds_of(b, n)
    await ws_frame(b, d1, "frame 1")
    end = b.ws_ch[-1][0]
    await b.run(RESET + 100)
    assert b.ws_ch[-1][0] == end and b.ws_lv == 0, "line not low for the reset time"
    m, _ = ws_check(b.ws_ch, d1)
    cocotb.log.info("W1 %d LEDs at link B = 16: ns (min, max) %s; reset low >= %d clk", n, m, b.now - end)
    d2 = leds_of(b, 2 if GL else 8)
    b.ws_ch = []
    await ws_frame(b, d2, "frame 2")
    await b.run(100)
    ws_check(b.ws_ch, d2)
    assert b.ws_ch[0][0] - end >= RESET
    assert b.notes() == [] and not b.chip.eng[0].ovr


# ---------------- W2: host pauses inside a frame
@cocotb.test()
async def test_w2_pauses(dut):
    # pauses of 10 us and 100 us after bytes 5 and 11: line low throughout, resumes on the byte boundary with a full
    # first pulse (fresh tick grid), no pulse shorter than 21 clk
    b = await nbench(dut, 82)
    await ws_load(b)
    d = leds_of(b, 6)
    sent = 0
    for upto, pause in ((5, 500), (11, 5000)):
        b.send(0, d[sent:upto])
        sent = upto
        await b.run(10 ** 5, lambda: len(b.ws_ch) >= 16 * sent, "bytes before the pause")
        await b.run(pause)
    await ws_frame(b, d[sent:], "rest of the frame", sent)
    await b.run(100)
    m, gaps = ws_check(b.ws_ch, d, gaps=2)
    assert [k for k, _ in gaps] == [39, 87], gaps
    assert gaps[0][1] >= 500 and gaps[1][1] >= 5000, gaps
    assert min(hi for _, hi, _ in ws2812_decode(b.ws_ch, 31.5)[1]) >= 21
    assert b.notes() == []
    cocotb.log.info("W2: gaps (bit, clk) %s; ns %s", gaps, m)
