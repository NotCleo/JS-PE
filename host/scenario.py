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
KEYS = {"name", "clk_hz", "programs", "lfsr_share", "gpio", "stimulus", "expect", "timeout", "settle", "backends",
        "link", "link_B", "note"}


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
    # third field: "" push-pull, "od" open drain (line pulled up), "shared" half duplex bus the protocol arbitrates (USB)
    m = re.fullmatch(r"(ui_in|uo_out|uio)\[([0-7])\](?::(od|shared))?", str(s).strip())
    if not m:
        raise Conflict(["bad pin '%s' (ui_in[n], uo_out[n], uio[n], optional :od or :shared)" % s])
    return {"ui_in": "ui", "uo_out": "uo", "uio": "uio"}[m.group(1)], int(m.group(2)), m.group(3) or ""


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
    import fault
    for st in expand(scn)[0]:                      # fault words: only to a reserved FAULT_W + e, armed and defined
        if "fault" in st:
            e, w = st["fault"]["e"], st["fault"]["word"]
            if MAP["FAULT_W"] + e not in img.reserved:
                err.append("fault: E%d has no reserved fault word (FAULTS=1 build of its program)" % e)
            try:
                fault.check(w)
            except ValueError as x:
                err.append("fault: %s" % x)
    if err:
        raise Conflict(sorted(set(err), key=err.index))
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
            if who == "rp" and od:
                err.append("pads: %s: gpio.rp pins are push-pull RP2350 outputs; :od / :shared only in gpio.pmod" % s)
            if key in LINK:
                err.append("pads: %s is a host link pad" % pname(k, n))
            elif k == "uo":
                err.append("pads: %s is a chip output; the %s must not drive it" % (pname(k, n), who))
            elif key in chip and not ((chip[key] and od == "od") or od == "shared"):
                err.append("pads: %s driven by the chip (%s) and by the %s" % (
                    pname(k, n), "od" if chip[key] else "push-pull", who))
            plan[who].append(key)
            if od == "od":
                plan["od"].add(key)
    owner = {}
    for st in scn.get("stimulus", []) or []:
        for s in partner_pins(st.get("partner")):
            key = pin(s)[:2]
            if key not in ext:
                err.append("pads: partner pin %s not in gpio.rp or gpio.pmod" % s)
            if key in owner:
                err.append("pads: two partners on %s" % pname(*key))
            owner[key] = st["partner"].get("id", st["partner"]["model"])
    plan["partner"] = owner
    plan["inputs"] = [(k, n) for k in ("ui", "uo", "uio") for n in range(8)
                      if (k, n) not in plan["rp"] and (k, n) not in plan["pmod"]]
    return plan


def partner_pins(pt):
    if not pt:
        return []
    return list(pt.get("pins", [])) + ([pt["pin"]] if "pin" in pt else [])


def board_plan(plan):
    # what host/coldstart.py needs: ui_in / uio bits the RP2350 drives; everything else input, no pull
    return {"rp_ui": sorted(n for k, n in plan["rp"] if k == "ui"),
            "rp_uio": sorted(n for k, n in plan["rp"] if k == "uio"),
            "pmod": [pname(k, n) for k, n in plan["pmod"]]}




# ---------------- partners: models of what sits on the pads (ISS and RTL backends) ----------------
class Partner:
    override = False                     # True: levels() already include the chip's own drive (protocol models)

    def __init__(self, run, t0, a):
        self.run, self.a, self.t0 = run, a, t0
        self.pins = [pin(s) for s in partner_pins(a)]

    def key(self, i=0):
        return self.pins[i][:2]

    def levels(self, t):
        return {}

    def observe(self, t, ui, uio):
        pass

    def verdict(self, x):
        return True, "partner %s started" % self.a.get("id", self.a["model"])


def _level(key, ui, uio):
    return ((uio if key[0] == "uio" else ui) >> key[1]) & 1


