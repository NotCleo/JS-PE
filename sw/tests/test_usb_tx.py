# usb_ls.s transmitter on the ISS: handshakes, IN data from the RP2350-armed buffer, STALL, EOP, turnaround, bounds.
import pytest
from harness import arm_steps
from jspe_asm import slot_gap, slot_chain
from models import USB_J, USB_SE0, USB_PID, usb_token, usb_data, usb_key, usb_crc_preset, usb_crc16_reg
from test_usb import setup, run, stream, chip_packets, transaction, ADDR, BIT, ERX, ETX, PAY

PHASES = range(4)


def arm(c, img, ep, pid, data=(), stall=False):
    # what the RP2350 writes with one WRMEM: buffer words, KEY0 unchanged, BUFKEY last
    b, k = img.label("inbuf"), img.label("keys")
    if stall:
        words = [16, 0, 0x80 | USB_PID["STALL"] << 8]
    else:
        d = list(data) + [0] * (len(data) & 1)
        words = [32 + 8 * len(data), usb_crc_preset(USB_PID[pid]), 0x80 | USB_PID[pid] << 8] + \
            [d[i] | d[i + 1] << 8 for i in range(0, len(d), 2)]
    assert len(words) <= 7
    for i, w in enumerate(words):
        c.mem[b + i] = w
    c.mem[k + 1] = usb_key(ADDR, ep)


def gap_bits(resp, host):
    return (resp[0] - host.eop_ends[-1]) / BIT


def eop_ok(c, log, p):
    # SE0 for 2 bits, J driven for 1 bit, then both pads released
    se0 = p[1] - next(t for t in range(p[0], p[1]) if log[t] == USB_SE0)
    rel = next(t for t in range(p[1], len(c.oelog)) if not c.oelog[t] & 0x30)
    return 2 * BIT - 2 <= se0 <= 2 * BIT + 2 and BIT - 2 <= rel - p[1] <= BIT + 2


def in_txn(c, host, ep, after=160, view=None):
    return transaction(c, host, usb_token("IN", ADDR, ep), after=after, view=view)


DATAS = [("DATA1", []), ("DATA0", [0x05]), ("DATA0", [0xF9]), ("DATA1", list(range(1, 8))), ("DATA0", [0x12, 0x01, 0x10, 0x01, 0, 0, 0, 8]),
         ("DATA1", [0xFF] * 8), ("DATA0", [0x00] * 8), ("DATA1", [0x7E, 0xFF, 0x01, 0xFC])]


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("own", [1, 0, 2])
@pytest.mark.parametrize("pid,data", DATAS)
def test_usb_tx_in_data_then_ack(own, phase, pid, data):
    c, host, img = setup(own, phase=phase)
    arm(c, img, 1, pid, data)
    log, clash, _ = in_txn(c, host, 1)
    resp = chip_packets(log, host)
    assert [p[2] for p in resp] == [usb_data(pid, data)] and resp[0][3] and not clash
    assert usb_crc16_reg(resp[0][2][1:]) == 0xB001
    assert 2.0 <= gap_bits(resp[0], host) <= 6.5 and eop_ok(c, log, resp[0])
    log, clash, _ = transaction(c, host, [USB_PID["ACK"]], after=20)            # host ACK
    assert stream(c) == [0x103] and c.mem[img.label("keys") + 1] == 0
    log, clash, _ = in_txn(c, host, 1, after=40)
    assert [p[2] for p in chip_packets(log, host)] == [[USB_PID["NAK"]]]


@pytest.mark.parametrize("own", [1, 0, 2])
def test_usb_tx_resend_until_ack(own):
    c, host, img = setup(own)
    arm(c, img, 0, "DATA1", PAY)
    for _ in range(3):
        log, clash, _ = in_txn(c, host, 0)
        assert [p[2] for p in chip_packets(log, host)] == [usb_data("DATA1", PAY)]
    assert stream(c) == [] and c.mem[img.label("keys") + 1] == usb_key(ADDR, 0)


@pytest.mark.parametrize("own", [1, 0, 2])
def test_usb_tx_other_endpoint_nak(own):
    # buffer armed for EP1: an IN to EP0 gets NAK and leaves it armed
    c, host, img = setup(own)
    arm(c, img, 1, "DATA0", [1, 2])
    log, clash, _ = in_txn(c, host, 0, after=40)
    assert [p[2] for p in chip_packets(log, host)] == [[USB_PID["NAK"]]]
    assert c.mem[img.label("keys") + 1] == usb_key(ADDR, 1)


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("own", [1, 0, 2])
def test_usb_tx_stall(own, phase):
    c, host, img = setup(own, phase=phase)
    arm(c, img, 0, None, stall=True)
    for _ in range(2):                                   # stays armed: no ACK follows a STALL
        log, clash, _ = in_txn(c, host, 0, after=40)
        resp = chip_packets(log, host)
        assert [p[2] for p in resp] == [[USB_PID["STALL"]]] and 2.0 <= gap_bits(resp[0], host) <= 6.5
        assert eop_ok(c, log, resp[0]) and not clash


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("own", [1, 0, 2])
def test_usb_tx_handshake_turnaround(own, phase):
    c, host, img = setup(own, phase=phase)
    log, clash, _ = transaction(c, host, usb_token("OUT", ADDR, 0), usb_data("DATA1", [2]))
    ack = chip_packets(log, host)
    assert [p[2] for p in ack] == [[USB_PID["ACK"]]] and eop_ok(c, log, ack[0]) and not clash
    assert 2.0 <= gap_bits(ack[0], host) <= 6.5
    log, clash, _ = in_txn(c, host, 0, after=40)
    nak = chip_packets(log, host)
    assert [p[2] for p in nak] == [[USB_PID["NAK"]]] and eop_ok(c, log, nak[0]) and not clash
    assert 2.0 <= gap_bits(nak[0], host) <= 6.5


