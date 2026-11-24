# usb_ls.s (not designed in, D200) on the ISS against an independent USB LS host line model (models.UsbHostLine).
import pytest
from harness import prog, boot
from models import (USB_J, USB_SE0, USB_PID, UsbHostLine, usb_token, usb_data, usb_key, usb_decode)

ETX, ERX = 0, 1
BIT = 32
ADDR = 5


def setup(own=1, addr=ADDR, bit=BIT, phase=0, pidchk=0):
    img = prog("usb_ls", OWNCHK=own, PIDCHK=pidchk)
    c = boot(img, phase)
    k = img.label("keys")
    c.mem[k], c.mem[k + 1], c.mem[k + 2] = usb_key(addr, 0), 0, usb_key(addr, 1)
    return c, UsbHostLine(bit), img


def bus(c, host, t, clash):
    oe, out = c.uio_oe, c.uio_out
    if oe & 0x30:
        if host.state(t) is not None:
            clash.append(t)
        return (out >> 4 & 1 if oe & 0x10 else 0, out >> 5 & 1 if oe & 0x20 else 1)
    s = host.state(t)
    return s if s is not None else USB_J


def run(c, host, n, link_b=None):
    # link_b: model the chip->host link at bit time B clk (10 B per data byte, 20 B per event, +2 bytes at start)
    log, clash, occ = [None] * c.cycle, [], 0                  # log index = cycle
    c.oelog = [0] * c.cycle
    free, seen = 2 * 10 * link_b if link_b else 0, 0
    for _ in range(n):
        t = c.cycle
        st = bus(c, host, t, clash)
        log.append(st)
        c.oelog.append(c.uio_oe)
        if link_b:
            c.tx_block[ERX] = t < free
        c.step(0x08, st[0] << 4 | st[1] << 5)
        occ = max(occ, 4 - c.tx[ERX].free())
        if link_b and len(c.host_out[ERX]) > seen:
            v = c.host_out[ERX][-1][1]
            seen = len(c.host_out[ERX])
            free = c.cycle + 10 * link_b * (2 if v & 0x100 or v == 0x1B else 1)
    return log, clash, occ


def stream(c):
    return [v for _, v in c.host_out[ERX]]


def chip_packets(log, host):
    # packets the chip drove: those not inside a host segment
    t0 = next(k for k, v in enumerate(log) if v is not None)
    return [p for p in usb_decode(log, BIT, t0) if host.state(p[0]) is None]


def transaction(c, host, tok, data=None, gap=4, after=40, link_b=None):
    host.packet(tok, t=max(host.t, c.cycle) + 20 * BIT)
    if data is not None:
        host.idle(gap)
        host.packet(data)
    host.idle(after)
    return run(c, host, int(host.t) - c.cycle, link_b)


PAY = [0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x12, 0x00]          # GET_DESCRIPTOR(device)


@pytest.mark.parametrize("phase", range(4))
@pytest.mark.parametrize("own", [1, 0])
@pytest.mark.parametrize("tok,pid", [("SETUP", "DATA0"), ("OUT", "DATA0"), ("OUT", "DATA1")])
def test_usb_rx_setup_out(own, tok, pid, phase):
    c, host, _ = setup(own, phase=phase)
    pkt = usb_data(pid, PAY if tok == "SETUP" else [0x02])
    log, clash, _ = transaction(c, host, usb_token(tok, ADDR, 0), pkt)
    assert stream(c) == pkt + [USB_PID[tok], 0x101]
    resp = chip_packets(log, host)
    assert [p[2] for p in resp] == [[USB_PID["ACK"]]] and resp[0][3] and not clash
    gap = (resp[0][0] - host.eop_ends[-1]) / BIT
    assert 2.0 <= gap <= 6.5, gap                                  # turnaround, detachable cable
    assert c.eng[ERX].ovr == 0


@pytest.mark.parametrize("own", [1, 0])
@pytest.mark.parametrize("addr,ep", [(ADDR + 1, 0), (0, 0), (ADDR, 2), (ADDR, 1)])
def test_usb_rx_not_ours_ignored(own, addr, ep):
    # wrong address or endpoint (EP1 has no OUT): no bytes, no handshake
    c, host, _ = setup(own)
    log, clash, _ = transaction(c, host, usb_token("SETUP", addr, ep), usb_data("DATA0", PAY))
    assert stream(c) == [] and chip_packets(log, host) == [] and not clash


@pytest.mark.parametrize("own", [1, 0])
def test_usb_rx_keepalive_ignored(own):
    c, host, _ = setup(own)
    for _ in range(3):
        host.keepalive(host.t + 30 * BIT)
    log, clash, _ = run(c, host, int(host.t + 40 * BIT) - c.cycle)
    assert stream(c) == [] and chip_packets(log, host) == []
    log, clash, _ = transaction(c, host, usb_token("SETUP", ADDR, 0), usb_data("DATA0", PAY))
    assert stream(c)[-2:] == [USB_PID["SETUP"], 0x101] and len(chip_packets(log, host)) == 1


STUFFY = [[0xF9], [0xFF] * 8, [0x7F, 0xFE, 0x3F, 0xFC, 0xFF, 0x01, 0xFF, 0x80], [0xFE, 0xFF, 0xFF, 0x7F]]


