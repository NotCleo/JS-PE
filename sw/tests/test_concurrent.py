# CAN (can.s E0 TX + E1 RX) and UART (uart_tx.s E2 + uart_rx.s E3) in one 512-word image (D131), all four
# engines at once, against CanNode and the UART models; pad timing must equal each protocol's image alone.
import os
import random
import pytest
from harness import assemble, PROG, boot
from models import CanNode, can_host_tx, can_stuff, can_frame, UartSender, uart_decode
from test_can import chip_dom, rx_expect, rx_stream, tx_events

ETX, ERX, UTX, URX = 0, 1, 2, 3
BIT, NOM = 100, 50                       # CAN 500 kbit/s, UART 1 Mbaud
F_CHIP = (0x123, [0x00, 0xFF, 0xA5, 0x5A, 0x01, 0x80, 0x7E, 0x3C])
F_NODE = (0x0F8, [0xFF] * 8)
T_NODE, T_END = 15000, 32000             # clk after the start: node frame request, end of the run
N_UART = 56                              # bytes each way, ~28k clk at 1 Mbaud


def image(can=True, uart=True):
    files = (["can"] if can else []) + (["uart_tx", "uart_rx"] if uart else [])
    defs = {"uart_tx.s:ENG": "2", "uart_rx.s:ENG": "3"} if uart else {}
    return assemble([os.path.join(PROG, f + ".s") for f in files], defs)


def scenario(phase, can=True, uart=True, seed=7):
    r = random.Random(seed)
    utx = [r.getrandbits(8) for _ in range(N_UART)]
    urx = [r.getrandbits(8) for _ in range(N_UART)]
    img = image(can, uart)
    c = boot(img, phase)
    t0 = c.t_run
    node = CanNode(BIT)
    line = UartSender(NOM * 1.02, urx, t0=t0 + 12 * BIT + 30) if uart else UartSender(NOM, [])
    log, sent = [], False
    for k in range(12 * BIT + T_END):
        if k == 12 * BIT:                # after bus integration: both protocols start together
            if can:
                c.host_in[ETX] += can_host_tx(*F_CHIP)
            if uart:
                c.host_in[UTX] += utx
        if can and not sent and k == 12 * BIT + T_NODE:
            node.send(c.cycle + 37, *F_NODE)
            sent = True
        t = c.cycle
        bus = 0 if chip_dom(c) or node.drive(t) == 0 else 1
        node.observe(t, bus)
        log.append((t - t0, bus, chip_dom(c)))
        c.step((line.level(t + 1 - 1e-6) << 7) | 0x08, bus << 4)
    txd, last = [], 1
    for t, uo, _, _ in c.pad_trace:
        if t >= t0 and (uo & 1) != last:
            last = uo & 1
            txd.append((t - t0, last))
    urx_out = [(t - t0, v) for t, v in c.host_out[URX]]
    return dict(c=c, img=img, node=node, log=log, txd=txd, urx=urx_out, utx_data=utx, urx_data=urx,
                line=line, t0=t0)


@pytest.mark.parametrize("phase", [0, 1, 2, 3])
def test_can_uart_together(phase):
    s = scenario(phase)
    c, node, img = s["c"], s["node"], s["img"]
    assert max(img.words) < 512 and len(img.words) == 144 and sorted(img.engines) == [0, 1, 2, 3]
    # CAN: node receives the chip frame (and logs its own), chip receives the node frame and ACKs it; self-reception
    assert node.frames == [(F_CHIP[0], F_CHIP[1], 0), (F_NODE[0], F_NODE[1], 0)] and not node.errors, (node.frames, node.errors)
    assert node.acked is True
    assert tx_events(c) == [0x101]
    assert rx_stream(c) == rx_expect(*F_CHIP) + rx_expect(*F_NODE), [hex(v) for v in rx_stream(c)]
    runs = [t for t, b, d in s["log"] if d]
    t_can0 = runs[0]
    want = can_stuff(can_frame(*F_CHIP))
    lv = {t: b for t, b, _ in s["log"]}
    assert [lv[t_can0 + k * BIT + BIT // 2] for k in range(len(want))] == want
    # UART: TX decoded back to back at exactly 10 BIT, RX bytes in order
    dec = uart_decode(s["txd"], NOM)
    assert [d[1] for d in dec] == s["utx_data"] and all(d[3] for d in dec)
    st = [d[0] for d in dec]
    assert all(b - a == 10 * NOM for a, b in zip(st, st[1:])), sorted({b - a for a, b in zip(st, st[1:])})
    assert [v for _, v in s["urx"]] == s["urx_data"]
    assert all(e.ovr == 0 and e.late == 0 for e in c.eng)
    # true overlap: UART TX and RX start within one character of the chip's SOF and outlast the node's frame
    node_bits = [t for t, b, d in s["log"] if b == 0 and not d and t > T_NODE]
    can_span = (t_can0, node_bits[-1])
    for u0, u1 in ((st[0], st[-1]), (s["line"].starts[0] - s["t0"], s["line"].starts[-1] - s["t0"])):
        assert abs(u0 - can_span[0]) < 10 * NOM and can_span[1] < u1, (u0, u1, can_span)


@pytest.mark.parametrize("phase", [0, 3])
def test_can_uart_timing_equals_alone(phase):
    # every CAN bus level and chip drive, every TXD edge and every RX byte time equal the single-protocol images
    both, can, uart = scenario(phase), scenario(phase, uart=False), scenario(phase, can=False)
    assert both["log"] == can["log"]
    assert both["txd"] == uart["txd"] and both["urx"] == uart["urx"]
    assert rx_stream(both["c"]) == rx_stream(can["c"]) and tx_events(both["c"]) == tx_events(can["c"])
