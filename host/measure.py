# Decoders and method choice for the measurement programs (docs/spec/programs/measure_design.md): meter.s, freq.s,
# nec.s, autobaud.s. MicroPython and CPython. A stream is a list of entries in arrival order: an int (data byte) or
# ("ev", code), as host/monitor.py (entries(), iss_entries()).
EV = "ev"
HZ = 50000000


def iss_entries(chip, e):
    return [(EV, v & 63) if v & 0x100 else v for _, v in chip.host_out[e]]


def _is_ev(x, code=None):
    return isinstance(x, tuple) and (code is None or x[1] == code)


def meter(ent, pre=0, ext=0, inv=0):
    # -> [(level, clk, flag)]: level = pad level of the phase, clk = length (None = over range), flag None, "short"
    # (below the minimum: length unknown, shorter) or "wrap" (EXT: edge within 14 clk after a wrap, +-13.5 clk).
    # The first reading (before the timer started) is dropped.
    hmin, lmin = (10, 14) if ext else (1, 22)
    out, w, b, k = [], 0, [], 0
    for x in ent:
        if _is_ev(x, 1):
            w += 1
            continue
        if _is_ev(x):
            continue
        b.append(x)
        if len(b) < 2:
            continue
        v = b[0] | b[1] << 8
        b = []
        logical = k & 1                         # readings: logical low, logical high, ...
        k += 1
        if k == 1:
            w = 0
            continue
        rem = 0x10000 - v
        if ext:
            clk = 65536 * w + rem
        elif v == 0:
            clk = None
        else:
            clk = rem if pre == 0 else (rem - 0.5) * 16 ** pre
        flag = None
        if clk is not None and clk < (hmin if logical else lmin) - 1:
            flag = "short"
        elif ext and w and rem <= 14:
            flag = "wrap"
        out.append((logical ^ inv, clk, flag))
        w = 0
    return out


def stats(phases, hz=HZ):
    # whole periods of valid phases: sums are exact to +-1 clk (consecutive phases share their edge sample)
    good = [p for p in phases if p[1] is not None and p[2] is None]
    hi = [p[1] for p in good if p[0]]
    lo = [p[1] for p in good if not p[0]]
    n = min(len(hi), len(lo))
    if n == 0:
        return None
    per = (sum(hi[:n]) + sum(lo[:n])) / n
    return {"n": n, "high": sum(hi[:n]) / n, "low": sum(lo[:n]) / n, "period": per, "freq": hz / per,
            "duty": sum(hi[:n]) / (sum(hi[:n]) + sum(lo[:n]))}


def freq(ent):
    # -> [(edges, lost)] per window; the first window (started by the RUN) is dropped
    out, b, lost = [], [], False
    for x in ent:
        if _is_ev(x, 2):
            lost = True
            continue
        if _is_ev(x):
            continue
        b.append(x)
        if len(b) == 5:
            s = b[0] | b[1] << 8
            out.append((bin(s).count("1") + 16 * (b[2] | b[3] << 8), lost))
            b, lost = [], False
    return out[1:]


def freq_hz(edges, gate, pre=0, hz=HZ):
    return edges * hz / (gate * 16 ** pre)


def gate_for(seconds, hz=HZ):
    # freq.s -D PRE, GATE for a window of `seconds` (window = GATE * 16^PRE clk, GATE <= 65535 - K)
    for pre in (0, 1, 2):
        k = 8 if pre == 0 else 1
        g = round(seconds * hz / 16 ** pre)
        if g <= 65535 - k:
            return pre, g
    raise ValueError("window longer than 335 ms")


def nec(ent):
    # -> [{"addr", "cmd", "ext", "ok"} | {"repeat": True} | {"broken": True}]; ext = 16-bit address (extended NEC)
    out, b = [], []
    for x in ent:
        if _is_ev(x, 1):
            out.append({"repeat": True})
        elif _is_ev(x, 2):
            out.append({"broken": True})
            b = []
        elif not _is_ev(x):
            b.append(x)
            if len(b) == 4:
                a, na, c, nc = b
                ext = a ^ na != 0xFF
                out.append({"addr": a | na << 8 if ext else a, "cmd": c, "ext": ext, "ok": c ^ nc == 0xFF})
                b = []
    return out


def autobaud(ent, hz=HZ):
    # -> {"bit": mean bit in clk, "baud", "tcfg", "chars": [(value, framing_error)], "refused": evt 3 count}
    r = {"bit": None, "baud": None, "tcfg": None, "chars": [], "refused": 0}
    k, n = 0, len(ent)
    while k < n:
        x = ent[k]
        if _is_ev(x, 3):
            r["refused"] += 1
        elif _is_ev(x, 4) and k + 4 < n:
            b = ent[k + 1:k + 5]
            tc = b[2] | b[3] << 8
            r["bit"] = ((b[0] | b[1] << 8) + (tc & 0xFF) / 256) * 16 ** (tc >> 8)
            r["baud"] = hz / r["bit"]
            r["tcfg"] = tc
            k += 4
        elif _is_ev(x, 1) and r["chars"]:
            v, _ = r["chars"][-1]
            r["chars"][-1] = (v, True)
        elif not _is_ev(x):
            r["chars"].append((x, False))
        k += 1
    return r


def choose(f_est, b=16, hz=HZ):
    # method for a signal near f_est Hz (from a 10 ms freq.s window): period capture while the link carries every
    # period (4 bytes/period, 10 B clk per byte, 64 % margin): error 1 clk / T at any f; else gate counting: 1 edge / T.
    # -> ("meter", PRE, EXT) or ("freq", None, None)
    if f_est > 0.64 * hz / (40 * b):
        return ("freq", None, None)
    per = hz / f_est if f_est > 0 else float("inf")
    for pre in (0, 1, 2):
        if per < 65536 * 16 ** pre:
            return ("meter", pre, 0)
    return ("meter", 0, 1)
