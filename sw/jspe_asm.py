#!/usr/bin/env python3
# Cleo assembler (ISA.md 9-10): multi-file, two-pass with label iteration, .hex/.bin/.lst, static arming bound.
import os, re, sys, struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isa_def import ISA

EN = ISA["enums"]
FORMATS = ISA["formats"]
INSTR = {i["name"]: i for i in ISA["instructions"]}
BY_FMT = {}
for _i in ISA["instructions"]:
    BY_FMT.setdefault(_i["format"], []).append(_i)
UNDEF = ISA.get("undefined", [])
CREG = {c["name"].upper(): c for c in ISA["cregs"]}
HOST = ISA["host"]
REGS = {k.upper(): v for k, v in EN["reg"].items()}
REG2 = {k.upper(): v for k, v in EN["reg2"].items()}
GUARDS = {"ALWAYS": 0, "TICK": 1, "TOCK": 2, "HRX": 3, "HTX": 4, "C0": 5, "C1": 6, "NEVER": 7}
COND = {"jz": "jz", "jnz": "jnz", "jc": "jc", "jnc": "jnc", "jb": "jb", "jnb": "jnb", "jpar": "jpar", "jnpar": "jnpar",
        "jrun": "jrun", "jnrun": "jnrun", "jw": "jw", "jnw": "jnw", "jrx": "jrx", "jnrx": "jnrx", "jtx": "jtx",
        "jntx": "jntx", "jc0": "jc0", "jnc0": "jnc0", "jc1": "jc1", "jnc1": "jnc1", "jovr": "jovr", "jnovr": "jnovr",
        "jmis": "jmis", "jnmis": "jnmis"}
ALU_RX = ["add", "adc", "sub", "cmp", "and", "or", "xor", "tst", "shl", "shr", "rlc", "rrc"]
GM0 = {"tsync": 0, "tstop": 1, "smp": 2, "wrst": 3, "ovclr": 4, "ret": 5, "halt": 6}
ACT_A = {"0": 1, "1": 2, "z": 3, "d": 4, "out": 5, "q": 6, "f": 7}
MEMSZ = ISA["mem_words"]


class AsmError(Exception):
    pass


class Undef(Exception):
    pass


def fmt_of(w):
    for f, d in FORMATS.items():
        if w & d["mask"] == d["value"]:
            return f


_dec = {}


def decode(w):
    r = _dec.get(w)
    if r is None:
        r = (None, None)
        if not any(w & u["mask"] == u["value"] for u in UNDEF):
            for i in BY_FMT[fmt_of(w)]:
                if w & i["mask"] == i["value"]:
                    fl = {k: (w >> lo) & ((1 << (hi - lo + 1)) - 1) for k, (hi, lo) in i.get("fields", {}).items()}
                    r = (i, fl)
                    break
        _dec[w] = r
    return r


def encode(name, **f):
    i = INSTR[name]
    w = i["value"]
    for k, (hi, lo) in i.get("fields", {}).items():
        v = f.get(k, 0)
        if v < 0 or v >= 1 << (hi - lo + 1):
            raise AsmError("%s: field %s = %d out of range" % (name, k, v))
        w |= v << lo
    return w


def is_guarded(w):
    i, f = decode(w)
    return i is not None and FORMATS[i["format"]]["guarded"] and f["g"] != 0


def sext7(v):
    return v - 128 if v & 64 else v


def jump_target(addr, w):
    i, f = decode(w)
    if i is None:
        return None
    if i["format"] in ("JF", "JP"):
        return (addr + 1 + sext7(f["rel"])) & 511
    if i["name"] in ("jmpa", "call"):
        return f["addr"]
    return None


# ---------------- expressions ----------------
TOK = re.compile(r"\s*(?:(0[xX][0-9a-fA-F_]+|0[bB][01_]+|\d+)|'(\\.|[^'])'|([A-Za-z_][A-Za-z0-9_]*)|"
                 r"(<<|>>|<=|>=|==|!=|[-+*/%&|^~()<>$]))")
BINOPS = [("|",), ("^",), ("&",), ("==", "!="), ("<", "<=", ">", ">="), ("<<", ">>"), ("+", "-"), ("*", "/", "%")]


def tokenize(s):
    pos, out = 0, []
    s = s.rstrip()
    while pos < len(s):
        m = TOK.match(s, pos)
        if not m or m.end() == pos:
            raise AsmError("bad expression near '%s'" % s[pos:])
        pos = m.end()
        num, ch, name, op = m.groups()
        if num is not None:
            n = num.replace("_", "")
            out.append(("n", int(n, 0) if n[:2].lower() in ("0x", "0b") else int(n)))
        elif ch is not None:
            out.append(("n", ord(ch.encode().decode("unicode_escape"))))
        elif name is not None:
            out.append(("s", name))
        else:
            out.append(("o", op))
    return out


