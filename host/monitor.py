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
    # wrap-counted time of the TS variants: time = W * (tsp + 1) + tsp - T (clk at prescale 1). After a LOST the wrap
    # count may be short (an evt 62 can be among the lost entries): times then belong to a new epoch, comparable only
    # with times of the same epoch.
    def __init__(self, tsp=0xFFFF):
        self.w, self.tsp, self.epoch = 0, tsp, 0

    def at(self, t, minus=0):
        return (self.w - minus) * (self.tsp + 1) + self.tsp - t


def spi(mosi, miso=None, ts=False, tsw=41, tsp=0xFFFF):
    # spi_mon.s: frames {"start", "end", "mosi", "miso", "partial", "lost", "epoch"}; times only with ts (clk,
    # TSPRE 0; tsw = the program's TSW). The n-th MOSI byte pairs with the n-th MISO byte; a frame whose evt 1 or
    # evt 2 was lost is kept with lost = True (start or end None), so the pairing stays aligned.
    a = _spi_one(mosi, ts, tsw, tsp)
    b = _spi_one(miso, ts, tsw, tsp) if miso is not None else []
    for k, f in enumerate(a):
        g = b[k] if k < len(b) else None
        f["miso"] = g["data"] if g else None
        f["mosi"] = f.pop("data")
        if g:
            f["partial"] = f["partial"] or g["partial"]
            f["lost"] = f["lost"] or g["lost"]
    return a


def _frame(clk, lost=False):
    return {"start": None, "end": None, "data": [], "partial": False, "lost": lost, "epoch": clk.epoch}


def _spi_one(ent, ts, tsw, tsp=0xFFFF):
    clk, frames, cur, pend, cut, seen, began = Clock(tsp), [], None, None, False, True, False
    for x in ent:
        if is_ev(x, WRAP):
            clk.w, seen = clk.w + 1, True
        elif is_ev(x, CUT):
            cut = True
        elif is_ev(x):
            pend, c = None, x[1]
            if c == 1:
                if cur is not None:                       # its evt 2 was lost
                    cur["lost"] = True
                    frames.append(cur)
                cur, began = _frame(clk), True
                pend = [1] if ts else None
                continue
            if cur is None:
                if not began:
                    continue                              # monitor started inside a frame
                cur = _frame(clk, True)                   # its evt 1 was lost
            if c == 3:
                cur["partial"] = True
            elif c == LOST:
                cur["lost"] = True
                clk.epoch += 1
            elif c == 2:
                frames.append(cur)
                cur = None
                pend = [2] if ts else None
        elif pend is not None:
            pend.append(x)
            if len(pend) == 3:
                t = pend[1] | pend[2] << 8
                v = clk.at(t, t < tsw)
                if pend[0] == 2:
                    f = frames[-1]
                    if cut and (not seen or (f["start"] is not None and v < f["start"])):
                        clk.w += 1                        # the CS rise cut a wrap call (evt 61)
                        v += clk.tsp + 1
                    f["end"] = v
                else:
                    cur["start"], seen = v, False
                pend, cut = None, False
        else:
            if cur is None:
                if not began:
                    continue
                cur = _frame(clk, True)
            cur["data"].append(x)
            seen = False
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
                n = 0
                while n < 2 and k + 1 + n < len(ent) and not is_ev(ent[k + 1 + n]):
                    n += 1
                if n == 2:
                    out.append(("STRETCH", ent[k + 1] | ent[k + 2] << 8))
                k += n                                    # a record cut by START/STOP is dropped
            elif c == LOST:
                out.append(("LOST",))
        else:
            out.append(("A", x >> 1, x & 1, 1) if first else ("D", x, 1))
            first = False
        k += 1
    return out


def uart(ent, ts=0):
    # uart_mon.s (and uart_rx.s for ts=0): [(time or None, byte, flags, epoch)], flags = set of event codes: 1 framing
    # error, 63 data lost before or at this character (ts 0: evt 63 follows the byte; ts 1, 2: it leads the next
    # record). ts 2: the up to two bytes before an evt 63 may be fragments of a dropped timestamp: flagged 63 too.
    clk, out, pend, minus, nts, lost = Clock(), [], ([] if ts == 1 else None), 0, None, False
    for x in ent:
        if is_ev(x, WRAP):
            clk.w += 1
        elif is_ev(x, TS_OLD) or is_ev(x, TS_NEXT):
            minus = 1 if x[1] == TS_OLD else 0
            if ts == 2:
                pend = []
        elif is_ev(x):
            if x[1] == LOST:
                clk.epoch += 1
                if ts:
                    lost = True
                    if ts == 1:
                        pend = []                         # resync: drop an unfinished record
                    else:
                        pend, nts = None, None
                        for r in out[-2:]:
                            r[2].add(LOST)
                    continue
            if out and (ts != 1 or not pend):
                out[-1][2].add(x[1])
        elif ts == 1:
            pend.append(x)
            if len(pend) == 3:
                out.append((clk.at(pend[0] | pend[1] << 8, minus), pend[2], {LOST} if lost else set(), clk.epoch))
                pend, minus, lost = [], 0, False
        elif ts == 2 and pend is not None:
            pend.append(x)
            if len(pend) == 2:
                nts, pend = clk.at(pend[0] | pend[1] << 8, minus), None
                minus = 0
        else:
            out.append((nts, x, {LOST} if lost and ts else set(), clk.epoch))
            nts, lost = None, False
    return out


def log_spi(frames, hz=50000000):
    lines = []
    for f in frames:
        t = "" if f["start"] is None else "%10.3f us (epoch %d) " % (f["start"] * 1e6 / hz, f["epoch"])
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
    # two directions merged by time when both carry timestamps of one epoch, else listed per line
    ab = a + b
    if ab and all(x[0] is not None for x in ab) and len(set(x[3] for x in ab)) == 1:
        m = sorted([(x[0], "A", x) for x in a] + [(x[0], "B", x) for x in b], key=lambda r: r[0])
        return ["%10.3f us %s %02x%s" % (t * 1e6 / hz, n, x[1], _fl(x[2])) for t, n, x in m]
    return ["A %02x%s" % (x[1], _fl(x[2])) for x in a] + ["B %02x%s" % (x[1], _fl(x[2])) for x in b]


def _fl(f):
    return (" FRAMING" if 1 in f else "") + (" LOST" if LOST in f else "")
