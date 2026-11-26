# Scenario runner (docs/spec/scenario_design.md, D225/D226): YAML -> conflict check -> backend -> PASS/FAIL report.
# PC side only; the board half of the cold start is host/coldstart.py (MicroPython).
import os, re, sys
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (os.path.join(ROOT, "sw"), os.path.join(ROOT, "sw", "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)
from jspe_asm import assemble, decode, AsmError   # noqa: E402
from jspe_iss import Chip                         # noqa: E402
import models                                     # noqa: E402

PROG = os.path.join(ROOT, "docs", "spec", "programs")
LINK = {("ui", 3), ("uo", 4), ("uo", 5)}
LFSR_CREGS = {16, 17, 18, 19, 20}                 # LFSR_LO/HI, POLY_LO/HI, LFCFG (isa.yaml)
TAPCFG, LFEN = 1, 1 << 3
KEYS = {"name", "clk_hz", "programs", "lfsr_share", "gpio", "stimulus", "expect", "timeout", "settle", "backends"}


class Conflict(Exception):
    def __init__(self, msgs):
        super().__init__("; ".join(msgs))
        self.msgs = msgs


def mem_map():
    out = {}
    for ln in open(os.path.join(PROG, "cleo_mem.inc")):
        m = re.match(r"\s*\.equ\s+(\w+),\s*(\d+)", ln)
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


MAP = mem_map()


def pin(s):
    m = re.fullmatch(r"(ui_in|uo_out|uio)\[([0-7])\](:od)?", str(s).strip())
    if not m:
        raise Conflict(["bad pin '%s' (ui_in[n], uo_out[n], uio[n], optional :od)" % s])
    return {"ui_in": "ui", "uo_out": "uo", "uio": "uio"}[m.group(1)], int(m.group(2)), bool(m.group(3))


def pname(k, n):
    return {"ui": "ui_in", "uo": "uo_out", "uio": "uio"}[k] + "[%d]" % n


def load(src):
    scn = yaml.safe_load(open(src) if os.path.exists(str(src)) else src)
    if not isinstance(scn, dict):
        raise Conflict(["scenario must be a mapping"])
    bad = set(scn) - KEYS
    if bad:
        raise Conflict(["unknown keys %s" % sorted(bad)])
    for k in ("name", "programs", "expect", "timeout"):
        if k not in scn:
            raise Conflict(["missing '%s'" % k])
    scn["_dir"] = os.path.dirname(os.path.abspath(src)) if os.path.exists(str(src)) else os.getcwd()
    return scn


def _path(scn, f):
    for d in (scn.get("_dir", "."), PROG):
        p = os.path.join(d, f)
        if os.path.exists(p):
            return p
    raise Conflict(["program file '%s' not found" % f])


def _defs(path, D):
    return {"%s:%s" % (os.path.basename(path), k): str(v) for k, v in (D or {}).items()}


def engine_words(img, e):
    ws = [w for w, _, _ in img.engines[e]["init"]]
    return ws + [img.words[a] for a in sorted(img.owner) if img.owner[a] == e and a in img.words]


def lfsr_user(ws):
    for k, w in enumerate(ws):
        i, f = decode(w)
        if i is None or i["name"] not in ("wcfg", "rcfg"):
            continue
        if f["creg"] in LFSR_CREGS:
            return True
        if i["name"] == "wcfg" and f["creg"] == TAPCFG:
            v = None                               # value of rs from li/lih just before; unknown counts as a user
            for w2 in ws[max(0, k - 3):k]:
                i2, f2 = decode(w2)
                if i2 is not None and i2["name"] in ("li", "lih") and f2["rd"] == f["rs"]:
                    v = f2["imm"] if i2["name"] == "li" else ((v or 0) & 0xFF) | f2["imm"] << 8
            if v is None or v & LFEN:
                return True
    return False


def check(scn):
    # returns (image, plan); raises Conflict listing every hit
    err, subs, claimed = [], [], {}
    paths = []
    for p in scn["programs"]:
        path = _path(scn, p["file"])
        paths.append(path)
        try:
            sub = assemble([path], _defs(path, p.get("D")))
        except AsmError as x:
            raise Conflict(["%s: %s" % (p["file"], x)])
        subs.append((p, sub))
        got, want = sorted(sub.engines), sorted(p.get("engines", []))
        if got != want:
            err.append("engines: %s uses %s, scenario says %s" % (p["file"], got, want))
        for e in got:
            if e in claimed:
                err.append("engines: E%d in %s and %s" % (e, claimed[e], p["file"]))
            claimed[e] = p["file"]
        if sub.clk is not None and sub.clk != scn.get("clk_hz", sub.clk):
            err.append("clock: %s CLK_HZ %d != clk_hz %d" % (p["file"], sub.clk, scn["clk_hz"]))
    maps = {p["file"]: s.slotmap for p, s in subs}
    set_maps = {v for v in maps.values() if v is not None}
    if len(set_maps) > 1:
        err.append("slot map: .slots differ %s" % {f: hex(v) for f, v in maps.items() if v is not None})
    elif set_maps and None in maps.values():
        err.append("slot map: %s assume the default map, %s set .slots" % (
            [f for f, v in maps.items() if v is None], [f for f, v in maps.items() if v is not None]))
    if err:
        raise Conflict(err)
    defs = {}
    for (p, _), path in zip(subs, paths):
        defs.update(_defs(path, p.get("D")))
    try:
        img = assemble(paths, defs)
    except AsmError as x:
        raise Conflict(["memory/pads: %s" % x])
    sm = img.slots()
    for e in img.engines:
        if all((sm >> 2 * s) & 3 != e for s in range(4)):
            err.append("slot map: E%d owns no slot in %#04x" % (e, sm))
    for a in img.words:
        if a >= MAP["MEM_DATA"] and a not in img.reserved:
            err.append("memory: word %d at or above MEM_DATA is not in a .reserve" % a)
            break
    users = sorted(e for e in img.engines if lfsr_user(engine_words(img, e)))
    groups = [set(g) for g in scn.get("lfsr_share", []) or []]
    if len(users) > 1 and not any(set(users) <= g for g in groups):
        err.append("LFSR: engines %s use the shared LFSR; list them in one lfsr_share group" % users)
    plan = gpio_plan(scn, img, err)
    if err:
        raise Conflict(err)
    return img, plan


def gpio_plan(scn, img, err):
    chip = {}                                      # (kind, n) -> od, pads the chip drives
    for e, d in img.engines.items():
        for idx, cfg in d["pads"].items():
            chip[("uo", idx) if idx < 8 else ("uio", idx - 8)] = bool(cfg & 0x20)
    g = scn.get("gpio") or {}
    ext, plan = {}, {"rp": [("ui", 3)], "pmod": [], "od": set()}
    for who in ("rp", "pmod"):
        for s in g.get(who, []) or []:
            k, n, od = pin(s)
            key = (k, n)
            if key in ext:
                err.append("pads: %s listed twice (%s, %s)" % (pname(k, n), ext[key], who))
                continue
            ext[key] = who
            if key in LINK:
                err.append("pads: %s is a host link pad" % pname(k, n))
            elif k == "uo":
                err.append("pads: %s is a chip output; the %s must not drive it" % (pname(k, n), who))
            elif key in chip and not (chip[key] and od):
                err.append("pads: %s driven by the chip (%s) and by the %s" % (
                    pname(k, n), "od" if chip[key] else "push-pull", who))
            plan[who].append(key)
            if od:
                plan["od"].add(key)
    for st in scn.get("stimulus", []) or []:
        pt = st.get("partner")
        if pt and "pin" in pt and pin(pt["pin"])[:2] not in ext:
            err.append("pads: partner pin %s not in gpio.rp or gpio.pmod" % pt["pin"])
    plan["inputs"] = [(k, n) for k in ("ui", "uo", "uio") for n in range(8)
                      if (k, n) not in plan["rp"] and (k, n) not in plan["pmod"]]
    return plan


def board_plan(plan):
    # what host/coldstart.py needs: ui_in / uio bits the RP2350 drives; everything else input, no pull
    return {"rp_ui": sorted(n for k, n in plan["rp"] if k == "ui"),
            "rp_uio": sorted(n for k, n in plan["rp"] if k == "uio"),
            "pmod": [pname(k, n) for k, n in plan["pmod"]]}


# ---------------- ISS backend ----------------
class UartPartner:
    def __init__(self, t0, a):
        self.m = models.UartSender(a["bit"], a["bytes"], t0=t0 + 0.5)

    def level(self, t):
        return self.m.level(t)

    def observe(self, t, b):
        pass


class CanPartner:
    def __init__(self, t0, a):
        self.m = models.CanNode(a["bit"])
        ident, data = a["frame"]
        self.m.send(t0, ident, data)

    def level(self, t):
        return self.m.drive(t)

    def observe(self, t, b):
        self.m.observe(t, b)


class Idle:
    # a partner pin before its partner starts: idle high (UART) or released and pulled up (od)
    def level(self, t):
        return 1

    def observe(self, t, b):
        pass


PARTNERS = {"uart": UartPartner, "can_node": CanPartner}


def _trigger_due(st, c, t_rel, fired):
    if "at" in st:
        return t_rel >= st["at"]
    a = st["after"]
    hits = [t for t, v in c.host_out[a["e"]] if v & 0x100 and v & 0xFF == a["evt"]]
    return bool(hits) and c.cycle >= hits[0] + a.get("delay", 0)


def run_iss(scn, img, phase=0):
    c = Chip()
    c.boot(img, phase=phase)
    t0 = c.t_run
    stim = list(scn.get("stimulus", []) or [])
    fired = [False] * len(stim)
    partners = {}                                   # (kind, n) -> (kind, n, od, partner); a later partner replaces
    for st in stim:
        if "partner" in st:
            k2, n, od = pin(st["partner"]["pin"])
            partners[(k2, n)] = (k2, n, od or st["partner"].get("od", False), Idle())
    exp = scn["expect"]
    settle = scn.get("settle", 2000)
    done_at = None
    for _ in range(scn["timeout"]):
        t = c.cycle
        for k, st in enumerate(stim):
            if fired[k] or not _trigger_due(st, c, t - t0, fired):
                continue
            fired[k] = True
            if "send" in st:
                s = st["send"]
                data = models.can_host_tx(s["can"][0], s["can"][1]) if "can" in s else list(s["data"])
                c.host_in[s["e"]] += data
            if "fault" in st:
                c.mem[MAP["FAULT_W"] + st["fault"]["e"]] = st["fault"]["word"] & 0xFFFF
            if "partner" in st:
                a = st["partner"]
                k2, n, od = pin(a["pin"])
                partners[(k2, n)] = (k2, n, od or a.get("od", False), PARTNERS[a["model"]](t, a))
        ts = t + 1 - 1e-6
        ui, uio, act = 0x08, 0, list(partners.values())
        for n in range(8):                          # uio: wired AND of chip and partners, od lines pulled up
            drv = [p.level(ts) for k2, n2, _, p in act if k2 == "uio" and n2 == n]
            if c.uio_oe >> n & 1:
                drv.append(c.uio_out >> n & 1)
            pulled = any(od for k2, n2, od, _ in act if k2 == "uio" and n2 == n)
            uio |= (min(drv) if drv else int(pulled)) << n
        for k2, n, _, p in act:
            if k2 == "ui":
                ui = ui & ~(1 << n) | p.level(ts) << n
        for k2, n, _, p in act:
            p.observe(t, (uio if k2 == "uio" else ui) >> n & 1)
        c.step(ui, uio)
        if (c.cycle & 255) == 0 and done_at is None and all(fired) and all(
                ok for ok, _ in (judge(x, c, t0, final=False) for x in exp)):
            done_at = c.cycle
        if done_at is not None and c.cycle >= done_at + settle:
            break
    res = [(x, *judge(x, c, t0, final=True)) for x in exp]
    if not all(fired):
        res.append(({"stimulus": "fired"}, False, "%d of %d stimuli never fired" % (fired.count(False), len(fired))))
    return res, c


def _pad_changes(c, k, n):
    if k == "uo":
        f = lambda uo, uout, uoe: uo >> n & 1
    else:
        f = lambda uo, uout, uoe: (uout >> n & 1) if uoe >> n & 1 else 1
    return models.trace_levels(c.pad_trace, f, init=1 if k == "uio" else 0)


def judge(x, c, t0, final):
    if "stream" in x or "events" in x or "fifo" in x:
        key = "stream" if "stream" in x else "events" if "events" in x else "fifo"
        e = x[key]["e"]
        out = c.host_out[e]
        if key == "stream":
            got = [(t, v) for t, v in out if not v & 0x100]
        elif key == "events":
            got = [(t, v & 0xFF) for t, v in out if v & 0x100]
        else:
            got = list(out)
        want = list(x["data"])
        vals = [v for _, v in got]
        ok = vals[:len(want)] == want and (x.get("prefix") or not final or len(vals) == len(want))
        last = got[len(want) - 1][0] - t0 if len(got) >= len(want) and want else 0
        if "within" in x and last > x["within"]:
            ok = False
        return ok, "%s E%d: %d entries%s, want %d, last at %d clk%s" % (
            key, e, len(vals), "" if ok else " %s" % [hex(v) for v in vals[:16]], len(want), last,
            " (<= %d)" % x["within"] if "within" in x else "")
    if "pad" in x:
        k, n, _ = pin(x["pad"])
        u = x["uart"]
        ch = [(t - t0, v) for t, v in _pad_changes(c, k, n) if t >= t0]
        dec = models.uart_decode(ch, u["bit"])
        vals = [d[1] for d in dec]
        drift = 0.0
        for st, _, _, _ in dec:                     # each edge inside a char vs. the nominal bit grid
            for t, _ in ch:
                if st < t < st + 10 * u["bit"]:
                    kb = round((t - st) / u["bit"])
                    if kb:
                        drift = max(drift, abs(t - st - kb * u["bit"]) / (kb * u["bit"]))
        tol = u.get("tol", 0.02)
        ok = vals == list(x["data"]) and all(d[3] for d in dec) and drift <= tol
        return ok, "pad %s uart: %d chars%s, drift %.3f (<= %.3f)" % (
            x["pad"], len(vals), "" if vals == list(x["data"]) else " %s" % [hex(v) for v in vals[:16]], drift, tol)
    if "clean" in x:
        what = x["clean"] if isinstance(x["clean"], list) else [x["clean"]]
        bad = []
        for e in c.eng:
            for w in what:
                if w in ("ovr", "late", "trap") and getattr(e, w, 0):
                    bad.append("E%d %s" % (e.idx, w))
        if "undef" in what and c.undef_reads:
            bad.append("undef reads %d" % c.undef_reads)
        return (not bad), "clean %s: %s" % ("/".join(what), ", ".join(bad) or "none")
    raise Conflict(["unknown expect %s" % x])


def report(name, res):
    lines = ["%s %s" % ("PASS" if ok else "FAIL", d) for _, ok, d in res]
    verdict = all(ok for _, ok, _ in res)
    lines.append("%s %s" % ("PASS" if verdict else "FAIL", name))
    return verdict, "\n".join(lines)


def run(src, backend="iss", phase=0):
    scn = load(src)
    img, plan = check(scn)
    if backend != "iss":
        raise NotImplementedError("backend %s: scenario step 3" % backend)
    res, _ = run_iss(scn, img, phase)
    return report(scn["name"], res)


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Cleo scenario runner")
    ap.add_argument("scenario")
    ap.add_argument("--backend", default="iss", choices=["iss", "rtl", "board"])
    ap.add_argument("--phase", type=int, default=0)
    a = ap.parse_args(argv)
    try:
        ok, text = run(a.scenario, a.backend, a.phase)
    except Conflict as x:
        print("\n".join("REJECT " + m for m in x.msgs))
        return 2
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
