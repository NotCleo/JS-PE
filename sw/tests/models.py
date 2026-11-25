# Independent protocol models (written from the protocol standards; no code shared with the ISS or assembler).
# Time unit: clk (20 ns). A chip pad register written at the end of clock c is valid from time c + 1.
# The chip samples an input at the edge ending clock c, i.e. at time c + 1.

import bisect

NS = 20.0


def trace_levels(pad_trace, getbit, init=0):
    # pad_trace: [(time, uo, uio_out, uio_oe)] -> [(time, level)] for one derived signal
    out, last = [], init
    for t, uo, uout, uoe in pad_trace:
        v = getbit(uo, uout, uoe)
        if v != last:
            out.append((t, v))
            last = v
    return out


def level_at(changes, t, init=0):
    v = init
    for tc, lv in changes:
        if tc > t:
            break
        v = lv
    return v


# ---------------- UART ----------------
class UartSender:
    def __init__(self, bit, chars, dbits=8, parity=0, stop=1.0, t0=10.0, gap=0.0, inv=False, bad_stop=(),
                 bad_parity=(), breaks=(), narrow=(), win=0.0):
        # narrow: characters whose stop bit is high only within +-win of its centre (clipped to the stop bit's start);
        # the next start follows at once, so stop and gap do not apply to these characters
        self.edges = []          # (time, level), line idle high
        self.starts = []
        t = t0
        for k, c in enumerate(chars):
            if k in breaks:
                self.edges.append((t, 0))
                t += bit * (dbits + 2 + (1 if parity else 0)) * 3
                self.edges.append((t, 1))
                self.starts.append(None)
                t += bit * 2
                continue
            self.starts.append(t)
            bits = [0] + [(c >> i) & 1 for i in range(dbits)]
            if parity:
                p = sum(bits[1:]) & 1
                p = p if parity == 1 else 1 - p
                if k in bad_parity:
                    p ^= 1
                bits.append(p)
            if k in narrow:
                assert bits[-1] == 0 and k not in bad_stop, "narrowed stop needs a 0 bit before it"
                for b in bits:
                    self.edges.append((t, b))
                    t += bit
                c = t + bit / 2
                self.edges.append((max(c - win, t), 1))
                t = c + win
                continue
            bits.append(0 if k in bad_stop else 1)
            for b in bits:
                self.edges.append((t, b))
                t += bit
            self.edges.append((t, 1))
            t += bit * (stop - 1) + gap
        self.end = t
        self.inv = inv

    def level(self, t):
        v = level_at(self.edges, t, 1)
        return 1 - v if self.inv else v


def uart_decode(changes, bit, dbits=8, parity=0, init=1):
    # decode a line from its changes; returns [(start_time, value, parity_ok, stop_ok)]
    out, k = [], 0
    while k < len(changes):
        t, v = changes[k]
        if v != 0:
            k += 1
            continue
        st = t
        def lv(x):
            return level_at(changes, x, init)
        bits = [lv(st + bit * (i + 1.5)) for i in range(dbits)]
        val = sum(b << i for i, b in enumerate(bits))
        pos = st + bit * (dbits + 1.5)
        pok = True
        if parity:
            p = lv(pos)
            pok = ((sum(bits) + p) & 1) == (0 if parity == 1 else 1)
            pos += bit
        sok = lv(pos) == 1
        out.append((st, val, pok, sok))
        end = pos + bit * 0.4
        while k < len(changes) and changes[k][0] <= end:
            k += 1
        if not sok:
            while k < len(changes) and changes[k][1] == 0:
                k += 1
    return out


