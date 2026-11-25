# Decoders for the monitor programs (docs/spec/programs/monitor_design.md): engine streams -> traffic and a readable
# log. MicroPython and CPython. A stream is a list of entries in arrival order: an int (data byte) or ("ev", code).
EV = "ev"
LOST, WRAP, CUT, TS_OLD, TS_NEXT = 63, 62, 61, 60, 58


def entries(link, e):
    # host/jspe.py Link: merge engine e's data bytes with its events (an event follows that many bytes)
    data = link.rx.get(e, b"")
    out, k = [], 0
    for ee, code, idx in link.events:
        if ee != e:
            continue
        out += list(data[k:idx])
        out.append((EV, code))
        k = idx
    return out + list(data[k:])


def iss_entries(chip, e):
    # sw/jspe_iss.py Chip direct mode (tests): bit 8 marks an event
    return [(EV, v & 63) if v & 0x100 else v for _, v in chip.host_out[e]]


def is_ev(x, code=None):
    return isinstance(x, tuple) and (code is None or x[1] == code)


class Clock:
    # wrap-counted time of the TS variants: time = W * (tsp + 1) + tsp - T (clk at prescale 1)
    def __init__(self, tsp=0xFFFF):
        self.w, self.tsp, self.broken = 0, tsp, False

    def at(self, t, minus=0):
        return (self.w - minus) * (self.tsp + 1) + self.tsp - t


def spi(mosi, miso=None, ts=False, tsw=41):
    # spi_mon.s: frames {"start", "end", "mosi", "miso", "partial", "lost"}; times only with ts (clk, TSPRE 0;
    # tsw = the program's TSW). The n-th MOSI byte pairs with the n-th MISO byte.
    a = _spi_one(mosi, ts, tsw)
    b = _spi_one(miso, ts, tsw) if miso is not None else []
    for k, f in enumerate(a):
        g = b[k] if k < len(b) else None
        f["miso"] = g["data"] if g else None
        f["mosi"] = f.pop("data")
        if g:
            f["partial"] = f["partial"] or g["partial"]
            f["lost"] = f["lost"] or g["lost"]
    return a


def _spi_one(ent, ts, tsw):
    clk, frames, cur, pend, cut = Clock(), [], None, None, False
    for x in ent:
        if is_ev(x, WRAP):
            clk.w += 1
        elif is_ev(x, CUT):
            cut = True
        elif is_ev(x):
            pend = None
            c = x[1]
            if c == 1:
                cur = {"start": None, "end": None, "data": [], "partial": False, "lost": False}
                pend = [1] if ts else None
            elif cur is None:
                continue                                  # monitor started inside a frame
            elif c == 3:
                cur["partial"] = True
            elif c == LOST:
                cur["lost"] = True
                clk.broken = True
            elif c == 2:
                frames.append(cur)
                pend = [2] if ts else None
        elif pend is not None:
            pend.append(x)
            if len(pend) == 3:
                t = pend[1] | pend[2] << 8
                v = clk.at(t, t < tsw)
                if pend[0] == 2:
                    f = frames[-1]
                    if cut and f["start"] is not None and v < f["start"]:
                        clk.w += 1                        # the CS rise cut a wrap call (evt 61)
                        v += clk.tsp + 1
                    f["end"], cut = v, False
                else:
                    cur["start"] = v
                pend = None
        elif cur is not None:
            cur["data"].append(x)
    return frames


def i2c(ent):
    # i2c_mon.s: tokens ("S",), ("Sr",), ("P",), ("A", addr7, rw, ack), ("D", byte, ack), ("STRETCH", steps of 16
    # clk), ("LOST",). ack = 1 for ACK, 0 for NACK; the first byte after S/Sr is the address.
    out, first, k, open_ = [], False, 0, False
    while k < len(ent):
        x = ent[k]
        if is_ev(x):
            c = x[1]
            if c in (1, 2):
                out.append(("S",) if c == 1 else ("Sr",))
                first = open_ = True
            elif c == 3 and open_:                        # a STOP before any START: monitor started mid-transfer
                out.append(("P",))
                open_ = False
            elif c == 4 and out and out[-1][0] in "AD":
                out[-1] = out[-1][:-1] + (0,)
            elif c == 5:
                b = [y for y in ent[k + 1:k + 3] if not is_ev(y)]
                if len(b) == 2:
                    out.append(("STRETCH", b[0] | b[1] << 8))
                    k += 2
            elif c == LOST:
                out.append(("LOST",))
        else:
            out.append(("A", x >> 1, x & 1, 1) if first else ("D", x, 1))
            first = False
        k += 1
    return out