class Idle(Partner):
    # a partner's pins before it starts: idle (UART high, od released) or the levels given by `idle`
    def levels(self, t):
        idle = self.a.get("idle", [1] * len(self.pins))
        return {self.key(i): idle[i] for i in range(len(self.pins))}


class UartPartner(Partner):
    def __init__(self, run, t0, a):
        super().__init__(run, t0, a)
        self.m = models.UartSender(a["bit"], a["bytes"], parity=a.get("parity", 0), t0=t0 + 0.5)

    def levels(self, t):
        return {self.key(): self.m.level(t + 1 - 1e-6)}


class SquarePartner(Partner):
    def levels(self, t):
        h, lo = self.a["high"], self.a["low"]
        return {self.key(): int((t + 1 - self.t0) % (h + lo) < h)}


class WirePartner(Partner):
    # a jumper from a chip output pad (`from`) to an input pad
    def levels(self, t):
        k, n, _ = pin(self.a["from"])
        c = self.run.c
        v = c.uo >> n & 1 if k == "uo" else (c.uio_out >> n & 1 if c.uio_oe >> n & 1 else 1)
        return {self.key(): v}


class CanPartner(Partner):
    def __init__(self, run, t0, a):
        super().__init__(run, t0, a)
        self.m = models.CanNode(a["bit"])
        if "frame" in a:
            self.m.send(t0 + a.get("delay", 0), a["frame"][0], a["frame"][1])

    def levels(self, t):
        return {self.key(): self.m.drive(t)}

    def observe(self, t, ui, uio):
        self.m.observe(t, _level(self.key(), ui, uio))

    def verdict(self, x):
        got = [[i, list(d)] for i, d, _ in self.m.frames]
        want = [[i, list(d)] for i, d in x.get("frames", [])]
        ok = got == want and not self.m.errors and ("acked" not in x or self.m.acked == x["acked"])
        return ok, "can node: frames %s%s, acked %s, errors %s" % (
            [hex(i) for i, _ in got], "" if got == want else " want %s" % [hex(i) for i, _ in want],
            self.m.acked, self.m.errors or "none")


class UsbPartner(Partner):
    # USB LS host model + RP2350 stand-in (sw/tests/usb_host.py); needs link: wire (the stand-in writes the buffer)
    override = True

    def __init__(self, run, t0, a):
        super().__init__(run, t0, a)
        import usb_host as U
        self.U = U
        cr = a.get("corrupt_req")
        self.host = U.UsbHost(corrupt_setup=(lambda p: p[1] == cr) if cr is not None else None, full=a.get("full", True))
        hl = run.hl
        self.rp = U.Rp2350(run.img, hl.send, lambda: len(hl.q))

    def levels(self, t):
        c = self.run.c
        st, chip = self.U.bus_state(c.uio_out, c.uio_oe, self.host.drive(t))
        self.host.tick(t, st, chip)
        self.rp.tick(t, st, self.run.hl.got)
        return {self.key(0): st[0], self.key(1): st[1]}

    def verdict(self, x):
        h, rp, U = self.host, self.rp, self.U
        desc = {"dev0": U.DEV_DESC, "dev": U.DEV_DESC, "cfg9": U.CFG_DESC[:9], "cfg": U.CFG_DESC,
                "report": U.REPORT_DESC, "report2": U.REPORT_DESC, "reports": U.REPORTS}
        bad = [k for k in x.get("result", []) if h.result.get(k) != desc[k]]
        if not h.done:
            bad.append("host script not finished")
        if rp.addr != x.get("addr", 7) or rp.resets != 1:
            bad.append("addr %d resets %d" % (rp.addr, rp.resets))
        if h.dups or h.clash or rp.notes:
            bad.append("dups %d clash %s notes %s" % (h.dups, h.clash, rp.notes))
        if "corrupt_req" in self.a and (rp.crc_events != 1 or h.timeouts != 1):
            bad.append("crc events %d, host timeouts %d (want 1, 1)" % (rp.crc_events, h.timeouts))
        return not bad, "usb host: %s" % (", ".join(bad) or "enumerated, addr %d, %s" % (rp.addr, x.get("result")))


