# SPDX-FileCopyrightText: © 2026 NotCleo
# SPDX-License-Identifier: Apache-2.0
# Manchester system tests (VERIF_PLAN 7.6 M1-M4; program not designed in, zero RTL diff): manchester.s TX on E0
# (uo_out[3]), RX on E1 (ui_in[4]), loaded through the host link; independent edge-time sender and decoder (IEEE 802.3:
# 1 = low then high); memory pre-filled with random words; pads == ISS every clock (test_uart Bench).
# GATES=yes: the G subset (M4, one polarity).

import bisect

import cocotb

from test_uart import Bench, ESC, GL                                 # noqa: E402 (sets sys.path)
from test_ws2812 import PROG, image, nbench                    # noqa: E402
from models import ManchesterSender, level_at                        # noqa: E402

SFD = 0xD5
EV4 = ("ev", 4)


class MLine:
    # ui_in[4] as driven by the partner: idle low, edges (t, level) in time order
    def __init__(self):
        self.ed, self.t, self.end = [], [], 0.0

    def add(self, edges, end):
        assert not self.ed or edges[0][0] >= self.end
        self.ed += edges
        self.t += [e[0] for e in edges]
        self.end = end

    def level(self, t):
        k = bisect.bisect_right(self.t, t) - 1
        return 0 if k < 0 else self.ed[k][1]


