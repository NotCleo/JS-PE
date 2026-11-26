# swd_host.s against an independent SWD target model (models.SwdTarget); host side = host/debug.py.
import os
import sys
import pytest
from harness import prog, boot, simulate, ROOT
from models import SwdTarget, parity32, level_at

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
    # header: a target driving BIT - 4.5 clk after the rising edge at its pin is read; BIT - 1.8 clk is too late
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


# ---------------- swd_dp.s (DP emulator) driven by models.SwdHost, AP box served by host/debug.py DpBox ----------------
from models import SwdHost                                            # noqa: E402
from harness import PROG                                              # noqa: E402
from jspe_asm import Assembler                                        # noqa: E402

DP_ID = 0x0C001001
BOX0 = 455


class DpBench:
    def __init__(self, img, ops, T, slot=0, lat=300, ap=None, fail=(), sample="rise", gap=0, engines=((0, 1),),
                 t0=60.0):
        self.c = boot(img, slot)
        self.host = SwdHost(T, ops, t0=self.c.cycle + t0, sample=sample, gap=gap)
        self.boxes = {e: debug.DpBox(BOX0 + 5 * e, ap, fail) for e, _ in engines}
        self.pads = dict(engines)                      # engine -> SWDIO pad
        self.seen = {e: 0 for e in self.boxes}
        self.due, self.ch, self.n, self.clash = [], [], 0, 0
        self.lat = lat

    def chip_drive(self, c):
        if sum(c.uio_oe >> p & 1 for p in self.pads.values()) > 1:
            self.clash += 1                            # two DPs driving at once
        for p in self.pads.values():
            if c.uio_oe >> p & 1:
                return c.uio_out >> p & 1
        return None

    def line(self, t):
        return level_at(self.ch, t, 1)

    def inputs(self, t, c):
        tr = c.pad_trace
        while self.n < len(tr):
            tt, uo, out, oe = tr[self.n]
            self.n += 1
            v = [out >> p & 1 for p in self.pads.values() if oe >> p & 1]
            lv = v[0] if v else 1
            if not self.ch or self.ch[-1][1] != lv:
                self.ch.append((tt, lv))
        for e, box in self.boxes.items():
            out = c.host_out[e]
            while self.seen[e] < len(out):
                self.due.append((c.cycle + self.lat, e, out[self.seen[e]][1]))
                self.seen[e] += 1
        while self.due and self.due[0][0] <= c.cycle:
            _, e, req = self.due.pop(0)

            def wr(a, ws):
                for i, w in enumerate(ws):
                    c.mem[a + i] = w
            self.boxes[e].serve(req, lambda a, n: [c.mem[a + i] for i in range(n)], wr)
        ck, d = self.host.at(t, self.line)
        cd = self.chip_drive(c)
        if d is not None and cd is not None:
            self.clash += 1
        lv = d if d is not None else (cd if cd is not None else 1)
        return 8, ck << 3 | sum(lv << p for p in self.pads.values())

    def run(self, limit=400000):
        ok = simulate(self.c, limit, self.inputs, until=lambda ch: self.host.done_at is not None
                      and ch.cycle > self.host.done_at + 20 and not self.due)
        assert ok, "SWD host did not finish"
        return self.host.results


def dp_img(**d):
    return prog("swd_dp", **d)


CONNECT = [("reset",), ("idle", 2), ("r", 0, 0)]


@pytest.mark.parametrize("T,slot,sample", [(100, 0, "rise"), (60, 1, "fall"), (50, 2, "rise"), (200, 3, "fall")])
def test_dp_session(T, slot, sample):
    ops = CONNECT + [("w", 0, 4, 0x50000000), ("r", 0, 4), ("w", 0, 8, 0x01000010),
                     ("w", 1, 0x4, 0xCAFEF00D), ("r", 1, 0x4), ("r", 1, 0xC), ("r", 0, 0xC), ("idle", 4)]
    b = DpBench(dp_img(), ops, T, slot, sample=sample, ap={(1, 0x1C): 0x12345678})
    r = b.run()
    assert r[0] == (1, DP_ID, True)
    assert r[2] == (1, 0xF0000000, True)               # power-up ACKs mirror the requests
    assert r[5] == (1, 0, True)                        # first AP read: posted (previous value, 0)
    assert r[6] == (1, 0xCAFEF00D, True)               # AP read of 0x1C returns the 0x14 read
    assert r[7] == (1, 0x12345678, True)               # RDBUFF: the 0x1C read
    assert all(x[0] == 1 for x in r) and b.clash == 0 and b.c.eng[0].ovr == 0
    box = b.boxes[0]
    assert box.select == 0x01000010 and box.ap[(1, 0x14)] == 0xCAFEF00D