PARTNERS = {"uart": UartPartner, "can_node": CanPartner, "square": SquarePartner, "wire": WirePartner,
            "usb_host": UsbPartner}
ESC = 0x1B


def expand(scn):
    # campaign entries -> fault + send stimuli (host/fault.py, same seed -> same campaign on the board)
    out, camp = [], None
    for st in scn.get("stimulus", []) or []:
        if "campaign" not in st:
            out.append(st)
            continue
        import fault
        a = st["campaign"]
        ar = {int(k): tuple(v) for k, v in (a.get("arg_range") or {}).items()}
        cw = dict(fault.campaign(a["seed"], a["prog"], len(a["data"]), a["faults"], a.get("types"), a.get("pos_max"),
                                 ar or None))
        for k, ch in enumerate(a["data"]):
            t = st.get("at", 0) + k * a["every"]
            if k in cw:
                out.append({"at": t, "fault": {"e": a["e"], "word": cw[k]}})
            out.append({"at": t, "send": {"e": a["e"], "data": [ch]}})
        camp = (list(a["data"]), cw)
    return out, camp


class Run:
    # one scenario on a clocked chip model: c = ISS Chip (direct, or wire link with hl), or the ISS twin of the RTL
    def __init__(self, scn, img, c, hl=None, sample=True):
        self.scn, self.img, self.c, self.hl, self.sample = scn, img, c, hl, sample
        self.t0 = c.cycle
        self.stim, self.campaign = expand(scn)
        self.fired = [False] * len(self.stim)
        self.partners, self.pinmap = {}, {}          # id -> partner; (kind, n) -> (mode, partner) latest on the pin
        for st in self.stim:
            if "partner" in st:
                self._attach(Idle(self, 0, st["partner"]), st["partner"], idle=True)
        self.out, self.notes = [[] for _ in range(4)], []
        self.pi, self.cur = (len(hl.got) if hl else 0), None
        g = scn.get("gpio") or {}
        self.pull = {("uio", i - 8) for d in img.engines.values() for i, cfg in d["pads"].items() if i >= 8 and cfg & 0x20}
        self.pull |= {pin(x)[:2] for who in ("rp", "pmod") for x in (g.get(who) or []) if pin(x)[2] == "od"}
        self.timed_out = False
        self.done_at = None

    def _attach(self, p, a, idle=False):
        if not idle:
            self.partners[a.get("id", a["model"])] = p
        for s in partner_pins(a):
            k, n, mode = pin(s)
            self.pinmap[(k, n)] = (mode, p)

    def restart(self):
        # wire link: drop what arrived before (boot break notice) and start the scenario clock now
        self.hl.got.clear()
        self.pi, self.cur, self.t0 = 0, None, self.c.cycle
        self.out, self.notes = [[] for _ in range(4)], []

    def host_out(self, e):
        return self.c.host_out[e] if self.hl is None else self.out[e]

    def _due(self, st):
        if "at" in st:
            return self.c.cycle - self.t0 >= st["at"]
        a = st["after"]
        hits = [t for t, v in self.host_out(a["e"]) if v & 0x100 and v & 0xFF == a["evt"]]
        return bool(hits) and self.c.cycle >= hits[0] + a.get("delay", 0)

    def _fire(self, st):
        c, hl = self.c, self.hl
        if "send" in st:
            s = st["send"]
            data = models.can_host_tx(s["can"][0], s["can"][1]) if "can" in s else list(s["data"])
            if hl is None:
                c.host_in[s["e"]] += data
            else:
                hl.send(bytes([ESC, s["e"], ESC, s["e"]]))
                for b in data:
                    hl.send(bytes([ESC, ESC]) if b == ESC else bytes([b]), is_data=True)
        if "fault" in st:
            a, w = MAP["FAULT_W"] + st["fault"]["e"], st["fault"]["word"] & 0xFFFF
            if hl is None:
                c.mem[a] = w
            else:
                hl.send(bytes([ESC, 0x70, a & 0xFF, a >> 8, 0, w & 0xFF, w >> 8]))
        if "partner" in st:
            a = st["partner"]
            self._attach(PARTNERS[a["model"]](self, c.cycle, a), a)

    def inputs(self, fire=True):
        c, t = self.c, self.c.cycle
        if fire:
            for k, st in enumerate(self.stim):
                if not self.fired[k] and self._due(st):
                    self.fired[k] = True
                    self._fire(st)
        act = {}
        for mode, p in self.pinmap.values():
            if id(p) not in act:
                act[id(p)] = (p, p.levels(t))
        lv = {}
        for key, (mode, p) in self.pinmap.items():
            lv[key] = (mode, p.override, act[id(p)][1][key])
        ui = 0x08 if self.hl is None else self.hl.level(c) << 3
        uio = 0
        for n in range(8):
            if ("ui", n) in lv:
                ui = ui & ~(1 << n) | lv[("ui", n)][2] << n
            mode, ovr, v = lv.get(("uio", n), ("", False, None))
            if ovr:
                uio |= v << n
                continue
            drv = [] if v is None else [v]
            if c.uio_oe >> n & 1:
                drv.append(c.uio_out >> n & 1)
            uio |= (min(drv) if drv else int(mode == "od" or ("uio", n) in self.pull)) << n
        for p, _ in act.values():
            p.observe(t, ui, uio)
        return ui, uio

    def after(self):
        hl, c = self.hl, self.c
        if hl is None:
            return
        if self.sample:
            hl.sample(c)
        g = hl.got
        while self.pi < len(g):                      # chip -> host bytes (HOSTIF 4); no reply ops while running
            b = g[self.pi]
            if b != ESC:
                if self.cur is not None:
                    self.out[self.cur].append((c.cycle, b))
                self.pi += 1
                continue
            if self.pi + 1 >= len(g):
                return
            x = g[self.pi + 1]
            self.pi += 2
            if x == ESC and self.cur is not None:
                self.out[self.cur].append((c.cycle, ESC))
            elif x <= 3:
                self.cur = x
            elif 0x40 <= x < 0x80 and self.cur is not None:
                self.out[self.cur].append((c.cycle, 0x100 | x & 63))
            else:
                self.notes.append(x)

    def finished(self):
        c = self.c
        if self.done_at is None and (c.cycle & 255) == 0 and all(self.fired) and all(
                judge(x, self, final=False)[0] for x in self.scn["expect"]):
            self.done_at = c.cycle
        return self.done_at is not None and c.cycle >= self.done_at + self.scn.get("settle", 2000)

    def results(self):
        res = [(x, *judge(x, self, final=True)) for x in self.scn["expect"]]
        if not all(self.fired):
            res.append(({}, False, "%d of %d stimuli never fired" % (self.fired.count(False), len(self.fired))))
        if self.timed_out:
            res.append(({}, False, "timeout: expectations not all met within %d clk" % self.scn["timeout"]))
        return res