def evaluate(s, lookup, here):
    toks = tokenize(s)
    p = [0]

    def peek():
        return toks[p[0]] if p[0] < len(toks) else (None, None)

    def take():
        t = peek()
        p[0] += 1
        return t

    def tdiv(a, b):
        if b == 0:
            raise AsmError("division by zero")
        q = abs(a) // abs(b)
        return q if (a >= 0) == (b >= 0) else -q

    def binary(level):
        if level == len(BINOPS):
            return unary()
        v = binary(level + 1)
        while peek()[0] == "o" and peek()[1] in BINOPS[level]:
            op = take()[1]
            r = binary(level + 1)
            v = {"|": lambda: v | r, "^": lambda: v ^ r, "&": lambda: v & r, "==": lambda: int(v == r),
                 "!=": lambda: int(v != r), "<": lambda: int(v < r), "<=": lambda: int(v <= r),
                 ">": lambda: int(v > r), ">=": lambda: int(v >= r), "<<": lambda: v << r, ">>": lambda: v >> r,
                 "+": lambda: v + r, "-": lambda: v - r, "*": lambda: v * r, "/": lambda: tdiv(v, r),
                 "%": lambda: v - r * tdiv(v, r)}[op]()
        return v

    def unary():
        t = peek()
        if t == ("o", "-"):
            take()
            return -unary()
        if t == ("o", "+"):
            take()
            return unary()
        if t == ("o", "~"):
            take()
            return ~unary()
        return primary()

    def primary():
        k, v = take()
        if k == "n":
            return v
        if k == "o" and v == "$":
            return here
        if k == "o" and v == "(":
            r = binary(0)
            if take() != ("o", ")"):
                raise AsmError("missing ')'")
            return r
        if k == "s":
            if v.lower() in ("lo", "hi") and peek() == ("o", "("):
                take()
                r = binary(0)
                if take() != ("o", ")"):
                    raise AsmError("missing ')'")
                return r & 0xFF if v.lower() == "lo" else (r >> 8) & 0xFF
            return lookup(v)
        raise AsmError("bad expression '%s'" % s.strip())

    r = binary(0)
    if p[0] != len(toks):
        raise AsmError("junk in expression '%s'" % s.strip())
    return r


def strip_comment(line):
    q = None
    for k, ch in enumerate(line):
        if q:
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif ch == ";":
            return line[:k]
    return line


def split_args(s):
    out, depth, cur, q = [], 0, "", None
    for ch in s:
        if q:
            cur += ch
            if ch == q:
                q = None
            continue
        if ch in "'\"":
            q = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
            continue
        cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


# ---------------- image ----------------
class Image:
    def __init__(self):
        self.words, self.src, self.owner = {}, {}, {}
        self.engines = {}
        self.slotmap = None
        self.filt = 0
        self.clk = None          # CLK_HZ of the sources (HOSTIF 7 CLK record)
        self.labels = {}
        self.deadlines = []
        self.guarded = []        # (engine, G descriptor) for every guarded instruction / loop
        self.bounds = {}
        self.assume = {}         # G start address -> m: G (a loop: its last pass) fires >= m clk after it is armed

    def eng(self, e):
        return self.engines.setdefault(e, {"init": [], "entry": None, "pads": {}})

    def slots(self):
        return 0xE4 if self.slotmap is None else self.slotmap

    def label(self, name, file=None):
        for (f, n), a in self.labels.items():
            if n == name and (file is None or os.path.basename(f) == os.path.basename(file)):
                return a
        raise KeyError(name)

    def hex_text(self, mem=MEMSZ):
        return "".join("%04x\n" % self.words.get(a, 0) for a in range(mem))

    def records(self):
        recs = []
        mask = 0
        for e in sorted(self.engines):
            mask |= 1 << e
        if self.clk is not None:
            recs.append((6, struct.pack("<I", self.clk)))
        recs.append((5, struct.pack("<B", mask)))
        if self.slotmap is not None:
            recs.append((2, struct.pack("<BH", HOST["regs"]["SLOTMAP"]["addr"], self.slotmap)))
        addrs = sorted(self.words)
        k = 0
        while k < len(addrs):
            s = k
            while k + 1 < len(addrs) and addrs[k + 1] == addrs[k] + 1:
                k += 1
            run = [self.words[a] for a in addrs[s:k + 1]]
            recs.append((1, struct.pack("<HH", addrs[s], len(run)) + b"".join(struct.pack("<H", w) for w in run)))
            k += 1
        recs.append((2, struct.pack("<BH", HOST["regs"]["FILT"]["addr"], self.filt)))
        pads = [(p, c) for e in sorted(self.engines) for p, c in sorted(self.engines[e]["pads"].items())]
        for p, c in pads:
            recs.append((2, struct.pack("<BH", pad_reg(p), c)))
        for e in sorted(self.engines):
            for w in self.exec_words(e):
                recs.append((3, struct.pack("<BH", e, w)))
        for p, c in pads:
            recs.append((2, struct.pack("<BH", pad_reg(p), c | 0x40)))
        recs.append((4, struct.pack("<B", mask)))
        return recs

    def exec_words(self, e):
        d = self.engines[e]
        ws = [w for w, _, _ in d["init"]]
        if d["entry"] is not None:
            ws.append(encode("jmpa", addr=d["entry"]))
        return ws

    def bin_bytes(self):
        recs = self.records()
        out = b"JSPE" + struct.pack("<BBH", 1, 0, len(recs))
        for t, payload in recs:
            out += struct.pack("<B", t) + payload
        return out

    def lst_text(self):
        out = []
        for a in sorted(self.words):
            f, ln, txt = self.src[a]
            b = self.bounds.get(a)
            bs = "" if b is None else "  ; next guard armed <= %s clk" % b
            out.append("%03x  %04x  %-48s %s:%d%s\n" % (a, self.words[a], txt.strip()[:48], os.path.basename(f), ln, bs))
        for e in sorted(self.engines):
            for w, f, ln in self.engines[e]["init"]:
                out.append("e%d init %04x  %s:%d\n" % (e, w, os.path.basename(f), ln))
        for d in self.deadlines:
            out.append("deadline %s:%d  n=%d bound=%s path=%s\n" % (os.path.basename(d["file"]), d["line"], d["n"],
                                                                   d["bound"], d["label"] or "straight"))
        for a in sorted(self.assume):
            out.append("assume %03x armed >= %d clk before it fires\n" % (a, self.assume[a]))
        out.append("prefetch credited only where `.assume armed` states the wait (ISA.md 10)\n")
        return "".join(out)


