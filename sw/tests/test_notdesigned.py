# ws2812.s and manchester.s (programs not designed in) against independent decoders (W1, W2, M1-M4 subset).
import random
import pytest
import os
from harness import prog, boot, simulate, changes, assemble_text, PROG
from models import ws2812_decode, ManchesterSender, manchester_decode


def ws_run(data, slot=0, pause_at=None, pause=0, **defs):
    img = prog("ws2812", **defs)
    c = boot(img, slot)
    simulate(c, 25)
    third = int(defs.get("THIRD", 21))
    if pause_at is None:
        c.host_in[0] += data
        simulate(c, third * 3 * 8 * (len(data) + 2))
    else:
        c.host_in[0] += data[:pause_at]
        simulate(c, third * 3 * 8 * pause_at + pause)
        c.host_in[0] += data[pause_at:]
        simulate(c, third * 3 * 8 * (len(data) - pause_at + 2))
    return c, changes(c, lambda uo, a, b: (uo >> 2) & 1)


@pytest.mark.parametrize("third,slot", [(21, 0), (20, 3)])
def test_ws2812_frame(third, slot):
    rnd = random.Random(7)
    data = [0x00, 0xFF] + [rnd.randrange(256) for _ in range(22)]       # 8 LEDs
    c, ch = ws_run(data, slot, THIRD=third)
    got, pulses = ws2812_decode(ch, 1.5 * third)
    assert got == data
    for t, hi, lo in pulses[:-1]:
        assert hi in (third, 2 * third) and hi + lo == 3 * third, (t, hi, lo)
    assert ch[-1][1] == 0 and c.eng[0].late == 0 and c.eng[0].ovr == 0


def test_ws2812_pause():
    data = [0x81, 0x42, 0x24, 0x18, 0xFF, 0x00]
    c, ch = ws_run(data, 1, pause_at=3, pause=2500)
    got, pulses = ws2812_decode(ch, 31)
    assert got == data
    assert min(hi for _, hi, _ in pulses) >= 21
    gaps = [lo for _, _, lo in pulses[:-1] if lo > 63]
    assert len(gaps) == 1 and gaps[0] > 2000          # line low during the pause, resumed at a byte boundary


def mtx_run(data, bit, slot):
    img = prog("manchester", BIT=bit)
    c = boot(img, slot)
    simulate(c, 25)
    c.tq = c.cycle
    c.host_in[0] += data
    simulate(c, bit * 8 * (len(data) + 3))
    return c, changes(c, lambda uo, a, b: (uo >> 3) & 1)


@pytest.mark.parametrize("bit,slot,data", [(50, 0, [0x55, 0xD5, 0x12, 0x34]), (22, 2, [0xAA, 0xD5, 0xF0]),
                                           (22, 1, [0x80])])
def test_manchester_tx(bit, slot, data):
    c, ch = mtx_run(data, bit, slot)
    first_bit = data[0] >> 7
    t_start = ch[0][0] - (bit / 2 if first_bit else 0)
    n = 8 * len(data)
    got = manchester_decode(ch, bit, t_start, n)
    assert got == [(b >> (7 - i)) & 1 for b in data for i in range(8)]
    mids = [t_start + k * bit + bit / 2 for k in range(n)]
    assert all(any(t == m for t, v in ch) for m in mids)      # every mid-bit edge exactly on the grid
    assert ch[-1][1] == 0 and ch[-1][0] <= t_start + n * bit
    assert not [f for f in c.eng[0].fires if f[2] and f[0] > c.tq]   # startup stale tick at idle is harmless