def run_iss(scn, img, phase=0):
    wire = scn.get("link") == "wire"
    c = Chip(direct=not wire)
    c.boot(img, phase=phase, run=not wire)
    if not wire:
        r = Run(scn, img, c)
        r.t0 = c.t_run
    else:
        from jspe_iss import HostLink
        r = Run(scn, img, c, HostLink(scn.get("link_B", 16)))
        for _ in range(800):                         # boot() held H_RX low (a break): let its notice pass, engines stopped
            c.step(*r.inputs(fire=False))
            r.after()
        while c.slot != phase:
            c.step(*r.inputs(fire=False))
            r.after()
        r.restart()
        c.start(sorted(img.engines))                 # RUN now: t0 = RUN on both ISS modes
    for _ in range(scn["timeout"]):
        c.step(*r.inputs())
        r.after()
        if r.finished():
            break
    else:
        r.timed_out = True
    return r.results(), r


# ---------------- expects ----------------
def _pad_changes(c, k, n, t0):
    if k == "uo":
        f = lambda uo, uout, uoe: uo >> n & 1
    else:
        f = lambda uo, uout, uoe: (uout >> n & 1) if uoe >> n & 1 else 1
    return [(t - t0, v) for t, v in models.trace_levels(c.pad_trace, f, init=1 if k == "uio" else 0) if t >= t0]