def test_dp_wait_while_host_busy():
    # host latency 3000 clk: the AP write and the following AP read get WAIT until the box is served
    ops = CONNECT + [("w", 1, 0x0, 0x11), ("r", 1, 0x0), ("r", 0, 0xC)]
    b = DpBench(dp_img(), ops, 60, 1, lat=3000)
    r = b.run()
    assert [x[0] for x in r] == [1, 1, 1, 1] and r[3] == (1, 0x11, True) and b.host.waits >= 2


def test_dp_fault_sticky_and_abort():
    # write parity error -> WDATAERR, FAULT for AP/CTRL-STAT writes, DPIDR and CTRL/STAT reads still OK,
    # ABORT WDERRCLR clears; an AP error from the host -> STICKYERR, cleared by STKERRCLR
    ops = CONNECT + [("w", 1, 0, 5, 1), ("r", 1, 0), ("w", 0, 4, 0), ("r", 0, 4), ("r", 0, 0), ("w", 0, 0, 0x8),
                     ("r", 1, 0), ("w", 0, 8, 0x0F000000), ("w", 1, 4, 1), ("r", 0, 0xC), ("r", 0, 4),
                     ("w", 1, 4, 2), ("w", 0, 0, 0x4), ("r", 0, 4), ("w", 0, 8, 0), ("w", 1, 4, 3)]
    b = DpBench(dp_img(), ops, 80, 2, fail={(0x0F, 4)})
    r = b.run()
    acks = [x[0] for x in r]
    assert acks == [1, 1, 4, 4, 1, 1, 1, 1, 1, 1, 4, 1, 4, 1, 1, 1, 1], acks   # RDBUFF after the AP error: FAULT
    assert r[4][1] == 0x80                             # CTRL/STAT: WDATAERR
    assert r[11][1] == 0x20                            # STICKYERR after the failed AP write
    assert r[14][1] == 0 and b.boxes[0].ap.get((0, 4)) == 3 and b.c.eng[0].ovr == 0


def test_dp_protocol_errors_and_line_reset():
    # bad request parity / stop / park -> no reply (line high, ACK 7) until a line reset; after a line reset only a
    # DPIDR read is answered; JTAG-to-SWD sequence works as a line reset
    seq = [(0xE79E >> i) & 1 for i in range(16)]
    ops = CONNECT + [("bits", [1, 0, 1, 0, 0, 0, 0, 1]),              # DPIDR read with a parity error
                     ("r", 0, 0), ("reset",), ("idle", 2), ("r", 0, 4), ("reset",), ("idle", 2), ("r", 0, 0),
                     ("r", 0, 4), ("bits", [1, 0, 1, 0, 0, 1, 1, 1]),  # stop bit 1
                     ("r", 0, 0), ("reset",), ("bits", seq), ("reset",), ("idle", 3), ("r", 0, 0)]
    b = DpBench(dp_img(), ops, 80, 3)
    r = b.run()
    assert [x[0] for x in r] == [1, 7, 7, 1, 1, 7, 1], r
    assert b.clash == 0 and b.c.eng[0].ovr == 0


def test_dp_back_to_back_writes_reads():
    # no idle cycles between transactions, AP box latency 200 clk
    ops = CONNECT + [("w", 0, 4, 0x50000000)] + [("w", 1, 0x8, k) for k in range(4)] + [("r", 0, 4)] * 3
    b = DpBench(dp_img(), ops, 60, 0, lat=200)
    r = b.run()
    assert [x[0] for x in r] == [1] * 9 and r[-1] == (1, 0xF0000000, True) and b.clash == 0


def mdrop_img():
    src = open(os.path.join(PROG, "swd_dp.s")).read()
    return Assembler([("swd_dp.s", src), ("swd_dp1.s", src)],
                     {"swd_dp.s:MDROP": "1", "swd_dp.s:ORG": "0", "swd_dp1.s:MDROP": "1", "swd_dp1.s:ENG": "1",
                      "swd_dp1.s:ORG": "212",
                      "swd_dp1.s:DIO": "4", "swd_dp1.s:TSEL": "0x1C1E0001", "swd_dp1.s:DPIDR": "0x2C002001"}).run()