# ---------------- SPI ----------------
class SpiController:
    # Drives SCK/CS/MOSI in continuous time; samples MISO from the chip's pad history.
    def __init__(self, T, mode=0, tx=(), phase=0.0, t0=20.0, cs_setup=None, cs_hold=None, gap=0.0, cs_cuts=None):
        self.T, self.mode, self.cpol, self.cpha = T, mode, mode >> 1, mode & 1
        self.tx = list(tx)
        self.t0 = t0 + phase
        cs_setup = T if cs_setup is None else cs_setup
        self.cs_lo = self.t0
        self.first = self.t0 + cs_setup
        self.gap = gap
        n = len(self.tx) * 8
        self.nbits = n
        # leading edge of bit i (mode 0: rising, samples), trailing edge half a period later
        self.lead = [self.first + i * T + (i // 8) * gap + (T / 2 if self.cpha else 0) for i in range(n)]
        self.trail = [x + T / 2 for x in self.lead]
        last = (self.trail[-1] if n else self.first)
        self.cs_hi = last + (T if cs_hold is None else cs_hold)
        if cs_cuts is not None:
            self.cs_hi = self.lead[cs_cuts - 1] + T / 4 if cs_cuts else self.first
        # sampling edge: leading for CPHA 0, trailing for CPHA 1; launch edge is the other one
        self.samp = self.lead if not self.cpha else self.trail
        self.launch = self.trail if not self.cpha else self.lead
        self.rx_bits = []

    def sck(self, t):
        v = 0
        for a, b in zip(self.lead, self.trail):
            if a > t:
                break
            v = 1 if t < b else 0
        return v ^ self.cpol

    def cs(self, t):
        return 0 if self.cs_lo <= t < self.cs_hi else 1

    def mosi(self, t):
        # bit i valid from the launch edge before its sampling edge (CPHA 0: from CS fall / previous trailing edge)
        i = None
        for k in range(self.nbits):
            start = (self.cs_lo if k == 0 else self.launch[k - 1]) if not self.cpha else self.launch[k]
            if start <= t:
                i = k
        if i is None:
            return 0
        byte = self.tx[i // 8]
        return (byte >> (7 - i % 8)) & 1

    def sample(self, miso_changes, init=0):
        out = []
        for k, ts in enumerate(self.samp):
            if ts >= self.cs_hi:
                break
            out.append(level_at(miso_changes, ts - 1e-9, init))
        by = []
        for k in range(0, len(out) - 7, 8):
            by.append(sum(b << (7 - i) for i, b in enumerate(out[k:k + 8])))
        return by


class SpiTarget:
    # Answers a controller from the chip's SCK/CS pad changes; MISO changes `delay` clk after the launch edge
    # seen at its pin (pad round trip included in delay). Records MOSI at its sampling edges.
    def __init__(self, mode=0, tx=(), delay=0.0):
        self.cpol, self.cpha = mode >> 1, mode & 1
        self.tx = list(tx)
        self.delay = delay

    def miso_changes(self, sck_ch, cs_ch, nbytes):
        # returns MISO change list computed from chip edges
        out = []
        bits = [(b >> (7 - i)) & 1 for b in self.tx for i in range(8)]
        cs_fall = [t for t, v in cs_ch if v == 0]
        sck_edges = [(t, v ^ self.cpol) for t, v in sck_ch]   # logical: 1 = leading edge
        k = 0
        if not self.cpha and cs_fall and bits:
            out.append((cs_fall[0] + self.delay, bits[0]))
            k = 1
        for t, v in sck_edges:
            launch = (v == 0) if not self.cpha else (v == 1)
            if launch and k < len(bits):
                out.append((t + self.delay, bits[k]))
                k += 1
        return sorted(out)

    def received(self, sck_ch, mosi_ch, init_mosi=0):
        samp = [t for t, v in sck_ch if (v ^ self.cpol) == (1 if not self.cpha else 0)]
        bits = [level_at(mosi_ch, t - 1e-9, init_mosi) for t in samp]
        return [sum(b << (7 - i) for i, b in enumerate(bits[k:k + 8])) for k in range(0, len(bits) - 7, 8)]


def pad_levels(out, oe, pull=0xFF):
    # uio pad levels: driven bits from the chip, released bits at the partner / pull level
    return (out & oe) | (pull & ~oe & 0xFF)


class SpiTargetOnline:
    # SPI target answering the chip as controller from its pad history, incrementally. Any mode, wl bits per word,
    # MSB or LSB first. MISO changes `delay` clk after each launch edge (CS fall for the first bit in CPHA 0); the
    # shift register restarts on a word boundary at every CS rise. Records MOSI at its sampling edges.
    def __init__(self, mode, words, wl=8, lsb=False, delay=1.0, sck=3, cs=0, mosi=1):
        self.cpol, self.cpha, self.wl, self.lsb, self.delay = mode >> 1, mode & 1, wl, lsb, delay
        self.bits = [(w >> (i if lsb else wl - 1 - i)) & 1 for w in words for i in range(wl)]
        self.pins = (sck, cs, mosi)
        self.prev = (1 ^ self.cpol, 1, 1)            # released pads read high
        self.seen, self.k, self.wb, self.ns = 0, 0, 0, 0
        self.ch, self.cht, self.rx = [], [], []

    def skip(self, pad_trace):
        # answer from now on: the pad history so far is not part of a transfer
        if pad_trace:
            lv = pad_levels(pad_trace[-1][2], pad_trace[-1][3])
            self.prev = ((lv >> self.pins[0] & 1) ^ self.cpol, lv >> self.pins[1] & 1, lv >> self.pins[2] & 1)
        self.seen = len(pad_trace)

    def _launch(self, t):
        if self.k < len(self.bits):
            self.ch.append((t + self.delay, self.bits[self.k]))
            self.cht.append(t + self.delay)
        self.k += 1

    def update(self, pad_trace):
        sp, cp, mp = self.pins
        while self.seen < len(pad_trace):
            t, uo, out, oe = pad_trace[self.seen]
            self.seen += 1
            lv = pad_levels(out, oe)
            sck, cs, mosi = (lv >> sp & 1) ^ self.cpol, lv >> cp & 1, lv >> mp & 1
            psck, pcs, pmosi = self.prev
            self.prev = (sck, cs, mosi)
            if pcs and not cs:
                self.k = self.wb * self.wl
                if not self.cpha:
                    self._launch(t)
            elif cs and not pcs:
                self.wb += -(-self.ns // self.wl)
                self.ns = 0
            if cs or psck == sck:
                continue
            if sck == self.cpha:                     # launch edge: trailing (CPHA 0) or leading (CPHA 1)
                self._launch(t)
            else:
                self.rx.append(pmosi)
                self.ns += 1

    def level(self, t):
        k = bisect.bisect_right(self.cht, t) - 1
        return 0 if k < 0 else self.ch[k][1]

    def words(self):
        b, w = self.rx, self.wl
        return [sum(x << (i if self.lsb else w - 1 - i) for i, x in enumerate(b[k:k + w]))
                for k in range(0, len(b) - w + 1, w)]


def spi_ctrl_check(sck_ch, cs_ch, mosi_ch, mode, wl, bit, smp, t0=0.0, lsb=False):
    # Chip as SPI controller, pad level changes after t0 (CS idle high). Per CS-low window: SCK idle = CPOL at both CS
    # edges and between words; wl leading edges per word exactly `bit` apart; SCK high time inside a word bit - smp
    # (CPHA 0) or smp (CPHA 1); MOSI changes only at launch edges (smp before each sampling edge); MOSI sampled at
    # the sampling edges; no SCK edge while CS is high after the first CS fall. Returns [dict(lo, hi, setup, hold, mosi=[words], lead=[...])].
    cpol, cpha = mode >> 1, mode & 1
    lo = [t for t, v in cs_ch if v == 0 and t > t0]
    his = [t for t, v in cs_ch if v == 1 and lo and t > lo[0]]
    stray = [t for t, v in sck_ch if lo and t > lo[0] and not any(a <= t <= b for a, b in zip(lo, his))]
    assert not stray, "SCK edges while CS is high at %s" % stray[:4]
    out = []
    for a in lo:
        hi = [t for t, v in cs_ch if v == 1 and t > a]
        assert hi, "CS low at %g never rises" % a
        b = hi[0]
        assert level_at(sck_ch, a, cpol) == cpol and level_at(sck_ch, b, cpol) == cpol, "SCK not idle at CS edges"
        ed = [(t, v ^ cpol) for t, v in sck_ch if a <= t <= b]
        lead = [t for t, v in ed if v == 1]
        trail = [t for t, v in ed if v == 0]
        assert len(lead) == len(trail) and lead and len(lead) % wl == 0, "SCK edges %d/%d" % (len(lead), len(trail))
        hi_t = bit - smp if not cpha else smp
        for k in range(0, len(lead), wl):
            w = lead[k:k + wl]
            assert all(y - x == bit for x, y in zip(w, w[1:])), "SCK period in word: %s" % w
            assert all(trail[k + i] - w[i] == hi_t for i in range(wl)), "SCK high time"
        samp = lead if not cpha else trail
        launch = set(s - smp for s in samp)
        bad = [t for t, v in mosi_ch if a < t < b and t not in launch]
        assert not bad, "MOSI changes outside launch edges at %s" % bad[:4]
        bits = [level_at(mosi_ch, s - 1e-9, 0) for s in samp]
        words = [sum(x << (i if lsb else wl - 1 - i) for i, x in enumerate(bits[k:k + wl])) for k in range(0, len(bits), wl)]
        out.append(dict(lo=a, hi=b, setup=ed[0][0] - a, hold=b - ed[-1][0], mosi=words, lead=lead))
    return out


# ---------------- I2C ----------------
class I2cLine:
    # open-drain wired-AND with a pull-up rise delay (clk)
    def __init__(self, rise=0):
        self.rise = rise
        self.low_until = -1
        self.level = 1

    def update(self, t, pulled_low):
        if pulled_low:
            self.level = 0
            self.rel_t = None
        else:
            if self.level == 0 and getattr(self, "rel_t", None) is None:
                self.rel_t = t
            if self.level == 0 and t - self.rel_t >= self.rise:
                self.level = 1
                self.rel_t = None
        return self.level


class I2cMonitor:
    # bus checker: START/STOP decode, bytes with ACK, SDA changes while SCL high
    def __init__(self):
        self.prev = (1, 1)
        self.events = []
        self.bits = []
        self.illegal = []

    def step(self, t, scl, sda):
        pscl, psda = self.prev
        if pscl and scl and psda != sda:
            self.events.append(("S" if sda == 0 else "P", t))
            self.bits = []
        elif not pscl and scl:
            self.bits.append(sda)
            if len(self.bits) == 9:
                b = sum(x << (7 - i) for i, x in enumerate(self.bits[:8]))
                self.events.append(("B", b, self.bits[8], t))
                self.bits = []
        self.prev = (scl, sda)

    def summary(self):
        return [e[0] if e[0] in "SP" else (e[1], e[2]) for e in self.events]


class I2cControllerModel:
    # clocked bit-banger, honours clock stretching. script: list of ("S",), ("P",), ("W", byte), ("R", ack_bit)
    def __init__(self, script, thigh, tlow, tsu=None):
        self.script = list(script)
        self.thigh, self.tlow = thigh, tlow
        self.tsu = tlow // 2 if tsu is None else tsu
        self.scl_rel, self.sda_rel = 1, 1
        self.state, self.t_next = "idle", 0
        self.read = []
        self.acks = []
        self.busy_until = 0
        self.ops = []

    def done(self):
        return not self.script and not self.ops and self.state == "idle"

    def expand(self):
        it = self.script.pop(0)
        k = it[0]
        if k == "S":
            # SDA high, SCL high, then SDA low, wait, SCL low
            return [("sda", 1), ("scl", 1), ("waithigh",), ("wait", self.thigh), ("sda", 0), ("wait", self.thigh),
                    ("scl", 0), ("wait", self.tsu)]
        if k == "P":
            return [("sda", 0), ("wait", self.tlow - self.tsu), ("scl", 1), ("waithigh",), ("wait", self.thigh),
                    ("sda", 1), ("wait", self.tlow)]
        ops = []
        if k == "W":
            bits = [(it[1] >> (7 - i)) & 1 for i in range(8)] + [1]
        else:
            bits = [1] * 8 + [it[1]]
        for i, b in enumerate(bits):
            ops += [("sda", b), ("wait", self.tlow - self.tsu), ("scl", 1), ("waithigh",), ("wait", self.thigh // 2),
                    ("sample", k, i), ("wait", self.thigh - self.thigh // 2), ("scl", 0), ("wait", self.tsu)]
        return ops

    def step(self, t, scl, sda):
        if t < self.busy_until:
            return
        while True:
            if not self.ops:
                if not self.script:
                    self.state = "idle"
                    return
                self.ops = self.expand()
                self.cur = []
                self.state = "run"
            op = self.ops[0]
            if op[0] == "sda":
                self.sda_rel = op[1]
            elif op[0] == "scl":
                self.scl_rel = op[1]
            elif op[0] == "waithigh":
                if not scl:
                    return
            elif op[0] == "wait":
                self.ops.pop(0)
                self.busy_until = t + op[1]
                return
            elif op[0] == "sample":
                self.cur.append(sda)
                if op[2] == 8:
                    if op[1] == "W":
                        self.acks.append(sda)
                    else:
                        self.read.append(sum(x << (7 - i) for i, x in enumerate(self.cur[:8])))
                    self.cur = []
            self.ops.pop(0)


class I2cTargetModel:
    # clocked target: 7-bit address, ACK, receive, transmit until NACK; stretch = {(state, bit): clk} on SCL falls
    def __init__(self, addr, tx=(), stretch=None, nack_after=None):
        self.addr, self.tx = addr, list(tx)
        self.stretch = dict(stretch or {})
        self.nack_after = nack_after
        self.sda_rel = self.scl_rel = 1
        self.prev = (1, 1)
        self.state, self.phase, self.n, self.sh = "idle", None, 0, 0
        self.rx, self.mack = [], 1
        self.hold_until = None
        self.nbytes = 0

    def step(self, t, scl, sda):
        pscl, psda = self.prev
        self.prev = (scl, sda)
        if self.hold_until is not None and t >= self.hold_until:
            self.scl_rel, self.hold_until = 1, None
        if pscl and scl and psda != sda:
            self.sda_rel = 1
            self.state, self.phase, self.n, self.sh = ("addr", "data", 0, 0) if sda == 0 else ("idle", None, 0, 0)
            return
        if self.state == "idle":
            return
        if not pscl and scl:
            if self.phase == "data" and self.state in ("addr", "wr"):
                self.sh, self.n = (self.sh << 1) | sda, self.n + 1
            elif self.phase == "mack":
                self.mack = sda
        elif pscl and not scl:
            key = (self.state, self.n)
            if key + (self.phase,) in self.stretch:     # (state, n, phase) tells the 8th fall from the 9th
                key = key + (self.phase,)
            if key in self.stretch:
                self.scl_rel, self.hold_until = 0, t + self.stretch.pop(key)
            if self.state in ("addr", "wr"):
                if self.phase == "data" and self.n == 8:
                    if self.state == "addr":
                        self.ok, self.rw = (self.sh >> 1) == self.addr, self.sh & 1
                    else:
                        self.rx.append(self.sh)
                        self.nbytes += 1
                        self.ok = self.nack_after is None or self.nbytes < self.nack_after
                    self.sda_rel = 0 if self.ok else 1
                    self.phase = "ack"
                elif self.phase == "ack":
                    self.sda_rel = 1
                    if not self.ok:
                        self.state = "idle"
                    elif self.state == "addr" and self.rw:
                        self.state = "rd"
                        self.load()
                    else:
                        self.state, self.phase, self.n, self.sh = "wr", "data", 0, 0
            elif self.state == "rd":
                if self.phase == "data" and self.n < 8:
                    self.sda_rel = (self.cur >> (7 - self.n)) & 1
                    self.n += 1
                elif self.phase == "data":
                    self.sda_rel, self.phase = 1, "mack"
                else:
                    if self.mack == 0:
                        self.load()
                    else:
                        self.state, self.sda_rel = "idle", 1

    def load(self):
        self.cur = self.tx.pop(0) if self.tx else 0xFF
        self.phase, self.n = "data", 1
        self.sda_rel = (self.cur >> 7) & 1


# UM10204 table 10 (ns): minimums, except tVDDAT (data valid after SCL low) which is a maximum
I2C_LIM = {
    "Sm": dict(tLOW=4700, tHIGH=4000, tHDSTA=4000, tSUSTA=4700, tSUSTO=4000, tBUF=4700, tSUDAT=250, tVDDAT=3450),
    "Fm": dict(tLOW=1300, tHIGH=600, tHDSTA=600, tSUSTA=600, tSUSTO=600, tBUF=1300, tSUDAT=100, tVDDAT=900),
    "Fm+": dict(tLOW=500, tHIGH=260, tHDSTA=260, tSUSTA=260, tSUSTO=260, tBUF=500, tSUDAT=50, tVDDAT=450),
}


def i2c_timing(hist, low_max=None, rise=0):
    # bus-level UM10204 intervals (clk) from changes [(t, scl, sda)], first entry = state at the start. A line reads
    # high at the end of its rise (70 %); tLOW and tSU;DAT end at SCL's 30 % point, taken as 0.7 * rise earlier.
    # SDA and SCL changing in one clock (setup or hold 0) is listed in "same". tSU;DAT and tVD;DAT count
    # only for data / ACK bits: a clock whose high phase holds a START or STOP is not one. low_max: tVD;DAT counts only
    # for low phases <= low_max clk (UM10204 table 10 note: when SCL low is stretched, only the setup applies)
    q = {k: [] for k in I2C_LIM["Sm"]}
    q["same"] = []
    early = 0.7 * rise
    _, scl, sda = hist[0]
    busy, rise, fall, dch, stop, hd = False, None, None, None, None, None
    vd = su = None
    for t, s, d in hist[1:]:
        if s != scl and d != sda:
            q["same"].append(t)
        ev = [("d", d), ("c", s)] if s else [("c", s), ("d", d)]
        for k, v in ev:
            if k == "c" and v != scl:
                scl = v
                if v:
                    if busy and fall is not None:
                        q["tLOW"].append(t - fall - early)
                        su = t - dch - early
                        if low_max is not None and t - fall > low_max:
                            vd = None
                    rise = t
                else:
                    if hd is not None:
                        q["tHDSTA"].append(t - hd)
                        hd = None
                    elif busy and rise is not None:
                        q["tHIGH"].append(t - rise)
                    if su is not None:
                        q["tSUDAT"].append(su)
                    if vd is not None:
                        q["tVDDAT"].append(vd)
                    fall, vd, su = t, None, None
            elif k == "d" and v != sda:
                sda = v
                if not scl:
                    if busy and fall is not None:
                        vd = t - fall
                elif not v:
                    if busy:
                        q["tSUSTA"].append(t - rise)
                    elif stop is not None:
                        q["tBUF"].append(t - stop)
                    busy, hd = True, t
                else:
                    if busy:
                        q["tSUSTO"].append(t - rise)
                    busy, stop, fall = False, t, None
                if scl:
                    vd = su = None
                dch = t
    return q


def i2c_violations(q, mode, ns=20.0):
    lim, bad = I2C_LIM[mode], []
    if q.get("same"):
        bad.append(("SDA and SCL change in one clock", q["same"][:4], 0))
    for k, v in q.items():
        if not v or k not in lim:
            continue
        x = (max(v) if k == "tVDDAT" else min(v)) * ns
        if (x > lim[k]) if k == "tVDDAT" else (x < lim[k]):
            bad.append((k, x, lim[k]))
    return bad


# ---------------- WS2812 ----------------
def ws2812_decode(changes, t_thresh):
    # high pulse shorter than t_thresh = 0, longer = 1; returns (bytes, pulses[(start, high, low)])
    pulses = []
    for k, (t, v) in enumerate(changes):
        if v == 1 and k + 1 < len(changes):
            hi = changes[k + 1][0] - t
            lo = (changes[k + 2][0] - changes[k + 1][0]) if k + 2 < len(changes) else None
            pulses.append((t, hi, lo))
    bits = [1 if hi > t_thresh else 0 for _, hi, _ in pulses]
    return [sum(b << (7 - i) for i, b in enumerate(bits[k:k + 8])) for k in range(0, len(bits) - 7, 8)], pulses


# ---------------- Manchester (IEEE 802.3: 1 = low then high) ----------------
class ManchesterSender:
    def __init__(self, bit, data, t0=20.0, jitter=None, pre_bits=()):
        # pre_bits: single bits sent before the bytes
        self.edges = []
        t = t0
        self.bits = list(pre_bits) + [(b >> (7 - i)) & 1 for b in data for i in range(8)]
        for k, b in enumerate(self.bits):
            j = jitter(k) if jitter else 0.0
            self.edges.append((t + j, 0 if b else 1))
            self.edges.append((t + bit / 2 + j, 1 if b else 0))
            t += bit
        self.edges.append((t, 0))
        self.end = t

    def level(self, t):
        return level_at(self.edges, t, 0)


def manchester_decode(changes, bit, t_start, n):
    # IEEE 802.3: the second half of each bit carries its value; grid starts at t_start
    return [level_at(changes, t_start + k * bit + 0.75 * bit, 0) for k in range(n)]


# ---------------- CAN 2.0A (independent of can.s: bit-level node, hard sync, sample point) ----------------
def can_crc15(bits):
    crc = 0
    for b in bits:
        nxt = b ^ (crc >> 14 & 1)
        crc = (crc << 1) & 0x7FFF
        if nxt:
            crc ^= 0x4599
    return crc


def can_frame(ident, data, rtr=False):
    # unstuffed SOF..CRC
    dlc = len(data)
    b = [0] + [(ident >> (10 - i)) & 1 for i in range(11)] + [int(rtr), 0, 0] + [(dlc >> (3 - i)) & 1 for i in range(4)]
    if not rtr:
        b += [(x >> (7 - i)) & 1 for x in data for i in range(8)]
    crc = can_crc15(b)
    return b + [(crc >> (14 - i)) & 1 for i in range(15)]


def can_stuff(bits):
    out, last, run = [], None, 0
    for x in bits:
        out.append(x)
        run = run + 1 if x == last else 1
        last = x
        if run == 5:
            out.append(1 - x)
            last, run = 1 - x, 1
    return out


def can_host_tx(ident, data, rtr=False):
    # can.s TX stream: [NB] [B0 = SOF ID10 ID9 in bits 7:5] [ID8..0 RTR IDE r0 DLC data, MSB first]
    b = can_frame(ident, data, rtr)[:-15]
    rest = b[3:]
    out = [sum(x << (7 - i) for i, x in enumerate(rest[k:k + 8])) for k in range(0, len(rest), 8)]
    return [len(out), b[0] << 7 | b[1] << 6 | b[2] << 5] + out


class CanNode:
    # one node on a wired-AND bus, independent of can.s: receiver hard-syncs on every falling edge and samples at
    # sp, destuffs, checks the CRC, ACKs frames it does not send, sends active error flags (stuff, form, bit, ACK
    # at the next bit; CRC after the ACK delimiter). Transmitter: own grid from its SOF (float bit time for clock
    # offsets), loses arbitration on recessive-sent/dominant-read inside SOF + ID + RTR, no retransmission.
    # drive(t) and observe(t, bus) once per clock.
    def __init__(self, bit, sp=None, ack=True, flags=True):
        self.bit, self.ack, self.flags = float(bit), ack, flags
        self.sp = sp if sp is not None else int(0.75 * bit)
        self.txbits, self.t0, self.pending = None, None, None
        self.ack_from, self.err_from, self.force = None, None, []
        self.prev, self.phase, self.st, self.idle = 1, 0.0, "idle", 0
        self.frames, self.errors, self.acked, self.lost = [], [], None, False

    def send(self, t0, ident, data, rtr=False, bits=None):
        body = bits if bits is not None else can_stuff(can_frame(ident, data, rtr))
        self.txbits = body + [1] * 10                       # CRC delimiter, ACK, ACK delimiter, EOF
        self.t0, self.ack_t, self.acked = t0, None, None
        self.nbody = len(body)

    def send_on_sof(self, delta, ident, data, rtr=False):
        self.pending = (delta, ident, data, rtr)            # start delta clk after the next SOF edge on the bus

    def txk(self, t):
        if self.txbits is None or t < self.t0:
            return None
        k = int((t - self.t0) // self.bit)
        return k if k < len(self.txbits) else None

    def drive(self, t):
        if self.err_from is not None and self.err_from <= t < self.err_from + 6 * self.bit:
            return 0
        if self.ack_from is not None and self.ack_from <= t < self.ack_from + self.bit:
            return 0
        if any(a <= t < b for a, b in self.force):
            return 0
        k = self.txk(t)
        return self.txbits[k] if k is not None else 1

    def observe(self, t, b):
        if self.prev == 1 and b == 0:
            self.phase = 0.0
            if self.pending is not None and self.st == "idle":
                d, i, dd, r = self.pending
                self.pending = None
                self.send(t + d, i, dd, r)
        self.prev = b
        if int(self.phase) == self.sp:
            self.sample(t, b)
        self.phase += 1.0
        if self.phase >= self.bit:
            self.phase -= self.bit

    def next_bit(self, t):
        return t - self.phase + self.bit

    def error(self, t, why, later=0):
        self.errors.append(why)
        k = self.txk(t)
        if k is not None:
            self.txbits = self.txbits[:k + 1]               # a transmitter stops after the current bit
        if self.flags:
            self.err_from = self.next_bit(t) + later * self.bit
        self.st, self.idle = "idle", -6 - later             # flag bits are not idle
        return None

    def sample(self, t, b):
        k = self.txk(t)
        if k is not None and k == self.nbody + 1:           # own ACK slot
            self.acked = b == 0
            if not self.acked:
                return self.error(t, "ack")
        if self.st == "idle":
            if b:
                self.idle += 1
            elif self.idle >= 11:
                self.st, self.bits, self.last, self.run, self.n = "frame", [0], 0, 1, None
            else:
                self.idle = 0                               # 11 recessive after the last dominant (ISO)
            return
        if self.st == "frame":
            stuff = self.run == 5
            if k is not None and k < self.nbody and self.txbits[k] != b:
                if self.txbits[k] == 1 and not stuff and len(self.bits) <= 12:
                    self.txbits, self.lost = None, True     # arbitration lost: receive the winner
                else:
                    return self.error(t, "bit")
            if stuff:
                if b == self.last:
                    return self.error(t, "stuff")
                self.last, self.run = b, 1
                if self.n is not None and len(self.bits) == self.n:
                    self.st = "delim"
                return
            self.bits.append(b)
            self.run = self.run + 1 if b == self.last else 1
            self.last = b
            if len(self.bits) == 19:
                dlc = int("".join(map(str, self.bits[15:19])), 2)
                self.n = 19 + (0 if self.bits[12] else 8 * min(dlc, 8)) + 15
            if self.n is not None and len(self.bits) == self.n and self.run < 5:
                self.st = "delim"
            return
        if self.st == "delim":
            body, crc = self.bits[:-15], self.bits[-15:]
            if not b:
                return self.error(t, "form")
            if can_crc15(body) != int("".join(map(str, crc)), 2) and self.txk(t) is None:   # receivers only
                return self.error(t, "crc", later=2)
            ident = int("".join(map(str, body[1:12])), 2)
            data = [int("".join(map(str, body[19 + 8 * j:27 + 8 * j])), 2) for j in range((len(body) - 19) // 8)]
            self.frames.append((ident, data, body[12]))
            if self.ack and self.txk(t) is None:
                self.ack_from = self.next_bit(t)
            self.st, self.idle = "idle", 0


# ---------------- USB low speed (USB 2.0 ch. 7-8) ----------------
# Line states (D+, D-): LS idle J = (0, 1), K = (1, 0), SE0 = (0, 0). Bits LSB first, NRZI: 0 = transition.
USB_J, USB_K, USB_SE0 = (0, 1), (1, 0), (0, 0)
USB_PID = {"OUT": 0xE1, "IN": 0x69, "SETUP": 0x2D, "DATA0": 0xC3, "DATA1": 0x4B,
           "ACK": 0xD2, "NAK": 0x5A, "STALL": 0x1E}


def usb_crc5(v11):
    # v11 = addr | ep << 7, fed LSB first; x^5 + x^2 + 1, init 11111, complemented
    crc = 0x1F
    for i in range(11):
        crc = (crc >> 1) ^ 0x14 if (crc ^ (v11 >> i)) & 1 else crc >> 1
    return crc ^ 0x1F


def usb_crc16_reg(data, crc=0xFFFF):
    # reflected x^16 + x^15 + x^2 + 1 register after the bytes (no final complement)
    for byte in data:
        for i in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc ^ (byte >> i)) & 1 else crc >> 1
    return crc


def usb_crc16(data):
    return usb_crc16_reg(data) ^ 0xFFFF


def usb_token(pid, addr, ep):
    v = (addr & 0x7F) | (ep & 0xF) << 7
    w = v | usb_crc5(v) << 11
    return [USB_PID[pid], w & 0xFF, w >> 8]


def usb_data(pid, payload, bad_crc=False):
    c = usb_crc16(payload) ^ (0x0001 if bad_crc else 0)
    return [USB_PID[pid]] + list(payload) + [c & 0xFF, c >> 8]


def usb_key(addr, ep):
    # the RP2350's token key: CRC-16 register after token bytes b1, b2 from 0xFFFF
    return usb_crc16_reg(usb_token("IN", addr, ep)[1:])


def usb_stuffed_bits(data):
    raw = [0] * 7 + [1] + [b >> i & 1 for b in data for i in range(8)]
    out, run = [], 0
    for b in raw:
        out.append(b)
        run = run + 1 if b else 0
        if run == 6:
            out.append(0)
            run = 0
    return out


class UsbHostLine:
    # what the host drives: segments (t_start, t_end, state); None = not driving
    def __init__(self, bit=32.0):
        self.bit, self.segs, self.t = bit, [], 0.0
        self.eop_ends = []

    def _seg(self, n_bits, state):
        t1 = self.t + n_bits * self.bit
        self.segs.append((self.t, t1, state))
        self.t = t1

    def packet(self, data, t=None, eop=True):
        if t is not None:
            self.t = max(self.t, t)
        st = USB_J
        for b in usb_stuffed_bits(data):
            if b == 0:
                st = USB_K if st == USB_J else USB_J
            self._seg(1, st)
        if eop:
            self.eop(drive_j=True)
        return self.t

    def eop(self, t=None, drive_j=True):
        if t is not None:
            self.t = max(self.t, t)
        self._seg(2, USB_SE0)
        self.eop_ends.append(self.t)
        if drive_j:
            self._seg(1, USB_J)
        return self.t

    def keepalive(self, t=None):
        return self.eop(t)

    def idle(self, n_bits):
        self.t += n_bits * self.bit
        return self.t

    def state(self, t):
        k = bisect.bisect_right([s[0] for s in self.segs], t) - 1
        if k >= 0 and self.segs[k][0] <= t < self.segs[k][1]:
            return self.segs[k][2]
        return None


def usb_decode(log, bit, t_from=0):
    # log: list of (D+, D-) per clk. Packets: J->K starts SYNC; sample at mid-bit; stop at SE0.
    # -> [(t_first_K, t_eop_end, bytes, ok)]; ok = SYNC, stuffing and whole bytes correct
    out, t, n = [], t_from, len(log)
    while t < n - 1:
        if not (log[t] == USB_J and log[t + 1] == USB_K):
            t += 1
            continue
        t0 = t + 1
        prev, bits, k, ok = USB_J, [], 0, True
        while True:
            ts = int(t0 + (k + 0.5) * bit)
            if ts >= n:
                return out
            s = log[ts]
            if s == USB_SE0:
                break
            bits.append(1 if s == prev else 0)
            prev, k = s, k + 1
        te = ts
        while te < n and log[te] == USB_SE0:
            te += 1
        data, run = [], 0
        for b in bits:
            if run == 6:
                ok &= b == 0
                run = 0
                continue
            data.append(b)
            run = run + 1 if b else 0
        ok &= run != 6                                     # six 1s before the EOP need their stuff bit too
        ok &= data[:8] == [0] * 7 + [1] and (len(data) - 8) % 8 == 0
        by = [sum(data[8 + 8 * j + i] << i for i in range(8)) for j in range((len(data) - 8) // 8)]
        out.append((t0, te, by, ok))
        t = te
    return out


def usb_crc_preset(pid):
    # LFSR start value that becomes 0xFFFF after the transmitter shifts SYNC | PID << 8 (16 bits, LSB first)
    word = 0x80 | pid << 8
    s = 0xFFFF
    for i in reversed(range(16)):
        fb = s >> 15 & 1
        t = s ^ (0xA001 if fb else 0)
        s = (t << 1 | (fb ^ (word >> i & 1))) & 0xFFFF
    return s


class UsbEdges:
    # receiver view of slow D+/D- edges: a rising edge crosses the threshold U[rise] clk late, a falling edge U[fall]
    # (per edge). Rises later than falls: every J<->K transition shows crossover SE0 of rise - fall; the reverse: SE1
    def __init__(self, rng, rise, fall):
        self.rng, self.rise, self.fall = rng, rise, fall
        self.w = self.s = None
        self.q = [[], []]
        self.se0, self.xmax = 0, 0                 # current SE0 run, longest SE0 run shorter than 1 bit

    def __call__(self, t, st):
        if self.w is None:
            self.w, self.s = list(st), list(st)
        ch = [i for i in (0, 1) if st[i] != self.w[i]]
        if ch:
            for i in ch:
                tt = t + self.rng.randint(*(self.rise if st[i] else self.fall))
                if self.q[i]:
                    tt = max(tt, self.q[i][-1][0] + 1)
                self.q[i].append((tt, st[i]))
                self.w[i] = st[i]
        for i in (0, 1):
            while self.q[i] and self.q[i][0][0] <= t:
                self.s[i] = self.q[i].pop(0)[1]
        v = (self.s[0], self.s[1])
        if v == USB_SE0:
            self.se0 += 1
        else:
            if 0 < self.se0 < 32:
                self.xmax = max(self.xmax, self.se0)
            self.se0 = 0
        return v