def mrx_run(frames, bit, slot, rate=0.0, jitter=0.0, gap_bits=4, drop=None, **defs):
    img = prog("manchester", BIT=bit, **defs)
    c = boot(img, slot)
    rnd = random.Random(3)
    senders, t = [], c.cycle + 30
    for f in frames:
        s = ManchesterSender(bit * (1 + rate), f, t0=t, jitter=(lambda k: rnd.uniform(-jitter, jitter)) if jitter
                             else None)
        if drop is not None and not senders:
            s.edges.pop(2 * drop + 1)                  # first frame: bit `drop` without its mid-bit edge
        senders.append(s)
        t = s.end + gap_bits * bit

    def inputs(tt, ch):
        for s in senders:
            if tt < s.end + 1:
                return s.level(tt) << 4 | 8, 0
        return 8, 0
    simulate(c, int(t) + 4 * bit, inputs)
    return c, [v for _, v in c.host_out[1]]


@pytest.mark.parametrize("bit,slot,pre,rate", [(50, 0, 0x55, 0.02), (50, 1, 0xAA, -0.02), (22, 2, 0x55, 0.0),
                                               (18, 3, 0xAA, 0.02)])
def test_manchester_rx(bit, slot, pre, rate):
    payload = [0x12, 0xA5, 0xFF, 0x00, 0xD5]
    c, out = mrx_run([[pre, pre, 0xD5] + payload], bit, slot, rate, jitter=(bit / 4 - 2) / 2)
    assert out == payload + [0x104], [hex(x) for x in out]


@pytest.mark.parametrize("cv", [0, 1])
def test_manchester_rx_errors(cv):
    # M3: no delimiter -> only event 4 (CVCHK 0) / no event (CVCHK 1, D099); two frames 3 bits apart -> both complete
    c, out = mrx_run([[0x55, 0x55, 0x55]], 50, 0, CVCHK=cv)
    assert out == ([] if cv else [0x104])
    c, out = mrx_run([[0x55, 0xD5, 0x01], [0x55, 0xD5, 0x02, 0x03]], 50, 1, gap_bits=3, CVCHK=cv)
    assert out == [0x01, 0x104, 0x02, 0x03, 0x104]


@pytest.mark.parametrize("bit,cv", [(50, 1), (18, 1), (50, 0)])
def test_manchester_rx_missing_transition(bit, cv):
    # M3 missing mid-bit transition at every data bit (D099): CVCHK 1 ends the frame there (bytes before intact,
    # a byte whose last bit is hit is complete), CVCHK 0 <= 4 junk bytes and <= 2 event 4; next frame complete
    q = [0x12, 0xA5, 0x3C, 0x00]
    for k in range(16, 48):
        c, out = mrx_run([[0x55, 0xD5] + q, [0x55, 0xD5, 0x77]], bit, k % 4, drop=k, CVCHK=cv)
        good = (k - 16 + 1) // 8 if cv else (k - 16) // 8
        assert out[:good] == q[:good] and out[-2:] == [0x77, 0x104], (k, out)
        bad = out[good:-2]
        if cv:
            assert bad == [0x104], (k, out)
        else:
            assert bad[-1] == 0x104 and bad.count(0x104) <= 2 and len(bad) - bad.count(0x104) <= 4, (k, out)


def test_manchester_loopback():
    # M4: E0 TX on uo_out[3] -> E1 RX on ui_in[4], both polarities (inv on the TX pad + bind invert on RX)
    src = open(os.path.join(PROG, "manchester.s")).read()
    for inv in (0, 1):
        if inv:
            src = src.replace(".pad uo3, p0", ".pad uo3, p0, inv").replace("li    R0, #4  ", "li    R0, #0x24")
        img = assemble_text(src, name="manchester.s")
        c = boot(img, 0)
        loop = lambda t, ch: ((((ch.uo >> 3) & 1) << 4) | 8, 0)
        simulate(c, 25 + 2 * 50, loop)           # >= 2 bits after the pad-enable edge (header, D053)
        c.host_in[0] += [0x55, 0x55, 0xD5, 0xC3, 0x3C]
        simulate(c, 50 * 8 * 9, loop)
        out = [v for _, v in c.host_out[1]]
        # CVCHK 1: the enable edge of the inverted line is a silent hunting timeout (no empty frame, D099)
        assert out == [0xC3, 0x3C, 0x104]