class MBench(Bench):
    def __init__(self, dut, seed):
        super().__init__(dut, seed)
        self.mline, self.loop = MLine(), False
        self.m_ch, self.m_lv = [], 0              # uo_out[3] (TX line)

    def pads(self, t):
        c = self.chip
        tx = c.uo >> 3 & 1
        if tx != self.m_lv:
            self.m_lv = tx
            self.m_ch.append((t, tx))
        rx = tx if self.loop else self.mline.level(t + 1 - 1e-6)
        return 0x80 | rx << 4 | self.hl.level(c) << 3, (~c.uio_oe | c.uio_out) & 0xFF

    def frame(self, data, bit, rate=0.0, jit=0.0, alt=False, gap=4, cut=None, drop=(), pre_bits=()):
        # sender `gap` bits after the line's last edge; jitter: both edges of bit k moved by the same uniform
        # +-jit, or alternating -jit / +jit (alt: every mid-bit edge 2 jit early or late against one bit after the
        # previous one); drop: bits without their mid-bit edge; cut = (edge index, level, clk): line stuck at level
        # from that edge (2k = boundary, 2k + 1 = middle of bit k) for clk, then idle low
        t0 = max(self.now + 50, self.mline.end + gap * bit) + self.r.random()
        rnd = self.r
        jf = (lambda k: jit if k % 2 else -jit) if alt else (lambda k: rnd.uniform(-jit, jit))
        s = ManchesterSender(bit * (1 + rate), data, t0=t0, jitter=jf if jit else None, pre_bits=pre_bits)
        ed = [e for k, e in enumerate(s.edges) if not (k % 2 and k // 2 in drop)]
        end = s.end
        if cut is not None:
            e, lv, n = cut
            tc = s.edges[e][0]
            ed = [e for e in ed if e[0] < tc] + [(tc, lv), (tc + n, 0)]
            end = tc + n
        self.mline.add(ed, end)
        return end


def m_img(bit=50, inv=0, cv=1):
    # inv: the other line polarity, on both sides (header: `inv` on the TX pad, bind invert on RX)
    txt = open(PROG + "/manchester.s").read()
    if inv:
        for a, z in ((".pad uo3, p0", ".pad uo3, p0, inv"), ("li    R0, #4  ", "li    R0, #0x24")):
            assert txt.count(a) == 1, a
            txt = txt.replace(a, z)
    return image("manchester", txt, BIT=bit, CVCHK=cv)


async def m_load(b, img, bit):
    b.loop = False
    await b.load(img)
    await b.run(4 * bit)                         # >= 2 bits after the pad-enable edge (header, D053)
    b.m_ch = []
    b.clear()


def ev_count(b):
    return sum(1 for x in b.stream(1) if x == EV4)


async def rx_wait(b, nev, bit, what):
    await b.run(10 ** 6, lambda: b.now > b.mline.end + 3 * bit and ev_count(b) >= nev, what)
    await b.run(4 * bit)


def payload(b, n):
    d = [b.r.getrandbits(8) for _ in range(n)]
    if n > 4:
        d[n // 2] = ESC
    return d


# ---------------- independent TX decoder
def tx_check(ch, frames, bit, inv=0):
    # split the line at quiet gaps > 2 bits; per frame: every edge on the half-bit grid of one continuous bit grid
    # (no gap between bytes), first half = complement, second half = bit value, line back to idle after the last bit
    segs, cur = [], []
    for e in ch:
        if cur and e[0] - cur[-1][0] > 2 * bit:
            segs.append(cur)
            cur = []
        cur.append(e)
    if cur:
        segs.append(cur)
    assert len(segs) == len(frames), ("TX frames on the line", len(segs), len(frames))
    for seg, data in zip(segs, frames):
        bits = [(x >> (7 - i)) & 1 for x in data for i in range(8)]
        ts = seg[0][0] - (bit // 2 if bits[0] else 0)
        off = [t - ts for t, _ in seg if (t - ts) % (bit // 2)]
        assert not off, ("TX edge off the half-bit grid", off[:4])
        for k, v in enumerate(bits):
            lo = level_at(seg, ts + k * bit + bit / 4, inv) ^ inv
            hi = level_at(seg, ts + k * bit + 3 * bit / 4, inv) ^ inv
            assert (lo, hi) == (1 - v, v), ("TX bit %d of %d: halves %d %d, bit %d" % (k, len(bits), lo, hi, v))
        assert seg[-1][1] == inv and seg[-1][0] <= ts + len(bits) * bit, ("TX line not idle after the frame", seg[-1])
    return len(segs)


# ---------------- M1: transmitter
@cocotb.test()
async def test_m1_tx(dut):
    # frames of 1, 2, 64 bytes at BIT 50 (1 Mbit/s) and 22 (2.27 Mbit/s); host bytes arrive behind H_RDY
    b = await nbench(dut, 91, cls=MBench)
    for bit in (50, 22):
        await m_load(b, m_img(bit), bit)
        frames = [payload(b, n) for n in (1, 2, 64)]
        frames[0][0] |= 0x80                    # first bit 1 and 0 both covered
        frames[1][0] &= 0x7F
        for f in frames:
            b.send(0, f)
            await b.run(bit * (8 * len(f) + 8) + 400)
        tx_check(b.m_ch, frames, bit)
        assert b.stream(1) == [] and b.notes() == []
        cocotb.log.info("M1 BIT %d: frames %s bytes, every edge on the grid", bit, [len(f) for f in frames])


# ---------------- M2: receiver
@cocotb.test()
async def test_m2_rx(dut):
    # BIT 50, 22, 18: preamble 0x55 (first bit 0: first edge is a bit boundary, half-bit-off lock, D038) and 0xAA;
    # payload 64 and 1 bytes; sender +-2 %; jitter (BIT/4 - 2)/2 per bit, random and alternating (every mid-bit edge
    # BIT/4 - 2 off one bit after the previous one); exactly 2 preamble bits; sub-clock start phase
    b = await nbench(dut, 92, cls=MBench)
    for bit in (50, 22, 18):
        await m_load(b, m_img(bit), bit)
        jit = (bit / 4 - 2) / 2
        np = 7 if bit == 50 else 2
        exp = []
        cases = [([0x55] * np, (), 64, 0.02, False), ([0xAA] * np, (), 1, -0.02, False),
                 ([0x55] * np, (), 1, -0.02, False), ([0xAA] * np, (), 64, 0.02, False),
                 ([0x55] * np, (), 8, 0.02, True), ([0xAA] * np, (), 8, -0.02, True),
                 ([], (0, 1), 2, 0.02, False), ([], (1, 0), 2, -0.02, False)]
        for pre, pb, n, rate, alt in cases:
            p = payload(b, n)
            b.frame(pre + [SFD] + p, bit, rate, jit, alt, pre_bits=pb)
            exp += p + [EV4]
        await rx_wait(b, len(cases), bit, "M2 frames")
        assert b.stream(1) == exp, ("M2 BIT %d" % bit, b.stream(1), exp)
        assert b.notes() == []
        cocotb.log.info("M2 BIT %d: %d frames (0x55 / 0xAA / 2 preamble bits, 64 / 8 / 2 / 1 bytes), jitter +-%.2f "
                        "clk random and alternating, +-2 %%", bit, len(cases), jit)


# ---------------- M3: receiver errors
@cocotb.test()
async def test_m3_rx_errors(dut):
    # no delimiter; line stuck low / high in mid-frame; frames 3 bits apart; a missing mid-bit transition;
    # H_TX blocked by an RDMEM reply (OVR, byte alignment kept)
    b = await nbench(dut, 93, cls=MBench)
    bit = 50
    await m_load(b, m_img(bit), bit)
    p = payload(b, 3)
    # CVCHK 1 (default, D099): no delimiter -> no event (hunting timeouts are silent)
    b.frame([0x55] * 3, bit)
    await rx_wait(b, 0, bit, "no delimiter")
    assert b.stream(1) == [], b.stream(1)
    # stuck 10 bits inside the SFD (no event), at a bit boundary and in the middle of a bit of the third / second
    # byte: partial byte dropped, one event 4 (the release edge of a high line is a silent hunting timeout)
    for e, lv, exp in ((2 * 11, 0, []), (2 * 12 + 1, 1, []), (2 * 36, 0, p[:2] + [EV4]), (2 * 36, 1, p[:2] + [EV4]),
                       (2 * 29 + 1, 0, p[:1] + [EV4]), (2 * 29 + 1, 1, p[:1] + [EV4])):
        b.clear()
        b.frame([0x55, SFD] + p, bit, cut=(e, lv, 10 * bit))
        b.frame([0x55, SFD, 0x77], bit)
        await rx_wait(b, exp.count(EV4) + 1, bit, "stuck %d at edge %d" % (lv, e))
        assert b.stream(1) == exp + [0x77, EV4], ("stuck", lv, e, b.stream(1))
    # frames 3 bits apart
    b.clear()
    b.frame([0x55, SFD, 0x01], bit)
    b.frame([0x55, SFD, 0x02, 0x03], bit, gap=3)
    await rx_wait(b, 2, bit, "frames 3 bits apart")
    assert b.stream(1) == [0x01, EV4, 0x02, 0x03, EV4], b.stream(1)
    # missing mid-bit transition, CVCHK 1: the frame ends there with one event 4; bytes before intact, a byte whose
    # last bit is hit is already complete; next frame complete. Also with +-2 % (VERIF M3, D099)
    q = [0x12, 0xA5, 0x3C, 0x00]
    for k, rate in ((16, 0), (18, 0.02), (23, 0), (27, -0.02), (31, 0), (37, 0.02), (44, -0.02), (47, 0)):
        b.clear()
        b.frame([0x55, SFD] + q, bit, rate, drop=(k,))
        b.frame([0x55, SFD, 0x77], bit)
        await rx_wait(b, 2, bit, "missing transition at bit %d" % k)
        good = (k - 16 + 1) // 8
        assert b.stream(1) == q[:good] + [EV4, 0x77, EV4], ("CVCHK missing transition", k, b.stream(1))
    # CVCHK 0 (D098): no delimiter -> event 4; stuck high: + an empty frame; missing transition: <= 4 junk bytes
    await m_load(b, m_img(bit, cv=0), bit)
    b.frame([0x55] * 3, bit)
    await rx_wait(b, 1, bit, "no delimiter, CVCHK 0")
    assert b.stream(1) == [EV4], b.stream(1)
    b.clear()
    b.frame([0x55, SFD] + p, bit, cut=(2 * 36, 1, 10 * bit))
    b.frame([0x55, SFD, 0x77], bit)
    await rx_wait(b, 3, bit, "stuck high, CVCHK 0")
    assert b.stream(1) == p[:2] + [EV4, EV4, 0x77, EV4], b.stream(1)
    seen = []
    for k in (18, 27, 47):
        b.clear()
        b.frame([0x55, SFD] + q, bit, drop=(k,))
        b.frame([0x55, SFD, 0x77], bit)
        await rx_wait(b, 2, bit, "missing transition at bit %d, CVCHK 0" % k)
        s, good = b.stream(1), (k - 16) // 8
        bad = s[good:-2]
        assert s[-2:] == [0x77, EV4] and bad[-1] == EV4 and s[:good] == q[:good], ("missing transition", k, s)
        assert bad.count(EV4) <= 2 and len(bad) - bad.count(EV4) <= 4, ("missing transition", k, s)
        seen.append((k, [hex(x) if x != EV4 else "ev4" for x in bad]))
    cocotb.log.info("M3 CVCHK 0 missing transition (bit, stream after the intact bytes): %s", seen)
    await m_load(b, m_img(bit), bit)
    # H_TX blocked: 512-byte RDMEM reply (~20 k clk at B = 4) while a 64-byte frame arrives (400 clk per byte)
    b.clear()
    b.rd_len = 512
    b.hl.send(bytes([ESC, 0x71, 0, 0, 255]))
    await b.run(400)
    q = payload(b, 64)
    b.frame([0x55, 0x55, SFD] + q, bit)
    await rx_wait(b, 1, bit, "frame behind RDMEM")
    x, rep = b.replies.pop(0)
    assert x == 0xC1 and len(rep) == 512
    r = b.rx_data(1)
    k = next(i for i in range(len(r) + 1) if i == len(r) or r[i] != q[i])
    assert 0 < k < len(r) and r[k:] == q[len(q) - (len(r) - k):], ("alignment after the overrun", k, r)
    assert b.stream(1) == r + [EV4] and b.notes() == [0x81], (b.stream(1), b.notes())
    assert await b.rdreg(0x09) >> 11 & 1, "EST1 overrun not set"
    cocotb.log.info("M3 H_TX blocked: %d of 64 bytes delivered (%d + %d), OVR set", len(r), k, len(r) - k)
    await b.op([ESC, 0x72, 0x06, 0x02, 0])        # ECLR E1 overrun
    assert not await b.rdreg(0x09) >> 11 & 1
    b.items = []                                 # the 0x81 notice was expected
    b.frame([0x55, SFD, 0x5A, 0xA5], bit)
    await rx_wait(b, 1, bit, "frame after the overrun")
    assert b.stream(1) == [0x5A, 0xA5, EV4] and b.notes() == []


# ---------------- M4: loop-back E0 -> E1
@cocotb.test()
async def test_m4_loopback(dut):
    # uo_out[3] wired to ui_in[4]: 802.3 preamble + SFD + payload through TX and RX; both polarities at 1 Mbit/s,
    # BIT 22 (2.27 Mbit/s); line checked by the TX decoder too
    b = await nbench(dut, 94, cls=MBench)
    cases = [(50, 0)] if GL else [(50, 0), (50, 1), (22, 0)]
    for bit, inv in cases:
        b.clear()
        b.loop = False
        await b.load(m_img(bit, inv))
        b.loop = True
        await b.run(4 * bit)
        # a reload that changes the idle level (pad enable / polarity edge): a silent hunting timeout (CVCHK 1)
        assert b.stream(1) == [], b.stream(1)
        b.clear()
        b.m_ch = []
        p = payload(b, 4 if GL else 16)
        f = [0x55] * 7 + [SFD] + p
        b.send(0, f)
        await b.run(10 ** 6, lambda: ev_count(b) >= 1, "loop-back frame")
        await b.run(4 * bit)
        assert b.stream(1) == p + [EV4], (bit, inv, b.stream(1))
        tx_check(b.m_ch, [f], bit, inv)
        assert b.notes() == [] and not any(g.ovr for g in b.chip.eng)
        cocotb.log.info("M4 BIT %d inv %d: %d payload bytes through E0 -> E1", bit, inv, len(p))