@pytest.mark.parametrize("own", [1, 0])
@pytest.mark.parametrize("pay", STUFFY)
@pytest.mark.parametrize("drift", [0.0, -0.015, 0.015])
def test_usb_rx_stuffing(own, pay, drift):
    c, host, _ = setup(own, bit=BIT * (1 + drift))
    pkt = usb_data("DATA0", pay)
    log, clash, _ = transaction(c, host, usb_token("OUT", ADDR, 0), pkt)
    assert stream(c) == pkt + [USB_PID["OUT"], 0x101]
    assert [p[2] for p in chip_packets(log, host)] == [[USB_PID["ACK"]]]


@pytest.mark.parametrize("own", [1, 0])
def test_usb_rx_bad_crc(own):
    c, host, _ = setup(own)
    pkt = usb_data("DATA0", PAY, bad_crc=True)
    log, clash, _ = transaction(c, host, usb_token("SETUP", ADDR, 0), pkt)
    assert stream(c) == pkt + [USB_PID["SETUP"], 0x102] and chip_packets(log, host) == []


@pytest.mark.parametrize("own", [1, 0])
def test_usb_rx_fifo_drain_b16(own):
    # worst case: 8-byte payload of 0x00 (no stuffing: fastest bytes), link B = 16, host reads continuously
    c, host, _ = setup(own)
    pkt = usb_data("DATA0", [0x00] * 8)
    log, clash, occ = transaction(c, host, usb_token("OUT", ADDR, 0), pkt, link_b=16)
    assert stream(c) == pkt + [USB_PID["OUT"], 0x101] and c.eng[ERX].ovr == 0
    assert occ <= 3, occ


@pytest.mark.parametrize("own", [1, 0])
@pytest.mark.parametrize("addr,ep", [(ADDR, 0), (ADDR, 1), (ADDR + 1, 1)])
def test_usb_rx_in_unarmed(own, addr, ep):
    # IN while nothing is armed: NAK for our endpoints; another address: silence (OWNCHK 1) or NAK (OWNCHK 0)
    c, host, _ = setup(own)
    log, clash, _ = transaction(c, host, usb_token("IN", addr, ep))
    resp = [p[2] for p in chip_packets(log, host)]
    ours = addr == ADDR
    assert resp == ([[USB_PID["NAK"]]] if ours or not own else []) and stream(c) == [] and not clash


VARIANTS = [(1, 0), (0, 0), (0, 1)]                                   # (OWNCHK, PIDCHK)


@pytest.mark.parametrize("own,pc", VARIANTS)
def test_usb_rx_stray_ack_and_bad_crc5(own, pc):
    c, host, _ = setup(own, pidchk=pc)
    log, clash, _ = transaction(c, host, [USB_PID["ACK"]])
    tok = usb_token("SETUP", ADDR, 0)
    tok[2] ^= 0x08                                                     # CRC5 bit flipped
    log2, clash2, _ = transaction(c, host, tok, usb_data("DATA0", PAY))
    assert stream(c) == [] and chip_packets(log, host) == [] and chip_packets(log2, host) == []


@pytest.mark.parametrize("own,pc", VARIANTS)
def test_usb_rx_min_interpacket_gap(own, pc):
    # token -> DATA 2 bit times apart (the host's minimum)
    c, host, _ = setup(own, pidchk=pc)
    pkt = usb_data("DATA0", PAY)
    log, clash, _ = transaction(c, host, usb_token("SETUP", ADDR, 0), pkt, gap=2)
    assert stream(c) == pkt + [USB_PID["SETUP"], 0x101]
    assert [p[2] for p in chip_packets(log, host)] == [[USB_PID["ACK"]]]


@pytest.mark.parametrize("own,pc", VARIANTS)
def test_usb_rx_corrupted_data_pid(own, pc):
    # DATA with PID 0xC2 (check nibble wrong): PIDCHK 1 ignores it; PIDCHK 0 ACKs it and the RP2350 sees the bad PID
    c, host, _ = setup(own, pidchk=pc)
    pkt = usb_data("DATA0", [0x02])
    pkt[0] = 0xC2
    log, clash, _ = transaction(c, host, usb_token("OUT", ADDR, 0), pkt)
    resp = [p[2] for p in chip_packets(log, host)]
    if pc:
        assert resp == [] and stream(c) == pkt + [USB_PID["OUT"], 0x102]
    else:
        assert resp == [[USB_PID["ACK"]]] and stream(c) == pkt + [USB_PID["OUT"], 0x101]


@pytest.mark.parametrize("own,pc", VARIANTS)
def test_usb_rx_reset_mid_transaction(own, pc):
    # SETUP token, then a bus reset instead of its DATA: the reset's SE0 closes the pending data packet as
    # EV_CRC (token PID, no bytes), so the next SETUP works at once
    c, host, _ = setup(own, pidchk=pc)
    transaction(c, host, usb_token("SETUP", ADDR, 0))
    host.t = max(host.t, c.cycle)
    host._seg(1500 / BIT, USB_SE0)
    host._seg(1, USB_J)
    pkt = usb_data("DATA0", PAY)
    log, _, _ = transaction(c, host, usb_token("SETUP", ADDR, 0), pkt)
    assert [p[2] for p in chip_packets(log, host)] == [[USB_PID["ACK"]]]
    assert stream(c) == [USB_PID["SETUP"], 0x102] + pkt + [USB_PID["SETUP"], 0x101]