def uart(ent, ts=0):
    # uart_mon.s (and uart_rx.s for ts=0): [(time or None, byte, flags)], flags = set of event codes: 1 framing
    # error, 63 data lost before or at this character (after it the time base may be short of wrap events)
    clk, out, pend, minus, nts = Clock(), [], ([] if ts == 1 else None), 0, None
    for x in ent:
        if is_ev(x, WRAP):
            clk.w += 1
        elif is_ev(x, TS_OLD) or is_ev(x, TS_NEXT):
            minus = 1 if x[1] == TS_OLD else 0
            if ts == 2:
                pend = []
        elif is_ev(x):
            if x[1] == LOST:
                clk.broken = True
                if ts:
                    pend = [] if ts == 1 else None    # resync: drop an unfinished timestamp / record
                    if ts == 2:
                        nts = None
            if out and (ts == 0 or ts == 2 or not pend):
                out[-1][2].add(x[1])
        elif ts == 1:
            pend.append(x)
            if len(pend) == 3:
                out.append((clk.at(pend[0] | pend[1] << 8, minus), pend[2], set()))
                pend, minus = [], 0
        elif ts == 2 and pend is not None:
            pend.append(x)
            if len(pend) == 2:
                nts, pend = clk.at(pend[0] | pend[1] << 8, minus), None
                minus = 0
        else:
            out.append((nts, x, set()))
            nts = None
    return out


def log_spi(frames, hz=50000000):
    lines = []
    for f in frames:
        t = "" if f["start"] is None else "%10.3f us " % (f["start"] * 1e6 / hz)
        s = t + "CS " + " ".join("%02x/%02x" % (m, f["miso"][i]) if f["miso"] and i < len(f["miso"]) else "%02x" % m
                                  for i, m in enumerate(f["mosi"]))
        lines.append(s + (" +bits" if f["partial"] else "") + (" LOST" if f["lost"] else ""))
    return lines


def log_i2c(tokens):
    s, lines = [], []
    for tk in tokens:
        k = tk[0]
        if k in ("S", "Sr"):
            if s and k == "S":
                lines.append(" ".join(s))
                s = []
            s.append("[" if k == "S" else "[Sr")
        elif k == "A":
            s.append("%02x%s%s" % (tk[1], "R" if tk[2] else "W", "" if tk[3] else "-NACK"))
        elif k == "D":
            s.append("%02x%s" % (tk[1], "" if tk[2] else "-NACK"))
        elif k == "STRETCH":
            s.append("(stretch %d ns)" % (tk[1] * 320))
        elif k == "LOST":
            s.append("LOST")
        else:
            s.append("]")
            lines.append(" ".join(s))
            s = []
    return lines + ([" ".join(s)] if s else [])


def log_uart(a, b, hz=50000000):
    # two directions merged by time when both carry timestamps, else listed per line
    if all(x[0] is not None for x in a + b) and a + b:
        m = sorted([(x[0], "A", x) for x in a] + [(x[0], "B", x) for x in b], key=lambda r: r[0])
        return ["%10.3f us %s %02x%s" % (t * 1e6 / hz, n, x[1], _fl(x[2])) for t, n, x in m]
    return ["A %02x%s" % (x[1], _fl(x[2])) for x in a] + ["B %02x%s" % (x[1], _fl(x[2])) for x in b]


def _fl(f):
    return (" FRAMING" if 1 in f else "") + (" LOST" if LOST in f else "")