def spi_decode(c, a, t0):
    keys = {s: pin(a[s])[:2] for s in ("sck", "cs", "mosi")}
    def lvl(k, uo, uout, uoe):
        return uo >> k[1] & 1 if k[0] == "uo" else ((uout >> k[1] & 1) if uoe >> k[1] & 1 else 1)
    mode, bits = a.get("mode", 0), a.get("bits", 8)
    rise = mode in (0, 3)
    out, cur, prev, ragged = [], [], None, 0
    for t, uo, uout, uoe in c.pad_trace:
        if t < t0:
            continue
        sck, cs, mosi = (lvl(keys[s], uo, uout, uoe) for s in ("sck", "cs", "mosi"))
        if prev is not None:
            if cs == 0 and prev[0] != sck and sck == int(rise):
                cur.append(mosi)
            if prev[1] == 0 and cs == 1:
                ragged += len(cur) % bits != 0     # a frame that is not a whole number of words
                out += [sum(b << (bits - 1 - i) for i, b in enumerate(cur[k:k + bits]))
                        for k in range(0, len(cur) - bits + 1, bits)]
                cur = []
        prev = (sck, cs)
    return out, ragged


def _decoder(name):
    import importlib
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    mod, fn = name.split(".")
    return getattr(importlib.import_module(mod), fn)