def pad_reg(p):
    return (HOST["regs"]["PAD_UO"]["addr"] + p) if p < 8 else (HOST["regs"]["PAD_UIO"]["addr"] + p - 8)


def pad_index(name):
    m = re.fullmatch(r"(uo|uio|ui)([0-7])", name.strip().lower())
    if not m:
        raise AsmError("bad pin '%s'" % name)
    return m.group(1), int(m.group(2))


# ---------------- assembler core ----------------
class FileCtx:
    def __init__(self, path, text, defines):
        self.path, self.lines = path, text.splitlines()
        self.defines = defines
        self.equ = {}


class Assembler:
    def __init__(self, sources, defines=None, mem=MEMSZ):
        self.sources = sources            # list of (path, text)
        self.defines = defines or {}      # {"NAME": expr} or {"file.s:NAME": expr}
        self.mem = mem
        self.check = True                 # False: report .deadline bounds without failing (measurement)

    def run(self):
        prev = None
        for _ in range(12):
            self.strict = False
            labels = self.one_pass()
            if labels == prev:
                break
            prev = labels
        self.strict = True
        self.used = set()
        self.one_pass()
        unused = sorted(set(self.defines) - self.used)
        if unused:
            raise AsmError("-D %s matches no .equ" % ", ".join(unused))
        self.analyse()
        return self.img

    # one pass over all files
    def one_pass(self):
        self.img = Image()
        self.all_labels = getattr(self, "all_labels", {})
        new_labels = {}
        slots_seen = False
        for path, text in self.sources:
            fc = FileCtx(path, text, self.defines)
            self.fc, self.lc, self.engine, self.section = fc, 0, 0, "code"
            self.last_g = self.last_x = None
            conds = []
            loop = None
            for ln, raw in enumerate(fc.lines, 1):
                self.ln = ln
                line = strip_comment(raw)
                try:
                    if loop is not None:
                        body = line
                        if "}" in body:
                            body, rest = body.split("}", 1)
                            if rest.strip():
                                raise AsmError("text after '}'")
                            if body.strip():
                                loop["items"].append((ln, body.strip()))
                            if all(c[0] for c in conds):
                                self.emit_loop(loop)
                            loop = None
                        elif body.strip():
                            loop["items"].append((ln, body.strip()))
                        continue
                    label, body = self.split_label(line)
                    low = body.lower()
                    d = low.split(None, 1)[0] if low else ""
                    if d in (".if", ".else", ".endif"):
                        if d == ".if":
                            par = all(c[0] for c in conds)
                            v = bool(self.ev(body.split(None, 1)[1])) if par else False
                            conds.append([v, par, v])
                        elif d == ".else":
                            if not conds:
                                raise AsmError(".else without .if")
                            conds[-1][0] = conds[-1][1] and not conds[-1][2]
                        else:
                            if not conds:
                                raise AsmError(".endif without .if")
                            conds.pop()
                        continue
                    if not all(c[0] for c in conds):
                        continue
                    if label:
                        if self.section != "code":
                            raise AsmError("label inside .init")
                        key = (path, label)
                        if key in new_labels:
                            raise AsmError("label '%s' defined twice" % label)
                        new_labels[key] = self.lc
                        self.img.labels[key] = self.lc
                    if not body:
                        continue
                    if re.match(r"loop\b", low):
                        rest = body[4:].strip()
                        if not rest.startswith("{"):
                            raise AsmError("loop needs '{'")
                        rest = rest[1:]
                        loop = {"line": ln, "items": [], "text": raw}
                        if "}" in rest:
                            inner, after = rest.split("}", 1)
                            if after.strip():
                                raise AsmError("text after '}'")
                            if inner.strip():
                                loop["items"].append((ln, inner.strip()))
                            self.emit_loop(loop)
                            loop = None
                        elif rest.strip():
                            loop["items"].append((ln, rest.strip()))
                        continue
                    if body.startswith("."):
                        if d == ".slots":
                            if slots_seen:
                                raise AsmError("second .slots line")
                            slots_seen = True
                        self.directive(d, body[len(d):].strip(), raw)
                    else:
                        self.instruction(body, raw)
                except AsmError as e:
                    raise AsmError("%s:%d: %s" % (os.path.basename(path), ln, e))
            if conds:
                raise AsmError("%s: missing .endif" % os.path.basename(path))
            if loop is not None:
                raise AsmError("%s:%d: missing '}'" % (os.path.basename(path), loop["line"]))
            if "CLK_HZ" in fc.equ:
                if self.img.clk not in (None, fc.equ["CLK_HZ"]):
                    raise AsmError("%s: CLK_HZ %d differs from %d in an earlier file"
                                   % (os.path.basename(path), fc.equ["CLK_HZ"], self.img.clk))
                self.img.clk = fc.equ["CLK_HZ"]
        self.all_labels = new_labels
        return new_labels

    def split_label(self, line):
        s = line.strip()
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*:(.*)$", s)
        if m and not s.startswith("@") and m.group(1).lower() not in ("loop",):
            return m.group(1), m.group(2).strip()
        return None, s

    # symbols
    def lookup(self, name):
        fc = self.fc
        if name in fc.equ:
            return fc.equ[name]
        key = (fc.path, name)
        if key in self.all_labels:
            return self.all_labels[key]
        if key in self.img.labels:
            return self.img.labels[key]
        if self.strict:
            raise AsmError("undefined symbol '%s'" % name)
        raise Undef(name)

    def ev(self, s, here=None):
        try:
            return evaluate(s, self.lookup, self.lc if here is None else here)
        except Undef:
            return 0

    def override(self, name):
        base = os.path.basename(self.fc.path)
        for k in (base + ":" + name, name):
            if k in self.defines:
                if hasattr(self, "used"):
                    self.used.add(k)
                return str(self.defines[k])
        return None

    # directives
    def directive(self, d, args, raw):
        a = split_args(args)
        img = self.img
        if d == ".equ":
            if len(a) != 2:
                raise AsmError(".equ NAME, expr")
            name = a[0]
            expr = self.override(name) or a[1]
            if name in self.fc.equ:
                raise AsmError("'%s' defined twice" % name)
            self.fc.equ[name] = self.ev(expr)
        elif d == ".org":
            v = self.ev(a[0])
            if not 0 <= v < 512:
                raise AsmError(".org out of range")
            self.lc = v
        elif d == ".word":
            for x in a:
                self.store(self.ev(x) & 0xFFFF, raw)
        elif d == ".space":
            for _ in range(self.ev(a[0])):
                self.store(0, raw)
        elif d == ".engine":
            v = self.ev(a[0])
            if not 0 <= v <= 3:
                raise AsmError("engine 0-3")
            self.engine = v
            img.eng(v)
        elif d == ".init":
            self.section = "init"
            img.eng(self.engine)
        elif d == ".code":
            self.section = "code"
        elif d == ".entry":
            img.eng(self.engine)["entry"] = self.ev(a[0]) & 511
        elif d == ".pad":
            if len(a) < 2:
                raise AsmError(".pad PIN, [eN.]pK {, od} {, inv}")
            kind, n = pad_index(a[0])
            if kind == "ui":
                raise AsmError("ui pads are inputs")
            idx = n if kind == "uo" else 8 + n
            if kind == "uo" and n in (4, 5):
                raise AsmError("uo4/uo5 are the host link")
            m = re.fullmatch(r"(?:e([0-3])\.)?p([0-3])", a[1].strip().lower())
            if not m:
                raise AsmError("bad logical pin '%s'" % a[1])
            eng = int(m.group(1)) if m.group(1) else self.engine
            cfg = int(m.group(2)) | eng << 2
            for opt in a[2:]:
                o = opt.strip().lower()
                if o == "od":
                    cfg |= 0x20
                elif o == "inv":
                    cfg |= 0x10
                else:
                    raise AsmError("bad pad option '%s'" % opt)
            for e2, ed in img.engines.items():
                if idx in ed["pads"]:
                    raise AsmError("pad %s has two owners" % a[0])
            img.eng(eng)["pads"][idx] = cfg
        elif d == ".slots":
            if len(a) != 4:
                raise AsmError(".slots a, b, c, d")
            v = 0
            for s, x in enumerate(a):
                e = self.ev(x)
                if not 0 <= e <= 3:
                    raise AsmError("slot owner 0-3")
                v |= e << (2 * s)
            img.slotmap = v
        elif d == ".filter":
            for x in a:
                kind, n = pad_index(x)
                if kind == "uo":
                    raise AsmError("filter on an output")
                img.filt |= 1 << (n if kind == "ui" else 8 + n)
        elif d == ".assert":
            if len(a) < 1:
                raise AsmError(".assert expr, \"text\"")
            if self.strict and not self.ev(a[0]):
                raise AsmError("assertion failed: %s" % (a[1].strip('"') if len(a) > 1 else a[0]))
        elif d == ".assume":
            if self.last_g is None:
                raise AsmError(".assume must follow a guarded instruction or a loop")
            m = re.match(r"armed\s+(.+)$", ",".join(a).strip())
            if not m:
                raise AsmError(".assume armed m")
            v = self.ev(m.group(1))
            if v < 0:
                raise AsmError(".assume armed: m < 0")
            img.assume[self.last_g[1]] = v
        elif d == ".deadline":
            g = self.last_g or getattr(self, "last_x", None)
            if g is None:
                raise AsmError(".deadline must follow an instruction or a loop")
            n = self.ev(a[0])
            lab = a[1].strip() if len(a) > 1 else None
            img.deadlines.append({"file": self.fc.path, "line": self.ln, "n": n, "label": lab, "engine": self.engine,
                                  "g": g, "label_addr": self.ev(lab) & 511 if lab else None,
                                  "bound": None, "target": None, "ungd": self.last_g is None})
        else:
            raise AsmError("unknown directive %s" % d)

    def store(self, w, raw):
        img = self.img
        if self.section != "code":
            raise AsmError("data inside .init")
        a = self.lc
        if a >= 512:
            raise AsmError("address past 511")
        if a >= self.mem:
            raise AsmError("address %d >= --mem %d" % (a, self.mem))
        if a in img.words:
            f, ln, _ = img.src[a]
            raise AsmError("address %d already used by %s:%d" % (a, os.path.basename(f), ln))
        img.words[a] = w
        img.src[a] = (self.fc.path, self.ln, raw)
        img.owner[a] = self.engine
        self.lc += 1

    def emit(self, w, raw):
        if self.section == "init":
            self.img.eng(self.engine)["init"].append((w, self.fc.path, self.ln))
        else:
            self.store(w, raw)

    # instructions
    def instruction(self, body, raw):
        g, op = self.parse_guard(body)
        words, kind = self.encode_op(op, g)
        if self.section == "init" and kind in ("rel", "call", "loop"):
            raise AsmError("relative jump, call or loop inside .init")
        start = self.lc
        for w in words:
            self.emit(w, raw)
        if self.section == "code":
            self.last_g = ("one", start) if g else None
            self.last_x = None if g else ("one", self.lc - 1)   # unguarded: a .deadline may follow it (wait 0)

    def emit_loop(self, loop):
        if self.section == "init":
            raise AsmError("loop inside .init")
        items = loop["items"]
        if len(items) not in (1, 2):
            raise AsmError("loop holds one or two instructions")
        start = self.lc
        rr = [EN["loop"]["rep"]] if len(items) == 1 else [EN["loop"]["head"], EN["loop"]["tail"]]
        for (ln, txt), r in zip(items, rr):
            self.ln = ln
            g, op = self.parse_guard(txt)
            w = self.encode_e(op, g)
            if w is None:
                raise AsmError("only pin/shift instructions inside a loop")
            self.store(w | (r << 4), loop["text"] if len(items) == 1 else txt)
        self.last_g = ("loop", start, self.lc - 1)
        self.last_x = None

    def parse_guard(self, s):
        m = re.match(r"@\s*(rise|fall)\s+p([0-3])\s*:\s*(.*)$", s, re.I)
        if m:
            base = 8 if m.group(1).lower() == "rise" else 12
            return base + int(m.group(2)), m.group(3).strip()
        m = re.match(r"@\s*([A-Za-z0-9]+)\s*:\s*(.*)$", s)
        if m:
            g = GUARDS.get(m.group(1).upper())
            if g is None:
                raise AsmError("unknown guard '@%s'" % m.group(1))
            return g, m.group(2).strip()
        if s.startswith("@"):
            raise AsmError("bad guard syntax")
        return 0, s

    def reg(self, s, two=False):
        r = (REG2 if two else REGS).get(s.strip().upper())
        if r is None:
            raise AsmError("bad register '%s'%s" % (s, " (R0-R2, S only)" if two else ""))
        return r

    def imm(self, s):
        s = s.strip()
        if not s.startswith("#"):
            raise AsmError("immediate needs '#'")
        return self.ev(s[1:])

    def rel(self, target):
        r = target - (self.lc + 1)
        if not -64 <= r <= 63:
            if self.strict:
                raise AsmError("jump target out of range (%d)" % r)
            r = 0
        return r & 127

    def encode_e(self, op, g):
        low = op.lower().strip()
        f = dict(pa=0, aa=0, pb=0, ab=0, s=0, rr=0, g=g)
        if low == "nop":
            return encode("pin", **f)
        items = [x.strip() for x in split_args(op)]
        acts = []
        for it in items:
            if it.lower() == "shin":
                if f["s"]:
                    raise AsmError("shin twice")
                f["s"] = 1
                continue
            m = re.fullmatch(r"p([0-3])\s*<-\s*(0|1|z|d|out|q|f|t)", it, re.I)
            if not m:
                return None
            acts.append((int(m.group(1)), m.group(2).lower()))
        if not acts and not f["s"]:
            return None
        # assign actions to slot A (any pin, any action) and B (p0/p1: 0, 1, t)
        best = None
        for mask in range(1 << len(acts)):
            A = [a for k, a in enumerate(acts) if not mask >> k & 1]
            B = [a for k, a in enumerate(acts) if mask >> k & 1]
            if len(A) > 1 or len(B) > 1:
                continue
            if B and (B[0][0] > 1 or B[0][1] not in ("0", "1", "t")):
                continue
            if A and A[0][1] == "t":
                continue
            if A and B and A[0][0] == B[0][0]:
                raise AsmError("two actions on p%d" % A[0][0])
            best = (A, B)
            break
        if best is None:
            raise AsmError("unencodable action combination")
        A, B = best
        if A:
            f["pa"], f["aa"] = A[0][0], ACT_A[A[0][1]]
            if A[0][1] == "out" and f["s"]:
                raise AsmError("'out' together with shin")
        if B:
            f["pb"], f["ab"] = B[0][0], {"0": 1, "1": 2, "t": 3}[B[0][1]]
        return encode("pin", **f)

    def encode_op(self, op, g):
        m = re.match(r"([A-Za-z][A-Za-z0-9]*)\s*(.*)$", op)
        if not m:
            raise AsmError("syntax error")
        mn, rest = m.group(1).lower(), m.group(2).strip()
        a = split_args(rest)

        def noguard():
            if g:
                raise AsmError("a guard on %s is not allowed" % mn)

        def nargs(n):
            if len(a) != n:
                raise AsmError("%s takes %d operand(s)" % (mn, n))

        w = self.encode_e(op, g)
        if w is not None:
            return [w], "e"
        if mn in ("jhi", "jlo"):
            nargs(2)
            pm = re.fullmatch(r"p([0-3])", a[0].strip().lower())
            if not pm:
                raise AsmError("bad pin")
            return [encode(mn, pin=int(pm.group(1)), rel=self.rel(self.ev(a[1])), g=g)], "rel"
        if mn == "jmp" or mn == "ljmp":
            noguard()
            nargs(1)
            t = self.ev(a[0]) & 511
            r = t - (self.lc + 1)
            if self.section == "init" or mn == "ljmp" or not -64 <= r <= 63:
                return [encode("jmpa", addr=t)], "abs"
            return [encode("jmp", rel=r & 127)], "rel"
        if mn in COND:
            noguard()
            nargs(1)
            return [encode(mn, rel=self.rel(self.ev(a[0])))], "rel"
        if mn == "call":
            noguard()
            nargs(1)
            return [encode("call", addr=self.ev(a[0]) & 511)], "call"
        if mn == "mov":
            nargs(2)
            return [encode("mov", rd=self.reg(a[0]), rs=self.reg(a[1]), g=g)], "gm"
        if mn in ("push", "pushh", "tset"):
            nargs(1)
            return [encode(mn, rs=self.reg(a[0]), g=g)], "gm"
        if mn in ("pull", "pullh"):
            nargs(1)
            return [encode(mn, rd=self.reg(a[0]), g=g)], "gm"
        if mn in GM0:
            nargs(0)
            return [encode(mn, g=g)], "gm"
        if mn in ("sset", "sclr"):
            nargs(1)
            return [encode(mn, n=self.chk(self.imm(a[0]), 0, 7), g=g)], "gm"
        if mn == "watch":
            nargs(1)
            return [encode(mn, m=self.chk(self.imm(a[0]), 0, 3), g=g)], "gm"
        if mn == "evt":
            nargs(1)
            return [encode(mn, c=self.chk(self.imm(a[0]), 0, 63), g=g)], "gm"
        if mn in ("li", "lih", "add", "sub", "cmp") and len(a) == 2 and a[1].strip().startswith("#"):
            noguard()
            rd = self.reg(a[0], two=True)
            v = self.imm(a[1])
            if mn == "li":
                v16 = self.chk(v, -32768, 65535) & 0xFFFF
                if v16 > 255:
                    return [encode("li", rd=rd, imm=v16 & 0xFF), encode("lih", rd=rd, imm=v16 >> 8)], "im"
                return [encode("li", rd=rd, imm=v16)], "im"
            if mn == "lih":
                return [encode("lih", rd=rd, imm=self.chk(v, 0, 255))], "im"
            if mn in ("add", "sub"):
                v = v if mn == "add" else -v
                return [encode("addi", rd=rd, imm=self.chk(v, -128, 127) & 0xFF)], "im"
            return [encode("cmpi", rd=rd, imm=self.chk(v, 0, 255))], "im"
        if mn in ALU_RX:
            noguard()
            nargs(2)
            return [encode(mn, rd=self.reg(a[0]), rs=self.reg(a[1]))], "rx"
        if mn in ("ld", "st"):
            noguard()
            nargs(2)
            mm = re.fullmatch(r"\[\s*([A-Za-z0-9]+)\s*(?:\+\s*(1|S|s))?\s*\]\s*(!?)", a[1].strip())
            if not mm:
                raise AsmError("bad memory operand '%s'" % a[1])
            rb = self.reg(mm.group(1), two=True)
            off, wb = mm.group(2), mm.group(3) == "!"
            if off is None:
                if wb:
                    raise AsmError("write-back needs +1 or +S")
                mode = 0
            elif off == "1":
                if not wb:
                    raise AsmError("[rb+1] needs '!'")
                mode = 1
            else:
                mode = 3 if wb else 2
            r = self.reg(a[0])
            return [encode(mn, rb=rb, mode=mode, **({"rd": r} if mn == "ld" else {"rs": r}))], "mem"
        if mn in ("wcfg", "rcfg"):
            noguard()
            nargs(2)
            cname, rname = (a[0], a[1]) if mn == "wcfg" else (a[1], a[0])
            c = CREG.get(cname.strip().upper())
            idx = c["index"] if c else self.chk(self.ev(cname), 0, 31)
            if mn == "wcfg" and c is not None and c.get("writable", True) is False:
                raise AsmError("wcfg %s is undefined (read-only register)" % c["name"])
            if mn == "wcfg":
                return [encode("wcfg", creg=idx, rs=self.reg(rname))], "rx"
            return [encode("rcfg", creg=idx, rd=self.reg(rname))], "rx"
        raise AsmError("unknown instruction '%s'" % mn)

    def chk(self, v, lo, hi):
        if not lo <= v <= hi:
            raise AsmError("value %d outside %d..%d" % (v, lo, hi))
        return v

    # static arming bound (ISA.md 10)
    def analyse(self):
        img = self.img
        sm = img.slots()
        for e, ed in img.engines.items():
            ed["gap"] = slot_gap(sm, e)
            ed["chain"] = slot_chain(sm, e)
        seen = set()
        for a in sorted(img.words):
            w = img.words[a]
            i, f = decode(w)
            if i is None or a in seen:
                continue
            if i["format"] == "E" and f["rr"] in (1, 2):
                end = a + (0 if f["rr"] == 1 else 1)
                seen.update(range(a, end + 1))
                g = ("loop", a, end)
            elif is_guarded(w) and not (i["format"] == "E" and f["rr"]):
                g = ("one", a)
            else:
                continue
            ed = img.engines.get(img.owner[a], {})
            b, _, _ = path_bound(img, g, None, ed.get("gap") or 4, ed.get("chain") or 4, img.assume.get(g[1], 0))
            img.bounds[g[1]] = b if b is not None else "?"
        for d in img.deadlines:
            ed = img.engines[d["engine"]]
            b, t, seq = path_bound(img, d["g"], d["label_addr"], ed.get("gap") or 4, ed.get("chain") or 4,
                                   0 if d["ungd"] else img.assume.get(d["g"][1], 0))
            d["bound"], d["target"], d["path"] = b, t, seq
            if b is None:
                raise AsmError("%s:%d: .deadline path cannot be bounded" % (os.path.basename(d["file"]), d["line"]))
            if b > d["n"] and self.check:
                pf, _, _ = path_bound(img, d["g"], d["label_addr"], ed.get("gap") or 4, ed.get("chain") or 4, 1 << 16)
                hint = "" if d["ungd"] or pf > d["n"] else \
                    " (%d if the guard waits long enough: state its minimum wait with .assume armed, ISA.md 10)" % pf
                raise AsmError("%s:%d: deadline %d exceeded: bound %d clk%s" % (os.path.basename(d["file"]), d["line"],
                                                                                d["n"], b, hint))


