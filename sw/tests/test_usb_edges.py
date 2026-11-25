# usb_ls.s with slow LS edges (75-300 ns by spec): crossover SE0 at random transitions, delayed read-back, EOP confirm.
import random
import pytest
from models import USB_J, USB_K, USB_SE0, USB_PID, UsbEdges, usb_token, usb_data
from test_usb import setup, run, stream, chip_packets, transaction, ADDR, BIT, PAY
from test_usb_tx import arm, in_txn

OUTPAY = [0xF9, 0x00, 0xFF, 0x7E]
INPAY = [0xFF] * 7


def session(c, host, img, view):
    # SETUP + DATA0, OUT DATA1, IN (armed) + host ACK, keep-alive, IN (unarmed): -> list of failures
    bad = []
    log, clash, _ = transaction(c, host, usb_token("SETUP", ADDR, 0), usb_data("DATA0", PAY), view=view)
    if [p[2] for p in chip_packets(log, host)] != [[USB_PID["ACK"]]] or clash:
        bad.append("setup ack")
    log, clash, _ = transaction(c, host, usb_token("OUT", ADDR, 0), usb_data("DATA1", OUTPAY), view=view)
    if [p[2] for p in chip_packets(log, host)] != [[USB_PID["ACK"]]] or clash:
        bad.append("out ack")
    arm(c, img, 0, "DATA1", INPAY)
    log, clash, _ = in_txn(c, host, 0, view=view)
    r = chip_packets(log, host)
    if [p[2] for p in r] != [usb_data("DATA1", INPAY)] or not r[0][3] or clash:
        bad.append("in data")
    transaction(c, host, [USB_PID["ACK"]], after=20, view=view)
    host.keepalive(host.t + 20 * BIT)
    log, clash, _ = in_txn(c, host, 0, after=40, view=view)
    if [p[2] for p in chip_packets(log, host)] != [[USB_PID["NAK"]]] or clash:
        bad.append("nak")
    want = usb_data("DATA0", PAY) + [0x2D, 0x101] + usb_data("DATA1", OUTPAY) + [0xE1, 0x101, 0x103]
    if stream(c) != want:
        bad.append("stream")
    return bad


# edges up to 14 clk late (300 ns), crossover SE0 (rises late) or SE1 (falls late) of random length at every
# transition: 512 words 0-10 clk (210 ns), 128 words 0-8 clk
@pytest.mark.parametrize("seed", range(3))
@pytest.mark.parametrize("phase", range(4))
@pytest.mark.parametrize("own,rise,fall,xse0", [(2, (5, 14), (4, 5), 10), (2, (4, 5), (5, 14), 0),
                                                (1, (5, 12), (4, 5), 8), (1, (4, 5), (5, 12), 0)])
def test_usb_edges_crossover(own, rise, fall, xse0, phase, seed):
    c, host, img = setup(own, phase=phase)
    v = UsbEdges(random.Random(seed * 4 + phase), rise, fall)
    assert session(c, host, img, v) == []
    assert v.xmax >= xse0 - 1                                  # the long crossovers did occur


def se0_glitch(own, phase, k, n):
    # SE0 for n clk at the k-th K->J transition of an OUT DATA1 packet (D+ edge on time, D- late): True if the packet
    # still arrives (ACK, EV_OK). At J->K the late edge is D+ and sampling fails first (16 clk: half a bit)
    c, host, img = setup(own, phase=phase)
    host.packet(usb_token("OUT", ADDR, 0), t=c.cycle + 20 * BIT)
    host.idle(4)
    t0 = host.t
    host.packet(usb_data("DATA1", OUTPAY))
    segs = [s for s in host.segs if s[0] >= t0]
    te = int([b[0] for a, b in zip(segs, segs[1:]) if (a[2], b[2]) == (USB_K, USB_J)][k])
    host.idle(40)
    log, clash, _ = run(c, host, int(host.t) - c.cycle, view=lambda t, st: USB_SE0 if te <= t < te + n else st)
    ok = stream(c) == usb_data("DATA1", OUTPAY) + [0xE1, 0x101]
    return ok and [p[2] for p in chip_packets(log, host)] == [[USB_PID["ACK"]]]


def se0_threshold(own, phase, k):
    return next(n for n in range(1, 64) if not se0_glitch(own, phase, k, n))


# shortest SE0 that ends a packet: 512 words >= 20 clk (400 ns), 128 words 9-10 clk; both < 670 ns (receiver EOP min)
@pytest.mark.parametrize("k", [1, 6, 12])
@pytest.mark.parametrize("phase", range(4))
def test_usb_eop_confirm_threshold(phase, k):
    assert 20 <= se0_threshold(2, phase, k) <= 30
    assert 9 <= se0_threshold(1, phase, k) <= 10


# chip-driven packets with the chip's own pads seen 0-15 clk late (300 ns edges) and crossovers: TXJK 1 does not
# read the line; TXJK 0 reads D+ TOCK clk before each drive
@pytest.mark.parametrize("phase", range(4))
@pytest.mark.parametrize("own,defs", [(1, {}), (2, {}), (1, {"TXJK": 1, "EOPCHK": 0})])
@pytest.mark.parametrize("data", [[0x00] * 7, [0xFF] * 7, [0xF9]])
def test_usb_tx_slow_edges(own, defs, data, phase):
    c, host, img = setup(own, phase=phase, **defs)
    arm(c, img, 0, "DATA1", data)
    v = UsbEdges(random.Random(phase), (14, 14), (14, 14))
    log, clash, _ = in_txn(c, host, 0, view=v)
    r = chip_packets(log, host)
    assert [p[2] for p in r] == [usb_data("DATA1", data)] and r[0][3] and not clash