def judge(x, r, final):
    c, t0 = r.c, r.t0
    if "partner" in x:
        p = r.partners.get(x["partner"])
        return (False, "partner %s never started" % x["partner"]) if p is None else p.verdict(x)
    if "stream" in x or "events" in x or "fifo" in x:
        key = "stream" if "stream" in x else "events" if "events" in x else "fifo"
        e = x[key]["e"]
        out = r.host_out(e)
        if key == "stream":
            got = [(t, v) for t, v in out if not v & 0x100]
        elif key == "events":
            got = [(t, v & 0xFF) for t, v in out if v & 0x100]
        else:
            got = list(out)
        for i, m in (x.get("mask") or {}).items():
            if int(i) < len(got):
                got[int(i)] = (got[int(i)][0], got[int(i)][1] & m)
        if "from_campaign" in x:
            data, cw = r.campaign
            tpl = {int(k): v for k, v in x["from_campaign"].items()}
            want = [ch if v == "ch" else v for k, ch in enumerate(data) for v in tpl[cw[k] >> 12 & 7 if k in cw else 0]]
        else:
            want = list(x["data"])
        vals = [v for _, v in got]
        ok = vals[:len(want)] == want and (x.get("prefix") or not final or len(vals) == len(want))
        last = got[len(want) - 1][0] - t0 if len(got) >= len(want) and want else 0
        if "within" in x and last > x["within"]:
            ok = False
        return ok, "%s E%d: %d entries%s, want %d, last at %d clk%s" % (
            key, e, len(vals), "" if ok else " %s" % [hex(v) for v in vals[:24]], len(want), last,
            " (<= %d)" % x["within"] if "within" in x else "")
    if "decode" in x:
        a = x["decode"]
        ent = [("ev", v & 63) if v & 0x100 else v for _, v in r.host_out(a["e"])]
        dec = _decoder(a["fn"])(ent, **(a.get("args") or {}))[a.get("skip", 0):]
        vals = [d[a["field"]] if "field" in a else d for d in dec]
        want, tol = list(x["data"]), x.get("tol", 0)
        ok = len(vals) >= len(want) and all(
            v == w or (tol and v is not None and abs(v - w) <= tol) for v, w in zip(vals, want))
        if x.get("exact") and len(vals) != len(want):
            ok = False
        return ok, "decode %s E%d: %d values %s, want %s (tol %s)" % (
            a["fn"], a["e"], len(vals), vals[:len(want) + 2], want, tol)
    if "pad" in x:
        k, n, _ = pin(x["pad"])
        u = x["uart"]
        ch = _pad_changes(c, k, n, t0)
        dec = models.uart_decode(ch, u["bit"], parity=u.get("parity", 0))
        vals = [d[1] for d in dec]
        drift = 0.0
        for st, _, _, _ in dec:                      # each edge inside a char vs. the nominal bit grid
            for t, _ in ch:
                if st < t < st + 10 * u["bit"]:
                    kb = round((t - st) / u["bit"])
                    if kb:
                        drift = max(drift, abs(t - st - kb * u["bit"]) / (kb * u["bit"]))
        tol = u.get("tol", 0.02)
        ok = vals == list(x["data"]) and all(d[3] for d in dec) and drift <= tol
        return ok, "pad %s uart: %d chars%s, drift %.3f (<= %.3f)" % (
            x["pad"], len(vals), "" if vals == list(x["data"]) else " %s" % [hex(v) for v in vals[:16]], drift, tol)
    if "spi" in x:
        got, ragged = spi_decode(c, x["spi"], t0)
        ok = got == list(x["data"]) and not ragged
        return ok, "spi %s: %d words%s%s" % (x["spi"]["mosi"], len(got), ", %d ragged frames" % ragged if ragged else "",
                                          "" if got == list(x["data"]) else " %s" % [hex(v) for v in got[:16]])
    if "clean" in x:
        what = x["clean"] if isinstance(x["clean"], list) else [x["clean"]]
        bad = []
        for e in c.eng:
            for w in what:
                if w in ("ovr", "late", "trap") and getattr(e, w, 0):
                    bad.append("E%d %s" % (e.idx, w))
            if "halt" in what and e.halted:
                bad.append("E%d halted" % e.idx)
        if "undef" in what and c.undef_reads:
            bad.append("undef reads %d" % c.undef_reads)
        if "notes" in what and r.notes:
            bad.append("notices %s" % [hex(v) for v in r.notes])
        na = " (notes not observable: direct ISS has no link)" if "notes" in what and r.hl is None else ""
        return (not bad), "clean %s: %s%s" % ("/".join(what), ", ".join(bad) or "none", na)
    raise Conflict(["unknown expect %s" % x])


def report(name, res):
    lines = ["%s %s" % ("PASS" if ok else "FAIL", d) for _, ok, d in res]
    verdict = all(ok for _, ok, _ in res)
    lines.append("%s %s" % ("PASS" if verdict else "FAIL", name))
    return verdict, "\n".join(lines)


# ---------------- board backend: files for host/scenario_board.py (MicroPython; not run on hardware) ----------------
BOARD_EXPECTS = ("stream", "events", "fifo", "decode")