def slot_gap(slotmap, e):
    own = [s for s in range(4) if (slotmap >> (2 * s)) & 3 == e]
    if not own:
        return None
    return max(((own[(k + 1) % len(own)] - own[k]) % 4) or 4 for k in range(len(own)))


def slot_chain(slotmap, e):
    # chained access: requested 2 clk after a grant in own slot s, waits for the first own slot >= s + 2 (D054)
    own = [s for s in range(4) if (slotmap >> (2 * s)) & 3 == e]
    if not own:
        return None
    return max(min(t for t in range(s + 2, s + 6) if t % 4 in own) - s for s in own)


def fetch_cost(j, gap, chain, x=0):
    # j accesses after G's successor; x = 1 or 2 more because the successor (and a jmp's target) was not prefetched
    if x == 0:
        return 1 if j == 0 else 1 + (gap + 1) + (j - 1) * chain
    return gap + 1 + (x - 1 + j) * chain


def _name(img, addr):
    w = img.words.get(addr & 511)
    i = decode(w)[0] if w is not None else None
    return i["name"] if i else None


def first_step(img, g_end, taken, wait, gap, chain):
    # (successor address, x): G's successor is in PF when G was armed >= gap clk before it fired; a jmp successor's
    # target is in PF after >= gap + 1 + chain clk; a taken guarded jump's target is never prefetched (ISA.md 10)
    w = img.words.get(g_end & 511)
    if taken:
        start, x = jump_target(g_end, w), 1
    else:
        start = (g_end + 1) & 511
        x = 0 if wait >= gap else 1
    if _name(img, start) in ("jmp", "jmpa"):
        if taken or wait < gap:
            x += 1
        elif wait < gap + 1 + chain:
            x = 1
        start = jump_target(start, img.words[start])
    return start, x


