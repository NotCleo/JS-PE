# uart_tx.s and uart_rx.s against the independent UART models (U1-U6 subset at the ISS level).
import pytest
from harness import prog, boot, simulate, changes
from models import UartSender, uart_decode


def tx_run(data, slot=0, **defs):
    img = prog("uart_tx", **defs)
    c = boot(img, slot)
    simulate(c, 25)
    c.host_in[0] += list(data)
    return img, c


@pytest.mark.parametrize("baud,slot", [(1000000, 0), (115200, 1), (921600, 2), (3000000, 3)])
def test_tx_rates(baud, slot):
    # U1/U3: decoded, back to back; mean bit time within 1 %, every bit within 1 clk of nominal
    nom = 50e6 / baud
    data = [0x55, 0x55, 0x55, 0xA3, 0x00, 0xFF]
    img, c = tx_run(data, slot, BAUD=baud)
    simulate(c, int(nom * 10 * (len(data) + 1)))
    ch = [x for x in changes(c, lambda uo, a, b: uo & 1, 1) if x[0] > 30]
    dec = uart_decode(ch, nom)
    assert [d[1] for d in dec] == data and all(d[3] for d in dec)
    st = [d[0] for d in dec]
    assert all(abs((b - a) / 10 - nom) <= 0.01 * nom for a, b in zip(st, st[1:]))
    edges = [t for t, v in ch if st[0] <= t < st[3]]          # 0x55 frames: an edge at every bit boundary
    assert all(abs((b - a) - nom) < 1.0 for a, b in zip(edges, edges[1:]) if b - a < 1.5 * nom)
    assert c.eng[0].ovr == 0 and c.eng[0].late == 0


@pytest.mark.parametrize("defs,dbits,parity,stop", [
    ({"DBITS": 7, "PARITY": 1, "STOP2": 1}, 7, 1, 2.0),
    ({"DBITS": 5, "PARITY": 2}, 5, 2, 1.0),
    ({"STOP15": 1}, 8, 0, 1.5),
    ({"DBITS": 9, "PARITY": 2}, 9, 2, 1.0),
])
def test_tx_formats(defs, dbits, parity, stop):
    # U2: data bits, parity, stop length between back-to-back characters
    vals = [0x1A5, 0x03C, 0x0FF, 0x100] if dbits == 9 else [0x15, 0x0A, 0x1F, 0x00]
    host = []
    for v in vals:
        host += [v & 0xFF, v >> 8] if dbits == 9 else [v & ((1 << dbits) - 1)]
    img, c = tx_run(host, 0, **defs)
    simulate(c, 50 * 14 * 6)
    ch = [x for x in changes(c, lambda uo, a, b: uo & 1, 1) if x[0] > 30]
    dec = uart_decode(ch, 50, dbits, parity)
    assert [d[1] for d in dec] == [v & ((1 << dbits) - 1) for v in vals]
    assert all(d[2] and d[3] for d in dec)
    st = [d[0] for d in dec]
    frame = (1 + dbits + (1 if parity else 0) + stop) * 50
    gaps = [b - a for a, b in zip(st, st[1:])]
    if dbits == 9:
        assert all(frame <= g <= frame + 15 for g in gaps), gaps
    else:
        assert all(g == frame for g in gaps), gaps


def rx_run(sender, slot=0, extra=200, **defs):
    img = prog("uart_rx", **defs)
    c = boot(img, slot)
    s = sender(c.cycle + 20)
    simulate(c, int(s.end) + extra, lambda t, ch: (s.level(t) << 7 | 8, 0))
    return c, [v for _, v in c.host_out[1]]


@pytest.mark.parametrize("baud,slot", [(1000000, 1), (115200, 2)])
def test_rx_rates(baud, slot):
    nom = 50e6 / baud
    data = [0x00, 0xFF, 0x55, 0xAA, 0x1B, 0x80]
    c, out = rx_run(lambda t0: UartSender(nom * 1.02, data, t0=t0), slot, BAUD=baud)
    assert out == data and c.eng[1].ovr == 0