@pytest.mark.parametrize("own", [1, 0, 2])
def test_usb_tx_setup_disarms(own):
    # a new SETUP makes a stale IN buffer unusable: the chip disarms it, the next IN is NAKed
    c, host, img = setup(own)
    arm(c, img, 0, "DATA1", PAY)
    transaction(c, host, usb_token("SETUP", ADDR, 0), usb_data("DATA0", PAY))
    assert c.mem[img.label("keys") + 1] == 0
    log, clash, _ = in_txn(c, host, 0, after=40)
    assert [p[2] for p in chip_packets(log, host)] == [[USB_PID["NAK"]]]


@pytest.mark.parametrize("own", [1, 0, 2])
def test_usb_bound_vs_iss(own):
    # every guarded instruction of both engines, 4 slot phases: measured arming delay <= assembler bound
    n, bad = 0, []
    for phase in PHASES:
        c, host, img = setup(own, phase=phase)
        transaction(c, host, usb_token("SETUP", ADDR, 0), usb_data("DATA0", PAY))
        arm(c, img, 0, "DATA1", [0xFF] * 7)
        in_txn(c, host, 0)
        transaction(c, host, [USB_PID["ACK"]], after=20)
        in_txn(c, host, 1, after=40)
        transaction(c, host, usb_token("OUT", ADDR, 0), usb_data("DATA1", [0xFF] * 8, bad_crc=True))
        host.keepalive(host.t + 30 * BIT)
        arm(c, img, 0, None, stall=True)
        in_txn(c, host, 0, after=40)
        transaction(c, host, usb_token("IN", ADDR + 3, 1), after=40)
        for e in (ETX, ERX):
            for s in arm_steps(c, img, e, slot_gap(0x44, e), slot_chain(0x44, e)):
                n += 1
                if s["bound"] is None or s["measured"] > s["bound"] or s["target"] != s["next"] or s["miss"]:
                    bad.append((phase, e, hex(s["g"]), s["measured"], s["bound"]))
    assert n > 1000 and not bad, (n, bad[:8])


@pytest.mark.parametrize("own,pc", [(1, 0), (0, 0), (0, 1), (1, 1)])
def test_usb_tx_corrupted_in_pid(own, pc):
    # IN with PID 0x68 while EP0 is armed. PIDCHK 1: ignored, buffer kept. PIDCHK 0: the chip disarms and the
    # host's retried IN is taken as data (EV_CRC, closing byte 0x68, not SETUP/OUT): the RP2350 re-arms
    c, host, img = setup(own, pidchk=pc)
    arm(c, img, 0, "DATA1", PAY)
    tok = usb_token("IN", ADDR, 0)
    tok[0] = 0x68
    log, clash, _ = transaction(c, host, tok, after=40)
    assert chip_packets(log, host) == []
    log, clash, _ = in_txn(c, host, 0)
    resp = [p[2] for p in chip_packets(log, host)]
    if pc:
        assert resp == [usb_data("DATA1", PAY)] and stream(c) == []
        return
    st = stream(c)
    assert resp == [] and st[-2:] == [0x68, 0x102] and c.mem[img.label("keys") + 1] == 0
    arm(c, img, 0, "DATA1", PAY)                                       # the RP2350 rule
    log, clash, _ = in_txn(c, host, 0)
    assert [p[2] for p in chip_packets(log, host)] == [usb_data("DATA1", PAY)]


@pytest.mark.parametrize("own,pc", [(1, 0), (0, 1), (1, 1)])
def test_usb_tx_host_ack_after_2_bits(own, pc):
    # host ACK 2 bit times after the chip's DATA EOP (SE0 -> J): E_RX is hunting again in time
    c, host, img = setup(own, pidchk=pc)
    arm(c, img, 0, "DATA1", [0xF9])
    host.packet(usb_token("IN", ADDR, 0), t=c.cycle + 20 * BIT)
    full, seen = [None] * c.cycle, False
    while True:
        lg, _, _ = run(c, host, 8)
        full += lg[len(full):]
        d = bool(c.oelog[-1] & 0x30)
        if seen and not d:
            break
        seen |= d
    resp = chip_packets(full, host)
    assert [p[2] for p in resp] == [usb_data("DATA1", [0xF9])] and resp[0][3]
    te = resp[0][1]
    host.packet([USB_PID["ACK"]], t=te + 2 * BIT)
    run(c, host, 40 * BIT)
    assert stream(c) == [0x103] and c.mem[img.label("keys") + 1] == 0
