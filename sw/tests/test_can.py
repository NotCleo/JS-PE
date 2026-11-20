# can.s (not designed in, D102/D103) on the ISS against an independent CAN node model (models.CanNode).
import pytest
from harness import prog, boot, record
from models import CanNode, can_host_tx, can_stuff, can_frame

ETX, ERX = 0, 1
STUFFY = [(0x000, [0x00] * 8), (0x0F8, [0xFF] * 8), (0x7C1, [0x07, 0xC1, 0xF0, 0x7C, 0x1F])]
FRAMES = [(0x123, [0x00, 0xFF, 0xA5]), (0x7F0, [0x55]), (0x000, [])] + STUFFY


def chip_dom(c):
    return (c.uio_oe >> 4 & 1) and not (c.uio_out >> 4 & 1)


def run(c, node, n, glitch=None, log=None):
    # glitch(t) -> True: dominant pulse seen by the chip input only (noise near the receiver)
    for _ in range(n):
        t = c.cycle
        bus = 0 if chip_dom(c) or node.drive(t) == 0 else 1
        node.observe(t, bus)
        if log is not None:
            log.append((t, bus, chip_dom(c)))
        c.step(0x08, (0 if glitch and glitch(t) else bus) << 4)


def setup(bit=100, phase=0, **kw):
    c, node = boot(prog("can", BIT=bit), phase), CanNode(kw.pop("nbit", bit), **kw)
    run(c, node, 12 * bit)                          # bus integration of both
    return c, node


def frame_clk(data, bit):
    return (60 + 10 * len(data)) * bit


def rx_expect(ident, data, rtr=0, ev=0x101):
    return [ident >> 8 & 7, ident & 0xFF, (ident & 1) << 7 | rtr << 6 | len(data)] + ([] if rtr else list(data)) + [ev]


def rx_stream(c):
    # header byte 0 carries ID10..8 in bits 2:0, the bits above are undefined: masked for every frame
    out, first = [], True
    for _, v in c.host_out[ERX]:
        out.append(v & 7 if first and not v & 0x100 else v)
        first = bool(v & 0x100)
    return out


def tx_events(c):
    return [v for _, v in c.host_out[ETX]]


def runs(log, col, t_from=0):
    # dominant runs (start, length) of column col (1 = bus, 2 = chip drive)
    out, start = [], None
    for row in log:
        d = (row[1] == 0) if col == 1 else bool(row[2])
        if row[0] < t_from:
            continue
        if d and start is None:
            start = row[0]
        elif not d and start is not None:
            out.append((start, row[0] - start))
            start = None
    return out


# ---------------- frames both ways, 500 and 125 kbit/s, +-1 % clock offset
SLOW = pytest.mark.slow
CASES = [(100, i, d, 0.0) for i, d in FRAMES] + [(100, 0x0F8, [0xFF] * 8, r) for r in (-0.01, 0.01)] + \
        [pytest.param(400, i, d, 0.0, marks=SLOW) for i, d in FRAMES[:1] + STUFFY[:2]] + \
        [pytest.param(400, 0x0F8, [0xFF] * 8, r, marks=SLOW) for r in (-0.01, 0.01)]