def walk_bound(img, g_end, decide, gap, chain, wait=0, taken=False, ret_addr=None):
    # bound of the path from G (last word g_end) to the next guarded instruction; decide(addr, target) -> taken for
    # each conditional jump on the path; ret_addr: where a `ret` with no `call` on the path returns (None: unbounded)
    # -> (bound, target address, executed addresses) or (None, None, None)
    addr, x = first_step(img, g_end, taken, wait, gap, chain)
    j, stack, seq = 0, [], []
    for _ in range(256):
        w = img.words.get(addr)
        if w is None:
            return None, None, None
        i, f = decode(w)
        if i is None:
            return None, None, None
        if is_guarded(w):
            return fetch_cost(j, gap, chain, x), addr, seq
        n = i["name"]
        seq.append(addr)
        if i["format"] == "E" and f["rr"]:
            return None, None, None
        if n in ("ld", "st"):
            j += 2
            addr = (addr + 1) & 511
        elif n in ("jmp", "jmpa"):
            j += 1
            addr = jump_target(addr, w)
        elif n == "call":
            stack.append((addr + 1) & 511)
            j += 1
            addr = f["addr"]
        elif n == "ret":
            if not stack and ret_addr is None:
                return None, None, None
            j += 1
            addr = stack.pop() if stack else ret_addr
            ret_addr = None if not stack else ret_addr
        elif n == "halt":
            return None, None, None
        elif i["format"] in ("JF", "JP"):
            t = jump_target(addr, w)
            j += 1
            addr = t if decide(addr, t) else (addr + 1) & 511
        else:
            j += 1
            addr = (addr + 1) & 511
    return None, None, None