def export_board(scn, img, plan, out_dir):
    import json
    stim, camp = expand(scn)
    hz = scn.get("clk_hz", img.clk or 50000000)
    steps, notes, released, t_prev = [], [], set(), 0.0
    for st in stim:
        if "after" in st:
            notes.append("event-triggered stimulus runs with the previous step (no event trigger on the board): %s" % st)
            t_us = t_prev
        else:
            t_us = st["at"] * 1e6 / hz
        t_prev = t_us
        if "send" in st:
            s = st["send"]
            data = models.can_host_tx(s["can"][0], s["can"][1]) if "can" in s else list(s["data"])
            steps.append({"t_us": t_us, "send": [s["e"], data]})
        if "fault" in st:
            steps.append({"t_us": t_us, "fault": [MAP["FAULT_W"] + st["fault"]["e"], st["fault"]["word"] & 0xFFFF]})
        if "partner" in st:
            a = st["partner"]
            if a["model"] == "square":
                steps.append({"t_us": t_us, "pwm": [pin(a["pin"])[1], hz / (a["high"] + a["low"]),
                                                    a["high"] / (a["high"] + a["low"])]})
            else:                                    # no on-board model: the pin stays an RP2350 input
                keys = [pin(x)[:2] for x in partner_pins(a)]
                released |= {k for k in keys if k in plan["rp"]}
                steps.append({"t_us": t_us, "operator": "attach a %s device to %s (no on-board %s partner; "
                              "the RP2350 leaves the pin an input)" % (a["model"], ", ".join(partner_pins(a)), a["model"])})
    exp = []
    for x in scn["expect"]:
        if any(k in x for k in BOARD_EXPECTS) and "from_campaign" not in x:
            exp.append(x)
        elif "from_campaign" in x:
            data, cw = camp
            tpl = {int(k): v for k, v in x["from_campaign"].items()}
            y = dict(x)
            del y["from_campaign"]
            y["data"] = [ch if v == "ch" else v for k, ch in enumerate(data) for v in tpl[cw[k] >> 12 & 7 if k in cw else 0]]
            exp.append(y)
        else:
            exp.append({"skip": "needs a logic analyser or the partner model: %s" % sorted(x)})
    bp = board_plan({**plan, "rp": [k for k in plan["rp"] if k not in released],
                     "pmod": plan["pmod"] + sorted(released)})
    levels = {}                                      # RP-driven partner pins start at their idle level
    for st in stim:
        a = st.get("partner")
        for i, s in enumerate(partner_pins(a)):
            k, n, _ = pin(s)
            if (k, n) in plan["rp"] and (k, n) not in released:
                levels["%s%d" % ("ui_in" if k == "ui" else "uio", n)] = a.get("idle", [1] * 8)[i]
    bp["levels"] = levels
    d = {"name": scn["name"], "clk_hz": hz, "timeout_us": scn["timeout"] * 1e6 / hz, "steps": steps,
         "expect": exp, "notes": notes, **bp}
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, scn["name"])
    open(base + ".bin", "wb").write(img.bin_bytes())
    open(base + ".json", "w").write(json.dumps(d, indent=1))
    return base + ".json", base + ".bin"


def run(src, backend="iss", phase=0, out_dir=None):
    scn = load(src)
    img, plan = check(scn)
    if scn.get("backends") and backend not in scn["backends"]:
        raise Conflict(["backend %s not in the scenario's backends %s" % (backend, scn["backends"])])
    if backend == "board":
        j, b = export_board(scn, img, plan, out_dir or ".")
        return True, "board files %s, %s: copy to the RP2350 with host/ and run scenario_board.run() (not run on hardware)" % (j, b)
    if backend == "rtl":
        return True, "rtl: make scenario S=%s BACKEND=rtl (cocotb, test/test_scenario.py)" % src
    res, _ = run_iss(scn, img, phase)
    return report(scn["name"], res)


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Cleo scenario runner")
    ap.add_argument("scenario")
    ap.add_argument("--backend", default="iss", choices=["iss", "rtl", "board"])
    ap.add_argument("--phase", type=int, default=0)
    ap.add_argument("--out", default=".")
    a = ap.parse_args(argv)
    try:
        ok, text = run(a.scenario, a.backend, a.phase, a.out)
    except Conflict as x:
        print("\n".join("REJECT " + m for m in x.msgs))
        return 2
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