def test_dp_multidrop_two_targets():
    # SWD v2: two DPs (E0 on uio[1], E1 on uio[4]) on one bus; TARGETSEL picks one, no ACK driven by either; an
    # unknown TARGETSEL leaves the bus silent
    ops = [("reset",), ("idle", 2), ("tsel", 0x0C1E0001), ("r", 0, 0), ("w", 1, 0, 0xA), ("idle", 2),
           ("reset",), ("idle", 2), ("tsel", 0x1C1E0001), ("r", 0, 0), ("w", 1, 0, 0xB), ("idle", 2),
           ("reset",), ("idle", 2), ("tsel", 0x5C1E0001), ("r", 0, 0)]
    b = DpBench(mdrop_img(), ops, 120, 0, engines=((0, 1), (1, 4)))
    r = b.run()
    assert r[0] == (1, 0x0C002001, True) and r[2] == (1, 0x2C002001, True) and r[4][0] == 7, r
    assert b.boxes[0].ap == {(0, 0): 0xA} and b.boxes[1].ap == {(0, 0): 0xB} and b.clash == 0


MIX = CONNECT + [("w", 0, 4, 0x50000000), ("r", 0, 4), ("w", 0, 8, 0x0F000000), ("w", 1, 4, 1), ("r", 1, 4),
                 ("r", 0, 0xC), ("w", 1, 0, 0xFFFFFFFF, 1), ("r", 1, 0), ("w", 0, 0, 0x1E), ("r", 1, 0), ("r", 0, 0xC),
                 ("w", 1, 8, 0), ("r", 0, 0xC), ("w", 0, 4, 0x50000000), ("w", 0, 0xC, 1), ("r", 0, 0), ("w", 0, 0, 4), ("r", 0, 4),
                 ("w", 0, 0xC, 1)]
MIX_ACKS = [1, 1, 1, 1, 1, 1, 1, 1, 4, 1, 1, 1, 1, 4, 4, 4, 1, 1, 1, 1]  # CTRL/STAT, TARGETSEL write while sticky: FAULT     # TARGETSEL outside a line reset: ignored, OK


def dp_scenario(img, slot, T=100, gap=0, sample="fall", lat=150):
    # every path: posted AP reads, WAIT, FAULT (write parity, AP error), ABORT clears, RDBUFF; T6 and the rate test
    md = any(k[1] == "etsel" for k in img.labels)
    ops = ([("reset",), ("idle", 2), ("tsel", 0x5C1E0001), ("r", 0, 0), ("reset",), ("idle", 2),
            ("tsel", 0x0C1E0001)] if md else []) + MIX
    b = DpBench(img, ops, T, slot, sample=sample, gap=gap, lat=lat, fail={(0x0F, 8)})
    r = b.run()
    acks = [x[0] for x in r]
    assert acks == ([7] if md else []) + MIX_ACKS, acks
    assert b.clash == 0 and b.c.eng[0].ovr == 0
    return b.c


@pytest.mark.parametrize("T,gap,slot", [(64, 0, 0), (64, 0, 1), (64, 0, 2), (64, 0, 3), (48, 2, 0), (48, 2, 3)])
def test_dp_rate(T, gap, slot):
    # header rule: SWCLK period >= 64 clk back to back, >= 48 clk with >= 2 idle cycles after each transaction;
    # the host samples right after the falling edge (strict)
    dp_scenario(dp_img(), slot, T, gap)


def test_dp_rate_too_fast_fails():
    # negative: 60 clk back to back breaks the ABORT-after-AP-error path (no reply)
    with pytest.raises(AssertionError):
        dp_scenario(dp_img(), 0, 60, 0)


def test_dp_csw_waits_while_busy_and_tsel_parity():
    # CTRL/STAT write while the box is busy: WAIT (only DPIDR / CTRL-STAT reads and ABORT skip it); MDROP: a TARGETSEL
    # with a data parity error deselects (both DPs silent), no WDATAERR
    ops = CONNECT + [("w", 1, 0, 1), ("w", 0, 4, 0x50000000), ("r", 0, 4)]
    b = DpBench(dp_img(), ops, 80, 1, lat=4000)
    r = b.run()
    assert [x[0] for x in r] == [1, 1, 1, 1] and r[3][1] == 0xF0000000 and b.host.waits >= 1
    bad = [1, 0, 0, 1, 1, 0, 0, 1] + [None] * 5 + [(0x0C1E0001 >> i) & 1 for i in range(32)] + [0]   # parity wrong (7 ones)
    ops = [("reset",), ("idle", 2), ("bits", bad), ("r", 0, 0), ("reset",), ("idle", 2), ("tsel", 0x0C1E0001),
           ("r", 0, 0), ("r", 0, 4)]
    b = DpBench(mdrop_img(), ops, 120, 0, engines=((0, 1), (1, 4)))
    r = b.run()
    assert r[0][0] == 7 and r[1] == (1, 0x0C002001, True) and r[2] == (1, 0, True), r   # no WDATAERR
    assert b.clash == 0