def path_bound(img, g, label_addr, gap, chain, wait=0):
    # .deadline path: straight-line, or taking the first conditional jump to label_addr (earlier ones not taken)
    st = {"took": label_addr is None}

    def decide(a, t):
        if not st["took"] and t == label_addr:
            st["took"] = True
            return True
        return False
    return walk_bound(img, g[-1], decide, gap, chain, wait)


def assemble(paths, defines=None, mem=MEMSZ):
    srcs = [(p, open(p).read()) for p in paths]
    return Assembler(srcs, defines, mem).run()


def assemble_text(text, defines=None, mem=MEMSZ, name="inline.s"):
    return Assembler([(name, text)], defines, mem).run()


def main(argv):
    defines, mem, out, files = {}, MEMSZ, None, []
    k = 0
    while k < len(argv):
        x = argv[k]
        if x == "-D":
            k += 1
            x = "-D" + argv[k]
        if x.startswith("-D"):
            n, v = x[2:].split("=", 1)
            defines[n] = v
        elif x == "--mem":
            k += 1
            mem = int(argv[k], 0)
        elif x == "-o":
            k += 1
            out = argv[k]
        else:
            files.append(x)
        k += 1
    if not files:
        print("usage: jspe_asm.py [-D [file.s:]NAME=expr] [--mem n] [-o base] file.s {file.s}")
        return 2
    try:
        img = assemble(files, defines, mem)
    except AsmError as e:
        print("error:", e)
        return 1
    base = out or os.path.splitext(files[0])[0]
    with open(base + ".hex", "w") as f:
        f.write(img.hex_text())
    with open(base + ".bin", "wb") as f:
        f.write(img.bin_bytes())
    with open(base + ".lst", "w") as f:
        f.write(img.lst_text())
    print("%d words, %d engines, %d deadlines ok" % (len(img.words), len(img.engines), len(img.deadlines)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
