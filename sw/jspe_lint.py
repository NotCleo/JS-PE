# Read-before-write lint for the ISS: state the RTL does not reset (R0-R2, S, P, C, T, LFSR, POLY) plus L, tracked
# per bit from reset, .init included. A read of an unwritten bit is reported: mode "warn" (once per site) or "error".
import os
import warnings

REGS = ("R0", "R1", "R2", "S", "P", "C", "T", "L")
FULL = {r: 0xFFFF for r in REGS}
FULL["L"] = 0x1FF


class LintError(Exception):
    pass


def mode_from_env():
    m = os.environ.get("JSPE_LINT", "warn")
    return None if m == "off" else m


class Lint:
    def __init__(self, mode="warn"):
        self.mode = mode
        self.sites = set()
        self.reset()

    def reset(self):
        self.d = [dict.fromkeys(REGS, 0) for _ in range(4)]
        self.lf, self.poly = 0, 0

    def read(self, e, pc, r, bits=None):
        bits = FULL[r] if bits is None else bits & FULL[r]
        miss = bits & ~self.d[e][r]
        if not miss:
            return
        key = (e, pc, r)
        msg = "e%d pc %d reads %s bits 0x%x before any write" % (e, pc, r, miss)
        if self.mode == "error":
            raise LintError(msg)
        if key not in self.sites:
            self.sites.add(key)
            warnings.warn(msg, stacklevel=3)

    def write(self, e, r, bits=None):
        if r != "T" or bits is not None:
            self.d[e][r] |= FULL[r] if bits is None else bits

    # ---------------------------------------------------------------- one engine clock (called by Engine.step)
    def engine(self, e, o, ins, fl, fired, wcall, tstart_auto, reload, wl, lsb, alt, apull):
        if wcall:
            self.write(e, "L")
        if tstart_auto or reload:
            self.read(e, o.PC, "P")
            self.d[e]["T"] = 0xFFFF
        if not fired:
            return
        pc, name, fmt = o.PC, ins["name"], ins["format"]
        rn = REGS
        if fl.get("g") == 2 and fmt in ("E", "JP", "GM"):
            self.read(e, pc, "C")                                  # @tock compares T with C
        if fmt == "E":
            mask = (1 << (wl + 1)) - 1
            tapbit = 1 if lsb else 1 << wl

            def shift():
                d = self.d[e]["S"]
                d = (((d & mask) >> 1) | (1 << wl)) if lsb else (((d << 1) | 1) & mask)
                self.d[e]["S"] = d | (~mask & 0xFFFF)
                return 1
            stepped = 0
            if fl["s"]:
                stepped += shift()
            aa = fl["aa"]
            if aa in (4, 5, 7) or (aa == 6 and alt == 1):
                self.read(e, pc, "S", tapbit)
            if aa == 5 and not fl["s"]:
                stepped += shift()
            if apull and stepped:
                self.d[e]["S"] = 0xFFFF
            return
        if fmt in ("JP", "JF"):
            return
        if name in ("ld", "st"):
            self.read(e, pc, rn[fl["rb"]])
            if fl["mode"] in (2, 3):
                self.read(e, pc, "S")
            if name == "st":
                self.read(e, pc, rn[fl["rs"]])
            else:
                self.write(e, rn[fl["rd"]])
            if fl["mode"] in (1, 3):
                self.write(e, rn[fl["rb"]])
            return
        if name == "mov":
            self.read(e, pc, rn[fl["rs"]])
            self.write(e, rn[fl["rd"]])
        elif name in ("push", "pushh"):
            self.read(e, pc, rn[fl["rs"]], 0xFF if name == "push" else 0xFF00)
        elif name == "pull":
            self.write(e, rn[fl["rd"]])
        elif name == "pullh":
            self.write(e, rn[fl["rd"]], 0xFF00)
        elif name == "tsync":
            self.read(e, pc, "P")
            self.d[e]["T"] = 0xFFFF
        elif name == "tset":
            self.read(e, pc, rn[fl["rs"]])
            self.d[e]["T"] = 0xFFFF
        elif name == "ret":
            self.read(e, pc, "L")
        elif name == "call":
            self.write(e, "L")
        elif name == "li":
            self.write(e, rn[fl["rd"]])
        elif name == "lih":
            self.write(e, rn[fl["rd"]], 0xFF00)
        elif name in ("addi", "cmpi"):
            self.read(e, pc, rn[fl["rd"]])
            if name == "addi":
                self.write(e, rn[fl["rd"]])
        elif name == "wcfg":
            self.read(e, pc, rn[fl["rs"]])                         # LFSR / POLY writes: Chip.lfsr_commit
        elif name == "rcfg":
            c = fl["creg"]
            if c in (16, 17):
                miss = (0xFFFF << (16 * (c - 16))) & ~self.lf
                if miss:
                    self.read_lfsr(e, pc, miss)
            self.write(e, rn[fl["rd"]])
        elif name in ("add", "adc", "sub", "cmp", "and", "or", "xor", "tst", "shl", "shr", "rlc", "rrc"):
            if name not in ("shl", "shr", "rlc", "rrc"):
                self.read(e, pc, rn[fl["rd"]])
            self.read(e, pc, rn[fl["rs"]])
            if name not in ("cmp", "tst") and rn[fl["rd"]] != "T":
                self.write(e, rn[fl["rd"]])

    def read_lfsr(self, e, pc, miss):
        msg = "e%d pc %d reads LFSR bits 0x%x before any write" % (e, pc, miss)
        if self.mode == "error":
            raise LintError(msg)
        if (e, pc, "LFSR") not in self.sites:
            self.sites.add((e, pc, "LFSR"))
            warnings.warn(msg, stacklevel=3)

    # ---------------------------------------------------------------- shared LFSR: one step (Chip.lfsr_commit)
    def lf_step(self, right, poly_val):
        d = self.lf
        if right:
            fb_ok = d & 1
            d = (d >> 1) | 0x80000000
        else:
            fb_ok = d >> 31 & 1
            d = ((d << 1) | 1) & 0xFFFFFFFF
        if not fb_ok:
            d &= ~poly_val & 0xFFFFFFFF
        self.lf = d & (self.poly | ~poly_val) & 0xFFFFFFFF