def test_rx_errors():
    # U6: low stop bit -> byte + event 1; break -> 0x00 + event 1 once; glitch ignored; next character fine
    data = [0x41, 0x42, 0x00, 0x43]
    c, out = rx_run(lambda t0: UartSender(50, data, t0=t0, gap=50, bad_stop=(1,), breaks=(2,)), 0)
    assert out == [0x41, 0x42, 0x101, 0x00, 0x101, 0x43], [hex(x) for x in out]

    class Glitch:
        def __init__(self, t0):
            self.s = UartSender(50, [0x5A], t0=t0 + 100)
            self.t0, self.end = t0, self.s.end

        def level(self, t):
            return 0 if self.t0 <= t < self.t0 + 15 else self.s.level(t)
    c, out = rx_run(Glitch, 2)
    assert out == [0x5A]


def test_rx_parity():
    data = [0x11, 0x22, 0x33]
    c, out = rx_run(lambda t0: UartSender(50, data, parity=1, t0=t0, bad_parity=(1,)), 3, PARITY=1)
    assert out == [0x11, 0x22, 0x102, 0x33], [hex(x) for x in out]
    c, out = rx_run(lambda t0: UartSender(50, [0x1A5, 0x03C], dbits=9, parity=2, t0=t0), 1, PARITY=2, DBITS=9)
    assert out == [0xA5, 0x01, 0x3C, 0x00]


def test_rx_overrun():
    # host not reading: the 5th character finds the FIFO full -> OVR, earlier bytes kept in order
    img = prog("uart_rx")
    c = boot(img, 0)
    c.tx_block[1] = True
    s = UartSender(50, list(range(1, 7)), t0=c.cycle + 20)
    simulate(c, int(s.end) + 100, lambda t, ch: (s.level(t) << 7 | 8, 0))
    assert c.eng[1].ovr == 1 and c.tx[1].q == [1, 2, 3, 4]


# ---------------------------------------------------------------- U9: inverted line, MSB first, 300 baud (D057)
def rev8(v):
    return int("{:08b}".format(v)[::-1], 2)


@pytest.mark.parametrize("inv,msb", [(1, 0), (0, 1), (1, 1)])
def test_u9_tx_inverted_msb(inv, msb):
    nom = 50e6 / 115200
    data = [0x55, 0xA3, 0x01, 0xF0]
    img, c = tx_run(data, 1, BAUD=115200, INV=inv, MSB=msb)
    simulate(c, int(nom * 10 * (len(data) + 1)))
    ch = [(t, v ^ inv) for t, v in changes(c, lambda uo, a, b: uo & 1, 0) if t > 30]
    dec = uart_decode(ch, nom)
    got = [rev8(d[1]) if msb else d[1] for d in dec]
    assert got == data and all(d[3] for d in dec)
    assert (c.uo & 1) == inv ^ 1                                # idle level: low when inverted


@pytest.mark.parametrize("inv,msb", [(1, 0), (0, 1), (1, 1)])
def test_u9_rx_inverted_msb(inv, msb):
    nom = 50e6 / 115200
    data = [0x55, 0xA3, 0x01, 0xF0]
    wire = [rev8(v) if msb else v for v in data]
    img = prog("uart_rx", BAUD=115200, INV=inv, MSB=msb)
    c = boot(img, 2)
    s = UartSender(nom * 1.02, wire, t0=c.cycle + 40)
    simulate(c, int(s.end) + 200, lambda t, ch: ((s.level(t) ^ inv) << 7 | 8, 0))
    assert [v for _, v in c.host_out[1]] == data and c.eng[1].ovr == 0


@pytest.mark.slow
def test_u9_300_baud_tx():
    nom = 50e6 / 300
    img, c = tx_run([0xA5], 3, BAUD=300)
    simulate(c, int(nom * 11))
    ch = [x for x in changes(c, lambda uo, a, b: uo & 1, 1) if x[0] > 25]
    dec = uart_decode(ch, nom)
    assert [d[1] for d in dec] == [0xA5] and dec[0][3]
    st = ch[0][0]
    # each edge within one timer step (16 clk) of nominal, mean bit time exact to the frac resolution
    assert all(abs((t - st) - round((t - st) / nom) * nom) <= 16 for t, v in ch), [(t - st) / nom for t, v in ch]


@pytest.mark.slow
def test_u9_300_baud_rx():
    nom = 50e6 / 300
    img = prog("uart_rx", BAUD=300)
    c = boot(img, 1)
    s = UartSender(nom * 1.02, [0x3C], t0=c.cycle + 40)
    simulate(c, int(s.end) + 200, lambda t, ch: (s.level(t) << 7 | 8, 0))
    assert [v for _, v in c.host_out[1]] == [0x3C] and c.eng[1].ovr == 0