@pytest.mark.parametrize("bit,ident,data,off", CASES)
def test_can_chip_tx(bit, ident, data, off):
    # chip E_TX -> node receives and ACKs; chip E_RX receives the same frame (self-reception)
    c, node = setup(bit, phase=ident % 4, nbit=bit * (1 + off))
    log = []
    c.host_in[ETX] += can_host_tx(ident, data)
    run(c, node, frame_clk(data, bit), log=log)
    assert node.frames == [(ident, data, 0)] and not node.errors, (node.frames, node.errors)
    assert tx_events(c) == [0x101]
    assert rx_stream(c) == rx_expect(ident, data), [hex(v) for v in rx_stream(c)]
    assert c.eng[ETX].ovr == 0 and c.eng[ERX].ovr == 0
    t0, want = runs(log, 2)[0][0], can_stuff(can_frame(ident, data))
    lv = {t: b for t, b, _ in log}
    assert [lv[t0 + k * bit + bit // 2] for k in range(len(want))] == want     # stuffed frame on the bus
    edges = {(t - t0) % bit for t, _ in runs(log, 2) if t0 + bit < t < t0 + len(want) * bit}
    assert len(edges) <= 1                                                        # one bit grid, no resync


@pytest.mark.parametrize("bit,ident,data,off", CASES)
def test_can_chip_rx(bit, ident, data, off):
    # node sends -> chip E_RX receives and ACKs through E_TX (G_DRV relay)
    c, node = setup(bit, phase=(ident + 1) % 4, nbit=bit * (1 + off))
    node.send(c.cycle + 37, ident, data)
    run(c, node, frame_clk(data, bit))
    assert node.acked is True and not node.errors
    assert rx_stream(c) == rx_expect(ident, data), [hex(v) for v in rx_stream(c)]
    assert tx_events(c) == [] and c.eng[ERX].ovr == 0


def test_can_remote_frame():
    c, node = setup()
    node.send(c.cycle + 10, 0x355, [0] * 5, rtr=True)            # DLC 5, no data field
    run(c, node, frame_clk([], 100))
    assert node.acked and rx_stream(c) == rx_expect(0x355, [0] * 5, rtr=1)


# ---------------- arbitration
ARB = [((0x100, [0x11], 0), (0x101, [0x22], 0), "chip"),       # IDs differ only in the last bit
       ((0x101, [0x11], 0), (0x100, [0x22], 0), "node"),
       ((0x123, [0x33], 0), (0x123, [], 1), "chip"),            # same ID, data frame beats remote frame (RTR)
       ((0x123, [], 1), (0x123, [0x44], 0), "node"),
       ((0x0F8, [0xFF] * 2, 0), (0x0F0, [0x00], 0), "node")]          # stuff-heavy IDs


@pytest.mark.parametrize("delta", [0, 4, 9])
@pytest.mark.parametrize("mine,theirs,winner", ARB)
def test_can_arbitration(mine, theirs, winner, delta):
    c, node = setup(phase=delta % 4)
    ci, cd, cr = mine
    ni, nd, nr = theirs
    node.send_on_sof(delta, ni, nd if not nr else [], nr)
    c.host_in[ETX] += can_host_tx(ci, cd if not cr else [], cr)
    run(c, node, 2 * frame_clk(cd + nd, 100))
    win_frame = (ci, [] if cr else cd, cr) if winner == "chip" else (ni, [] if nr else nd, nr)
    assert node.frames == [win_frame] and not node.errors, (node.frames, node.errors)
    assert node.lost == (winner == "chip")
    if winner == "chip":
        assert tx_events(c) == [0x101] and node.acked is None
    else:
        assert tx_events(c) == [0x102] and node.acked is True       # lost; E_RX ACKs the winner
        c.host_in[ETX] += can_host_tx(ci, cd if not cr else [], cr)   # host retries: stream is aligned (drain)
        run(c, node, frame_clk(cd, 100) + 2000)
        assert tx_events(c) == [0x102, 0x101] and node.frames[-1] == (ci, [] if cr else cd, cr)
    w = win_frame
    assert rx_stream(c)[:len(rx_expect(w[0], w[1], w[2]))] == rx_expect(w[0], w[1], w[2])


def test_can_defer_to_earlier_sof():
    # node starts first; the chip's frame queued 30 clk later waits for the bus to be idle again
    c, node = setup()
    node.send(c.cycle + 5, 0x3AA, [0x01])
    run(c, node, 35)
    c.host_in[ETX] += can_host_tx(0x055, [0x02])
    run(c, node, 2 * frame_clk([0, 0], 100))
    assert node.frames == [(0x3AA, [0x01], 0), (0x055, [0x02], 0)] and node.acked is True and not node.errors
    assert tx_events(c) == [0x101]
    assert rx_stream(c) == rx_expect(0x3AA, [0x01]) + rx_expect(0x055, [0x02])


# ---------------- errors
def test_can_stuff_error_then_recover():
    c, node = setup()
    log = []
    body = can_stuff(can_frame(0x123, [0x00, 0x42]))
    k = [i for i in range(len(body)) if i > 19 and body[i] != body[i - 1] and
         all(body[i - j] == body[i - 1] for j in range(1, 6))][0]
    bad = body[:k] + [body[k - 1]] + body[k + 1:]                          # stuff bit equal to the run: 6 equal
    node.send(c.cycle + 20, 0, [], bits=bad)
    t_bad = node.t0 + k * 100
    run(c, node, 40 * 100, log=log)
    assert rx_stream(c)[-1] == 0x103, [hex(v) for v in rx_stream(c)]
    flag = [r for r in runs(log, 2, t_bad) if r[1] > 300]
    assert flag and t_bad + 100 <= flag[0][0] <= t_bad + 100 + 25, (flag, t_bad)          # from the next bit
    assert 6 * 100 - 25 <= flag[0][1] <= 6 * 100 + 25
    assert max(n for _, n in runs(log, 1, t_bad)) <= 12 * 100 + 50                         # superposed flags
    n0 = len(c.host_out[ERX])
    node.send(c.cycle + 300, 0x321, [0x5A])
    run(c, node, frame_clk([0], 100))
    assert node.acked and rx_stream(c)[n0:] == rx_expect(0x321, [0x5A])       # next frame intact


def test_can_crc_error_flag_after_ack_delimiter():
    c, node = setup(flags=False)
    log = []
    fb = can_frame(0x2A5, [0x81, 0x18])
    fb[-3] ^= 1                                                            # wrong CRC, valid stuffing
    body = can_stuff(fb)
    node.send(c.cycle + 20, 0, [], bits=body)
    run(c, node, frame_clk([0, 0], 100), log=log)
    assert rx_stream(c)[-1] == 0x102 and node.acked is False
    t4 = node.t0 + (len(body) + 3) * 100                                   # bit after the ACK delimiter
    flag = [r for r in runs(log, 2, node.t0 + len(body) * 100)]
    assert flag and t4 <= flag[0][0] <= t4 + 25 and 575 <= flag[0][1] <= 625, (flag, t4)


def test_can_missing_ack_then_retry():
    c, node = setup(ack=False, flags=False)
    log = []
    c.host_in[ETX] += can_host_tx(0x456, [0x01, 0x02])
    run(c, node, frame_clk([0, 0], 100), log=log)
    assert tx_events(c) == [0x104]
    body = len(can_stuff(can_frame(0x456, [0x01, 0x02])))
    t_sof = runs(log, 2)[0][0]
    flag = [r for r in runs(log, 2, t_sof + (body + 1) * 100) if r[1] > 300]
    t3 = t_sof + (body + 2) * 100                                          # ACK delimiter
    assert flag and t3 <= flag[0][0] <= t3 + 10 and 595 <= flag[0][1] <= 610, (flag, t3)
    assert rx_stream(c) == rx_expect(0x456, [0x01, 0x02])                  # own frame, CRC good
    node.ack = True
    c.host_in[ETX] += can_host_tx(0x456, [0x01, 0x02])
    run(c, node, frame_clk([0, 0], 100) + 1500)
    assert tx_events(c) == [0x104, 0x101] and node.frames[-1] == (0x456, [0x01, 0x02], 0)


def test_can_bit_error_flag_then_retry():
    # node forces one dominant bit into a recessive data bit of the chip's frame
    c, node = setup()
    log = []
    data = [0x0F, 0xF0, 0x3C]
    c.host_in[ETX] += can_host_tx(0x2C3, data)
    run(c, node, 200, log=log)
    t_sof = [t for t, _, d in log if d][0]
    body = can_stuff(can_frame(0x2C3, data))
    k = [i for i in range(25, len(body) - 16) if body[i] == 1][0]
    node.force = [(t_sof + k * 100 + 4, t_sof + (k + 1) * 100)]
    run(c, node, frame_clk(data, 100), log=log)
    assert tx_events(c) == [0x103], [hex(v) for v in tx_events(c)]
    flag = [r for r in runs(log, 2, t_sof + (k + 1) * 100) if r[1] > 300]
    assert flag and flag[0][0] <= t_sof + (k + 1) * 100 + 25 and 580 <= flag[0][1] <= 610, (flag, k)  # one flag
    assert max(n for _, n in runs(log, 1, t_sof + k * 100 + 50)) <= 12 * 100 + 50
    assert "stuff" in node.errors or "crc" in node.errors or "form" in node.errors
    node.force = []
    c.host_in[ETX] += can_host_tx(0x2C3, data)
    run(c, node, frame_clk(data, 100) + 1500)
    assert tx_events(c)[-1] == 0x101 and node.frames[-1] == (0x2C3, data, 0)


# ---------------- glitch behaviour (limit 1 of can.s: SJW unlimited)
GLITCH = 8


def glitch_outcome(off):
    # node sends 0x7F0 [0x55]; one GLITCH-clk dominant pulse at the chip input only, `off` clk into recessive
    # bit k of a run of >= 3 recessive bits (ID 0x7F0: SOF then seven 1s). Outcome: chip RX events + ACK.
    c, node = setup()
    node.send(c.cycle + 20, 0x7F0, [0x55])
    t_g = node.t0 + 3 * 100 + off                                          # inside ID bit 8 (third 1)
    run(c, node, frame_clk([0], 100), glitch=lambda t: t_g <= t < t_g + GLITCH)
    s = rx_stream(c)
    return "ok" if s == rx_expect(0x7F0, [0x55]) else ("ev%d" % (s[-1] & 0xFF) if s and s[-1] & 0x100 else "none")


@pytest.mark.slow
def test_can_glitch_behaviour():
    # Measured (limit 1 of can.s): an 8-clk dominant pulse at the chip input inside a recessive bit (ID 0x7F0,
    # third 1 of the first run) re-times E_RX. Starting at offset 28..72 of the bit (from BIT - SPC + 3 up to just
    # before the sample point) the restarted timer moves this bit's sample past the bit end: one bit is dropped,
    # the frame fails with a CRC error (event 2, error flag: the transmitter retries; nothing is delivered as
    # good). Offsets 0..24: the sample only moves later inside the same bit. Offsets >= 76 (after the sample):
    # the shifted grid samples the following recessive bits correctly until the next falling edge re-syncs.
    # An SJW-limited receiver would move the sample point by at most SJW and keep the bit in most of 28..72.
    res = {off: glitch_outcome(off) for off in range(0, 100, 4)}
    bad = sorted(o for o, r in res.items() if r != "ok")
    record("CAN glitch %d clk at offset 0..96 of a recessive bit (BIT 100, sample point 75)" % GLITCH,
           bad == list(range(28, 76, 4)), "corrupted (CRC error) at offsets %s" % bad)
    assert bad == list(range(28, 76, 4)) and all(res[o] == "ev2" for o in bad), res


@pytest.mark.slow
@pytest.mark.parametrize("node_id,chip_wins", [(0x100, False), (0x102, True)])
def test_can_race_node_sof_first(node_id, chip_wins):
    # the node's SOF lands 0..24 clk before the chip's own SOF would (chip polling for bus idle): the chip either
    # defers (E_RX cleared G_IDLE in time) or starts late and arbitrates on its own grid; no error in any case
    probe = boot(prog("can"), 0)
    pn = CanNode(100)
    run(probe, pn, 12 * 100)
    probe.host_in[ETX] += can_host_tx(0x101, [0x5A])
    log = []
    run(probe, pn, 400, log=log)
    ts = [t for t, _, d in log if d][0]                        # chip SOF when nobody else sends
    for x in range(0, 25, 2):
        c, node = setup()
        c.host_in[ETX] += can_host_tx(0x101, [0x5A])
        node.send(ts - x, node_id, [0xC3])
        run(c, node, 3 * frame_clk([0, 0], 100))
        assert not node.errors and node.err_from is None, (x, node.errors)
        got = [f[0] for f in node.frames]
        if chip_wins:
            assert 0x101 in got and tx_events(c) == [0x101], (x, got, tx_events(c))
        else:
            assert got[0] == node_id and tx_events(c) in ([0x102], [0x101]), (x, got, tx_events(c))  # lost / deferred
            assert tx_events(c) == [0x102] or got == [node_id, 0x101]


# ---------------- reviewer findings (step 4): host timing and queued frames
def test_can_bit_error_queued_frame_waits():
    # frame 2 queued behind frame 1; bit error in frame 1: E_TX flags, E_RX's own flag (seen with the relay off)
    # must not let frame 2 start inside the error flags / delimiter: it starts >= 11 recessive bits after them
    c, node = setup()
    log = []
    data = [0x0F, 0xF0, 0x3C]
    c.host_in[ETX] += can_host_tx(0x2C3, data) + can_host_tx(0x000, [0x00])
    run(c, node, 200, log=log)
    t_sof = [t for t, _, d in log if d][0]
    body = can_stuff(can_frame(0x2C3, data))
    k = [i for i in range(25, len(body) - 16) if body[i] == 1][0]
    node.force = [(t_sof + k * 100 + 4, t_sof + (k + 1) * 100)]
    run(c, node, 2 * frame_clk(data, 100), log=log)
    assert tx_events(c) == [0x103, 0x101], [hex(v) for v in tx_events(c)]
    assert node.frames[-1] == (0x000, [0x00], 0) and node.errors.count("bit") <= 1
    tb = t_sof + k * 100
    dom = [r for r in runs(log, 1, tb) if r[1] > 300]                     # the superposed error flags
    flag_end = dom[0][0] + dom[0][1]
    nxt = [t for t, n in runs(log, 2, flag_end)][0]
    assert nxt - flag_end >= 11 * 100 - 10, (dom[:3], nxt)


def feed(c, data, every):
    # host keeps the TX FIFO fed one byte per `every` clk
    q = list(data)

    def f(t):
        if q and t % every == 0:
            c.host_in[ETX].append(q.pop(0))
    return f


def run_fed(c, node, n, f):
    for _ in range(n):
        f(c.cycle)
        run(c, node, 1)


def test_can_lost_slow_host_still_acks_winner():
    # arbitration lost early in an 8-byte frame whose bytes arrive slowly: the relay is back on during the drain,
    # so E_RX's ACK for the winner still reaches the bus; the stream stays aligned for the next frame
    c, node = setup()
    node.send_on_sof(4, 0x100, [])
    b = can_host_tx(0x101, [0x11] * 8)
    c.host_in[ETX] += b[:5]
    run_fed(c, node, 60 * 100, feed(c, b[5:], 900))
    assert node.acked is True and tx_events(c) == [0x102] and node.frames == [(0x100, [], 0)], (node.acked,
                                                                                                node.frames)
    c.host_in[ETX] += can_host_tx(0x2A2, [0x5A])
    run(c, node, frame_clk([0], 100) + 1500)
    assert tx_events(c) == [0x102, 0x101] and node.frames[-1] == (0x2A2, [0x5A], 0)


def test_can_late_host_bytes_drain_exact():
    # data bytes arrive after the frame has started: E_TX stalls at the byte reload, the frame breaks (error
    # flags); the drain removes exactly the rest of that frame, so the next frame is read and sent intact
    c, node = setup()
    b = can_host_tx(0x2C3, [0x0F, 0xF0, 0x3C])
    c.host_in[ETX] += b[:4]
    run(c, node, 60 * 100)
    c.host_in[ETX] += b[4:] + can_host_tx(0x111, [0x77])
    run(c, node, 200 * 100)
    assert c.host_in[ETX] == [] and node.frames[-1] == (0x111, [0x77], 0) and tx_events(c)[-1] == 0x101, \
        ([hex(v) for v in tx_events(c)], node.frames)
