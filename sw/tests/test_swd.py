# swd_host.s against an independent SWD target model (models.SwdTarget); host side = host/debug.py.
import os
import sys
import pytest
from harness import prog, boot, simulate, ROOT
from models import SwdTarget, parity32

sys.path.insert(0, os.path.join(ROOT, "host"))
import debug                                                          # noqa: E402

DPIDR = 0x3BA11477


class Line:
    # SWCLK/SWDIO from the chip's pads to the target model; the bus level back on uio_in[1] (pull-up when nobody drives)
    def __init__(self, tgt):
        self.tgt, self.seen, self.clash = tgt, 0, 0

    def update(self, c):
        tr = c.pad_trace
        while self.seen < len(tr):
            t, uo, out, oe = tr[self.seen]
            self.seen += 1
            host = (out >> 1 & 1) if oe >> 1 & 1 else None
            self.tgt.edge(t, out >> 3 & 1, host)

    def level(self, t, c):
        h = (c.uio_out >> 1 & 1) if c.uio_oe >> 1 & 1 else None
        g = self.tgt.level(t)
        if h is not None and g is not None:
            self.clash += 1
        return h if h is not None else (g if g is not None else 1)


class Port:
    # debug.py port on the ISS: send() queues host bytes, recv(n) runs until n more bytes came back
    def __init__(self, tgt, bit=10, smp=5, slot=0, img=None):
        self.c = boot(img or prog("swd_host", BIT=bit, SMP=smp), slot)
        self.line, self.got = Line(tgt), 0

    def inputs(self, t, c):
        self.line.update(c)
        return 8, self.line.level(t, c) << 1

    def send(self, b):
        self.c.host_in[0] += list(b)

    def recv(self, n):
        ok = simulate(self.c, 200000, self.inputs, until=lambda ch: len(ch.host_bytes(0)) >= self.got + n)
        assert ok, "no reply"
        r = self.c.host_bytes(0)[self.got:self.got + n]
        self.got += n
        return bytes(r)

    def raw(self, b):
        self.send(b)
        return self.recv(len(b) // 2)


def connect(p, jtag=False):
    p.raw(debug.swd_jtag_to_swd() if jtag else debug.swd_line_reset())
    return debug.swd_xfer(p, "r", 0, 0)


@pytest.mark.parametrize("bit,smp,slot", [(10, 5, 0), (6, 3, 1), (8, 4, 2), (50, 25, 3)])
def test_host_session(bit, smp, slot):
    t = SwdTarget(DPIDR, delay=bit - 4.5)
    p = Port(t, bit, smp, slot)
    assert connect(p) == (1, DPIDR)
    assert debug.swd_xfer(p, "w", 0, 4, 0x50000000) == (1, None)
    assert debug.swd_xfer(p, "w", 0, 8, 0x000000F0) == (1, None)
    assert debug.swd_xfer(p, "w", 1, 0xC, 0xFFFFFFFF) == (1, None)
    assert debug.swd_xfer(p, "w", 1, 4, 0x80000001) == (1, None)
    assert debug.swd_xfer(p, "r", 1, 0xC) == (1, 0xFFFFFFFF)
    assert debug.swd_xfer(p, "r", 1, 4) == (1, 0x80000001)
    assert debug.swd_xfer(p, "r", 0, 4) == (1, 0x50000000)
    p.raw(debug.swd_raw([0] * 8))
    assert t.dp[8] == 0xF0 and t.resets == 1 and t.errors == [] and p.line.clash == 0 and p.c.eng[0].ovr == 0


def test_host_jtag_to_swd_sequence():
    # the bits on the wire: >= 50 ones, 0xE79E LSB first, >= 50 ones, idle; then DPIDR
    t = SwdTarget(DPIDR)
    p = Port(t)
    assert connect(p, jtag=True) == (1, DPIDR)
    bits, prev = [], 1
    for tt, uo, out, oe in p.c.pad_trace:
        if (out >> 3 & 1) and not prev and oe >> 1 & 1:
            bits.append(out >> 1 & 1)
        prev = out >> 3 & 1
    s = "".join(map(str, bits))
    seq = "".join(str((0xE79E >> i) & 1) for i in range(16))
    assert s.startswith("1" * 56 + seq + "1" * 56 + "0" * 8)
    assert t.resets == 2


def test_host_wait_fault_noreply():
    n = {"w": 0}

    def ack(req):
        ap, rnw, a = req
        if ap and a == 0x4:
            n["w"] += 1
            return 2 if n["w"] <= 3 else 1          # WAIT 3x, then OK
        if ap and a == 0x8:
            return 4                                # FAULT
        if ap and a == 0xC:
            return None                             # no reply
        return 1
    t = SwdTarget(DPIDR, ack=ack)
    p = Port(t, slot=1)
    connect(p)
    assert debug.swd_xfer(p, "w", 1, 4, 0x1234) == (1, None) and n["w"] == 4
    p.raw(debug.swd_raw([0] * 8))                                                  # idle: the data phase is done
    assert t.ap[4] == 0x1234
    assert debug.swd_xfer(p, "w", 1, 8, 0x55) == (4, None) and 8 not in t.ap      # write bytes dropped by the chip
    assert debug.swd_xfer(p, "r", 1, 8) == (4, None)
    assert debug.swd_xfer(p, "r", 1, 0xC) == (7, None)                             # line high: no ACK
    p.raw(debug.swd_line_reset())                                                  # recover
    assert debug.swd_xfer(p, "r", 0, 0) == (1, DPIDR)
    assert p.line.clash == 0 and p.c.eng[0].ovr == 0


def test_host_read_parity_error_detected():
    t = SwdTarget(DPIDR, bad_parity=lambda r: r[0] == 1)
    p = Port(t, slot=2)
    connect(p)
    t.ap[0] = 0x0BAD0BAD
    assert debug.swd_xfer(p, "r", 1, 0) == ("parity", 0x0BAD0BAD)
    assert debug.swd_xfer(p, "r", 0, 0) == (1, DPIDR)


def test_host_bad_write_parity_seen_by_target():
    # negative: a write with a wrong parity byte reaches the target as such (the chip sends what the host gives)
    t = SwdTarget(DPIDR)
    p = Port(t, slot=3)
    connect(p)
    w = bytearray(debug.swd_write(1, 4, 0x77))
    w[5] ^= 1
    p.send(bytes(w))
    assert p.recv(1)[0] == 1
    p.raw(debug.swd_raw([0] * 8))
    assert t.errors == ["write parity"] and 4 not in t.ap


def test_host_targetsel_multidrop():
    # v2 multidrop: line reset, TARGETSEL (no ACK: 5 released cycles), DPIDR; a wrong TARGETSEL deselects
    for tsel, ok in ((0x01002927, True), (0x11002927, False)):
        t = SwdTarget(DPIDR, multidrop=True, tsel=0x01002927)
        p = Port(t)
        p.raw(debug.swd_line_reset())
        p.raw(debug.swd_targetsel(tsel))
        r = debug.swd_xfer(p, "r", 0, 0)
        assert r == ((1, DPIDR) if ok else (7, None)) and t.selected == ok and p.line.clash == 0


@pytest.mark.parametrize("bit", [6, 10, 20])
def test_host_target_answer_window(bit):
    # header: the target may drive BIT - 4 clk after the rising edge at its pin; 1 clk more is too late
    for d, good in ((bit - 4 - 0.5, True), (bit - 2 + 0.2, False)):
        t = SwdTarget(DPIDR, delay=d)
        p = Port(t, bit, bit // 2)
        p.raw(debug.swd_line_reset())
        p.send(debug.swd_read(0, 0))
        r = p.recv(1)[0]
        assert (r == 1) == good, (bit, d, r)


def test_host_timing():
    # SWCLK high/low exactly BIT - SMP / SMP inside a word, idles high; SWDIO driven by the chip changes only while
    # SWCLK is low; never driven during the turnaround and target cycles
    t = SwdTarget(DPIDR)
    p = Port(t, 12, 5)
    connect(p)
    debug.swd_xfer(p, "w", 1, 0, 0xA5A5A5A5)
    prev, fall = None, None
    lows, highs = set(), set()
    last = None
    for tt, uo, out, oe in p.c.pad_trace:
        ck = out >> 3 & 1
        if prev is not None and ck != prev:
            if ck == 0:
                fall = tt
                if last is not None:
                    highs.add(tt - last)
            else:
                lows.add(tt - fall)
                last = tt
        if prev is not None and oe >> 1 & 1 and ck == 1 and (out ^ pout) & 2:
            assert False, "SWDIO moved while SWCLK high at %d" % tt
        prev, pout = ck, out
    assert lows == {5} and min(highs) == 7 and p.line.clash == 0


def scenario(img, slot, pp=0):
    # T5/T6: line reset, DPIDR, WAIT then OK write, FAULT, read, raw sample
    n = {"w": 0}

    def ack(req):
        if req[0] and req[2] == 4:
            n["w"] += 1
            return 2 if n["w"] == 1 else 1
        return 4 if req[0] and req[2] == 8 else 1
    t = SwdTarget(DPIDR, ack=ack)
    p = Port(t, slot=slot, img=img)
    assert connect(p) == (1, DPIDR)
    assert debug.swd_xfer(p, "w", 1, 4, 0x1234 + pp) == (1, None)
    assert debug.swd_xfer(p, "w", 1, 8, 1) == (4, None)
    assert debug.swd_xfer(p, "r", 1, 4) == (1, 0x1234 + pp)
    p.raw(debug.swd_raw([0] * 3) + debug.swd_raw([1] * 4, release=True))
    return p.c
