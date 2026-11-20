#!/usr/bin/env python3
# JS-PE cycle-accurate ISS (ISA.md, ARCH.md 4-9, HOSTIF.md). One step() = one clk.
import os, sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isa_def import ISA
from jspe_asm import decode, encode, sext7, FORMATS

HOST = ISA["host"]
ESC = HOST["esc"]
ID = HOST["regs"]["ID"]["reset"]
GEV = {1: 8, 2: 9}            # guard -> event index (rise p0-3: 0-3, fall p0-3: 4-7, tick 8, tock 9)
for _g in range(8, 16):
    GEV[_g] = _g - 8


class IssError(Exception):
    pass


class Fifo:
    def __init__(self, depth=4):
        self.q, self.depth = [], depth

    def empty(self):
        return not self.q

    def full(self):
        return len(self.q) >= self.depth

    def free(self):
        return self.depth - len(self.q)

    def head(self):
        return self.q[0] if self.q else 0

    def commit(self, wr, rd, flush):
        if flush:
            self.q = []
            return True
        e, f = self.empty(), self.full()
        if rd and not e:
            self.q.pop(0)
        if wr is not None:
            if f:
                return False
            self.q.append(wr)
        return True


def m16(v):
    return v & 0xFFFF


class Engine:
    def __init__(self, idx):
        self.idx = idx
        self.R = [0, 0, 0]
        self.S = self.P = self.C = self.T = 0
        self.trace = False
        self.fires, self.arms = [], []
        self.redirs = []        # watcher redirects (cycle), trace only
        self.late = 0
        self.reset()

    def reset(self):
        self.run = self.xm = self.stepm = 0
        self.IR = self.PC = self.HR = self.PF = self.L = 0
        self.FP = 1                          # FP = PC + 1 from reset (D109)
        self.irv = self.hrv = self.pfv = 0
        self.flight = None            # (kind, addr, data): read granted in the previous clock
        self.memop = None             # ld/st in progress
        self.wen = 0
        self.wprev = [0, 0]
        self.F, self.D = [0] * 10, [0] * 10
        self.ovr = self.Z = self.Cy = self.halted = self.trap = 0
        self.cfg = [7, 0, 0, 0, 0, 0, 0, 0, 0]
        self.N = self.B = self.PAR = self.RUN = self.LV = self.LD = 0
        self.oe, self.val, self.fol, self.prev, self.bsup = [0] * 4, [0] * 4, [0] * 4, [0] * 4, [0] * 4
        self.trun = self.tone = self.FA = self.PSC = self.skip = 0
        self.req = None
        self.waiting = 0

    def getreg(self, r):
        return (self.R[0], self.R[1], self.R[2], self.S, self.P, self.C, self.T, self.L)[r]

    # ------------------------------------------------------------------ one clock
    def step(self, env):
        o = self
        e = self.idx
        pul = env.pulses.get(e, {})
        if pul.get("rst"):
            keep = (self.R, self.S, self.P, self.C, self.T)
            self.reset()
            self.R, self.S, self.P, self.C, self.T = keep
            env.flush[e] = True
            return
        n = SimpleNamespace(**self.__dict__)
        n.F, n.D, n.oe, n.val, n.fol, n.R = o.F[:], o.D[:], o.oe[:], o.val[:], o.fol[:], o.R[:]
        n.cfg, n.wprev = o.cfg[:], o.wprev[:]
        n.bsup = [0] * 4
        n.flight = None
        cyc = env.cycle
        rx, tx = env.rx[e], env.tx[e]

        sh = o.cfg[0]
        wl, lsb, inpin, inv = sh & 15, (sh >> 4) & 1, (sh >> 5) & 3, (sh >> 7) & 1
        diff, apush, apull = (sh >> 8) & 1, (sh >> 9) & 1, (sh >> 10) & 1
        tp = o.cfg[1]
        alt, podd, lfen, rl, r1 = tp & 3, (tp >> 2) & 1, (tp >> 3) & 1, (tp >> 4) & 7, (tp >> 7) & 1
        tc = o.cfg[2]
        frac, pre, sedge, spin, swin = tc & 255, (tc >> 8) & 3, (tc >> 10) & 3, (tc >> 12) & 3, (tc >> 14) & 1

        # events of the clock
        lv, rise, fall = [0] * 4, [0] * 4, [0] * 4
        for k in range(4):
            reg = o.cfg[3 + (k >> 1)]
            s = (k & 1) * 8
            src, iv = (reg >> s) & 31, (reg >> (s + 5)) & 1
            if src < 16:
                b = (env.lvl >> src) & 1
            elif src < 24:
                b = (env.sig >> (src - 16)) & 1
            else:
                b = 1 if src == 25 else 0
            b ^= iv
            lv[k] = b
            if not o.bsup[k]:
                rise[k] = b & (1 - o.prev[k])
                fall[k] = (1 - b) & o.prev[k]
        stp = pre in (0, 3) or (pre == 1 and (o.PSC & 15) == 15) or (pre == 2 and o.PSC == 255)
        tick = int(bool(o.trun and stp and not o.skip and o.T == 0))
        tock = int(bool(o.trun and stp and not o.skip and o.T == o.C))
        live = rise + fall + [tick, tock]
        hrx, htx = int(not rx.empty()), int(not tx.full())
        mis = lv[inpin] ^ o.LD

        def ccsig(sel):
            if sel < 4:
                return lv[sel]
            if sel < 8:
                return rise[sel - 4]
            if sel < 12:
                return fall[sel - 8]
            return (tick, tock, mis, hrx)[sel - 12]

        cc, ccmode = [0, 0], [0, 0]
        for k in (0, 1):
            reg = o.cfg[5 + k]
            a, b = ccsig(reg & 15), ccsig((reg >> 4) & 15)
            cc[k] = (reg >> (8 + ((b << 1) | a))) & 1
            ccmode[k] = (reg >> 12) & 3

        active = o.run or o.xm
        arrive = o.flight
        if active and o.irv and o.IR is None:
            raise IssError("e%d armed an unwritten word at %d, clk %d" % (e, o.PC, cyc))

        wf = None
        if o.run and not o.xm:
            for k in (0, 1):
                if (o.wen >> k) & 1 and ccmode[k] in (1, 2) and cc[k] and not o.wprev[k]:
                    wf = k
                    break

        ins, fl = decode(o.IR) if o.irv else (None, None)
        g = fl.get("g", 0) if ins is not None and FORMATS[ins["format"]]["guarded"] else 0
        gev = GEV.get(g)
        if g == 0:
            gok = 1
        elif gev is not None:
            gok = live[gev] | o.F[gev]
        else:
            gok = {3: hrx, 4: htx, 5: cc[0], 6: cc[1], 7: 0}[g]

        trap = active and o.irv and ins is None and wf is None and o.memop is None
        fire = active and o.irv and ins is not None and gok and wf is None and o.memop is None
        n.waiting = int(bool(active and o.irv and not gok and o.memop is None and wf is None))

        # shared datapath context for the executing instruction
        ctx = SimpleNamespace(tx_push=None, rx_pop=False, tstart=None, tstop=False, acts=set(), lf=[], sset=0, sclr=0,
                              shifted=False)
        redirect = None
        complete = False
        advance = False
        loopctl = None
        timer_clear = False

        def ovr():
            n.ovr = 1

        # ---------- fire
        if trap:
            n.trap, n.run, n.stepm = 1, 0, 0
            if o.xm:
                n.xm, n.irv = 0, 0
        elif wf is not None:
            mode = ccmode[wf]
            redirect = o.cfg[7 + wf] & 511
            if self.trace:
                self.redirs.append(cyc)
            if mode == 2:
                n.L = o.PC & 511
            n.memop = None
        elif fire:
            name = ins["name"]
            if gev is not None and not live[gev] and o.F[gev]:
                n.late += 1
            fmt = ins["format"]
            if fmt == "E":
                loopctl = self.exec_e(o, n, fl, ctx, lv, hrx, htx, rx, wl, lsb, inpin, inv, diff, apush, apull,
                                      alt, podd, lfen, rl, r1)
                if o.xm or fl["rr"] == 0:
                    loopctl = None
                    complete = advance = True
                else:
                    complete = True
            elif fmt == "JP":
                complete = True
                if lv[fl["pin"]] == (1 if name == "jhi" else 0):
                    redirect = (o.PC + 1 + sext7(fl["rel"])) & 511
                else:
                    advance = True
            elif fmt == "JF":
                complete = True
                src, ivt = (o.IR >> 8) & 15, (o.IR >> 7) & 1
                c = (1, o.Z, o.Cy, o.B, o.PAR, int(rl != 0 and o.RUN == rl), int(o.N != 0), hrx, htx, cc[0], cc[1],
                     o.ovr, mis)[src] ^ ivt
                if c:
                    redirect = (o.PC + 1 + sext7(fl["rel"])) & 511
                else:
                    advance = True
            elif name in ("ld", "st"):
                rb = (o.R[0], o.R[1], o.R[2], o.S)[fl["rb"]]
                mode = fl["mode"]
                addr = (rb + (0, 1, o.S, o.S)[mode]) & 511
                wbv = m16(rb + (0, 1, o.S, o.S)[mode]) if mode in (1, 3) else None
                n.memop = {"kind": name, "addr": addr, "rb": fl["rb"], "wb": wbv, "granted": False,
                           "reg": fl["rd"] if name == "ld" else fl["rs"]}
            else:
                res = self.exec_other(o, n, ins, fl, ctx, rx, tx, ovr)
                complete = True
                if res is not None and res[0] == "redir":
                    redirect = res[1]
                else:
                    advance = True
                    if res is not None and res[0] == "halt":
                        n.run, n.halted = 0, 1

        if fire and self.trace:
            late = gev is not None and not live[gev] and o.F[gev]
            self.fires.append((cyc, o.PC, bool(late), ins["name"], loopctl is None or bool(loopctl[1])))

        # ---------- memory instruction in progress (or started this clock)
        st_written = None
        if redirect is None and n.memop is not None and not trap:
            mo = n.memop
            if mo["granted"] and arrive is not None and arrive[0] == "ld":
                self.mem_done(n, mo, arrive[2])
                n.memop = None
                complete = advance = True
            elif not mo["granted"] and active and arrive is None and env.owner == e:
                if mo["kind"] == "st":
                    st_written = (mo["addr"], o.getreg(mo["reg"]))      # rs read at completion (D066)
                    self.mem_done(n, mo, None)
                    n.memop = None
                    complete = advance = True

        # ---------- next instruction
        fold = None
        if redirect is not None:
            n.irv = n.pfv = n.hrv = 0
            n.PC, n.FP = redirect, (redirect + 1) & 511
            arrive = None
            if o.xm:
                n.xm = 0
            if self.trace:
                pass
        else:
            # jump folding of an unconditional jmp waiting in PF
            if (active and not o.xm and o.pfv and arrive is None and n.memop is None):
                pi, pf = decode(o.PF) if o.PF is not None else (None, None)
                if pi is not None and pi["name"] in ("jmp", "jmpa"):
                    fold = (o.FP + 1 + sext7(pf["rel"])) & 511 if pi["name"] == "jmp" else pf["addr"]
            if loopctl is not None:
                rr, lend = loopctl
                if rr == 1:
                    if lend:
                        advance = True
                elif rr == 2:
                    if lend:
                        if o.hrv:
                            advance = True
                        else:
                            redirect = (o.PC + 2) & 511
                    elif o.hrv:
                        n.IR, n.HR, n.PC = o.HR, o.IR, (o.PC + 1) & 511
                        self.armed(n, cyc)
                    else:
                        n.HR, n.hrv = o.IR, 1
                        advance = True
                else:
                    if lend:
                        advance = True
                    elif o.hrv:
                        n.IR, n.HR, n.PC = o.HR, o.IR, (o.PC - 1) & 511
                        self.armed(n, cyc)
                    else:
                        redirect = (o.PC - 1) & 511
                if lend:
                    n.hrv = 0
                if redirect is not None:
                    n.irv = n.pfv = n.hrv = 0
                    n.PC, n.FP = redirect, (redirect + 1) & 511
                    arrive = None
                    fold = None
            if advance and o.xm:
                n.xm, n.irv = 0, 0
                advance = False
            elif advance:
                if o.pfv and fold is None:
                    n.IR, n.irv, n.PC, n.FP, n.pfv = o.PF, 1, o.FP, (o.FP + 1) & 511, 0
                    self.armed(n, cyc)
                elif o.pfv and fold is not None:
                    n.irv, n.pfv, n.PC, n.FP = 0, 0, fold, (fold + 1) & 511
                elif arrive is not None and arrive[0] == "fetch":
                    n.IR, n.irv, n.PC, n.FP = arrive[2], 1, o.FP, (o.FP + 1) & 511
                    arrive = None
                    self.armed(n, cyc)
                else:
                    n.irv, n.PC, n.FP = 0, o.FP, (o.FP + 1) & 511
            elif fold is not None:
                n.pfv, n.FP = 0, fold
            if arrive is not None and arrive[0] == "fetch":
                if not n.irv:
                    n.IR, n.irv = arrive[2], 1
                    self.armed(n, cyc)
                else:
                    n.PF, n.pfv = arrive[2], 1
        if complete and o.stepm and not o.xm:
            n.run, n.stepm = 0, 0

        # ---------- fetch request and grant
        req = None
        if active:
            if redirect is not None and not o.xm:
                req = ("fetch", redirect)
            elif fold is not None:
                req = ("fetch", fold)
            elif arrive is not None or o.flight is not None:
                req = None
            elif n.memop is not None and not n.memop["granted"]:
                req = (n.memop["kind"], n.memop["addr"])
            elif o.xm:
                req = None
            elif not n.irv:
                req = ("fetch", n.PC)
            elif not o.pfv and not n.pfv:
                req = ("fetch", n.FP)
        if st_written is not None:
            req = ("st", st_written[0])
        n.req = req                   # on n: the final update below would restore the stale value
        if req is not None and env.owner == e:
            if req[0] == "st":
                env.mem_write = st_written
            else:
                d = env.mem[req[1]]
                if d is None and req[0] == "ld":
                    env.undef_reads += 1
                    d = 0
                n.flight = (req[0], req[1], d)
                if req[0] == "ld":
                    n.memop = dict(n.memop, granted=True)

        # ---------- event flags (ARCH 6)
        if not active:
            n.F, n.D = [0] * 10, [0] * 10
        elif n.waiting:
            n.F, n.D = [0] * 10, [0] * 10
        else:
            for k in range(10):
                if fire and gev == k:
                    if o.D[k]:
                        n.ovr = 1
                    n.F[k] = o.F[k] & live[k]
                    n.D[k] = 0
                elif live[k]:
                    n.D[k] = o.F[k]
                    n.F[k] = 1
        if wf is not None and ccmode[wf] == 1:
            n.F, n.D = [0] * 10, [0] * 10

        # ---------- timer (runs whether or not the engine is active)
        start = ctx.tstart
        if start is None and sedge:
            edge = ((sedge & 1) and rise[spin]) or ((sedge & 2) and fall[spin])
            if edge:
                if not swin:
                    start = (o.P, 0)
                elif not o.trun or o.T < o.C:
                    start = (o.P, 1)
        if start is not None:
            n.T, n.tone = m16(start[0]), start[1]
            n.trun, n.FA, n.PSC, n.skip = 1, 0, 0, 0
            n.F[8] = n.F[9] = n.D[8] = n.D[9] = 0
        elif ctx.tstop:
            n.trun = 0
        elif o.trun:
            n.PSC = (o.PSC + 1) & 255
            if stp:
                if o.skip:
                    n.skip = 0
                elif o.T == 0:
                    if o.tone:
                        n.trun = 0
                    else:
                        n.T = o.P
                        s = o.FA + frac
                        n.FA, n.skip = s & 255, s >> 8
                else:
                    n.T = o.T - 1

        # ---------- followers, previous levels, watchers
        dcur = (((o.S >> wl) if not lsb else o.S) & 1) ^ inv
        for k in range(4):
            if o.fol[k] and k not in ctx.acts and n.fol[k]:
                n.val[k] = dcur
        n.prev = lv
        n.wprev = cc

        # ---------- host control pulses (take effect at the end of this clock)
        if pul.get("stop"):
            n.run, n.stepm = 0, 0
            if o.xm or n.xm:
                n.xm, n.irv = 0, 0
                n.memop = n.flight = None          # the injected word's memory access goes with it (D069)
        if pul.get("run"):
            n.run, n.halted, n.stepm = 1, 0, 0
        if pul.get("step"):
            n.run, n.stepm, n.halted = 1, 1, 0
        if "exec" in pul:
            n.IR, n.irv, n.xm, n.pfv, n.hrv = pul["exec"], 1, 1, 0, 0
            n.FP = (n.PC + 1) & 511               # a loaded loop pair had FP behind its tail (D069)
            n.flight = None
            n.memop = None            # an ungranted ld/st is dropped (D067)
            self.armed(n, cyc)

        # ---------- strobes to shared blocks
        if ctx.tx_push is not None:
            env.tx_push[e] = ctx.tx_push
        if ctx.rx_pop:
            env.rx_pop[e] = True
        env.sig_set |= ctx.sset
        env.sig_clr |= ctx.sclr
        for op in ctx.lf:
            env.lf.append((e,) + op)
        self.__dict__.update(n.__dict__)

    def armed(self, n, cyc):
        if self.trace:
            self.arms.append((cyc + 1, n.PC, n.IR))

    def mem_done(self, n, mo, data):
        if mo["wb"] is not None:
            self.setreg(n, mo["rb"], mo["wb"])
        if mo["kind"] == "ld":
            self.setreg(n, mo["reg"], data)

    @staticmethod
    def setreg(n, r, v):
        v = m16(v)
        if r < 3:
            n.R[r] = v
        elif r == 3:
            n.S = v
        elif r == 4:
            n.P = v
        elif r == 5:
            n.C = v
        elif r == 7:
            n.L = v & 511

    # ------------------------------------------------------------------ E format
    def exec_e(self, o, n, fl, ctx, lv, hrx, htx, rx, wl, lsb, inpin, inv, diff, apush, apull, alt, podd, lfen, rl,
               r1):
        w = wl
        mask = (1 << (w + 1)) - 1
        st = SimpleNamespace(S=o.S, N=o.N, B=o.B, PAR=o.PAR, RUN=o.RUN, xfer=False, wend=False, lend=False,
                             pushed=None, popped=False)

        def tap(v):
            return (v & 1) if lsb else (v >> w) & 1

        def tapupd(t):
            if r1:
                st.RUN = min(st.RUN + 1, 7) if t else 0
            else:
                st.RUN = min(st.RUN + 1, 7) if t == st.B else 1
            st.B = t

        def do_shift(fbit, t):
            N0 = st.N
            wend = N0 == 1 or (N0 == 0 and w == 0)
            st.PAR = (podd if N0 == 0 else st.PAR) ^ t
            st.N = w if N0 == 0 else N0 - 1
            st.S = (((st.S & mask) >> 1) | (fbit << w)) if lsb else (((st.S << 1) | fbit) & mask)
            tapupd(t)
            if lfen:
                ctx.lf.append(("step", t))
            ctx.shifted = True
            if wend:
                st.wend = True
                xf = (apush or apull) and (not apush or htx) and (not apull or hrx)
                if xf:
                    st.xfer = True
                    if apush:
                        ctx.tx_push = st.S & 0xFF
                    if apull:
                        st.S = rx.head()
                        ctx.rx_pop = True
                st.lend = not xf

        s = fl["s"]
        if s:
            l = lv[inpin]
            b = l ^ (o.LV if diff else 0) ^ inv
            n.LV = l
            do_shift(b, b)
        D = tap(st.S) ^ inv
        pa, aa, pb, ab = fl["pa"], fl["aa"], fl["pb"], fl["ab"]
        if aa:
            ctx.acts.add(pa)
            if aa == 1:
                n.val[pa], n.oe[pa], n.fol[pa] = 0, 1, 0
            elif aa == 2:
                n.val[pa], n.oe[pa], n.fol[pa] = 1, 1, 0
            elif aa == 3:
                n.oe[pa], n.fol[pa] = 0, 0
            elif aa in (4, 5):
                n.val[pa] = (o.val[pa] ^ D) if diff else D
                n.oe[pa], n.fol[pa] = 1, 0
                n.LD = D
                if aa == 5 and not s:
                    do_shift(1, D)
            elif aa == 6:
                if alt == 0:
                    q = st.PAR
                elif alt == 1:
                    q = 1 - D
                elif alt == 2:
                    q = 1 - st.B
                    tapupd(q)
                else:
                    q = st.B
                n.val[pa], n.oe[pa], n.fol[pa] = q, 1, 0
                n.LD = q
            else:
                n.val[pa], n.oe[pa], n.fol[pa] = D, 1, 1
                n.LD = D
        if ab:
            ctx.acts.add(pb)
            if ab == 3:
                n.val[pb] = 1 - o.val[pb]
            else:
                n.val[pb] = ab - 1
            n.oe[pb], n.fol[pb] = 1, 0
        n.S, n.N, n.B, n.PAR, n.RUN = st.S, st.N, st.B, st.PAR, st.RUN
        return (fl["rr"], st.lend)

    # ------------------------------------------------------------------ GM, IM, RX
    def exec_other(self, o, n, ins, fl, ctx, rx, tx, ovr):
        name = ins["name"]
        R = o.getreg
        if name == "mov":
            self.setreg(n, fl["rd"], R(fl["rs"]))
        elif name in ("push", "pushh", "evt"):
            v = 0x100 | fl["c"] if name == "evt" else (R(fl["rs"]) & 0xFF if name == "push" else R(fl["rs"]) >> 8)
            if tx.full():
                ovr()
            else:
                ctx.tx_push = v
        elif name in ("pull", "pullh"):
            if rx.empty():
                ovr()
            else:
                b = rx.head()
                ctx.rx_pop = True
                self.setreg(n, fl["rd"], b if name == "pull" else (R(fl["rd"]) & 0xFF) | (b << 8))
        elif name == "tsync":
            ctx.tstart = (o.P, 0)
        elif name == "tset":
            ctx.tstart = (R(fl["rs"]), 1)
        elif name == "tstop":
            ctx.tstop = True
        elif name == "smp":
            sh, tp = o.cfg[0], o.cfg[1]
            return self.do_smp(o, n, sh, tp)
        elif name == "wrst":
            n.N, n.PAR, n.RUN = 0, (o.cfg[1] >> 2) & 1, 0
        elif name == "ovclr":
            n.ovr = 0
        elif name == "ret":
            return ("redir", o.L & 511)
        elif name == "halt":
            return ("halt",)
        elif name == "sset":
            ctx.sset |= 1 << fl["n"]
        elif name == "sclr":
            ctx.sclr |= 1 << fl["n"]
        elif name == "watch":
            n.wen = fl["m"]
        elif name in ("li", "lih", "addi", "cmpi"):
            rd, imm = fl["rd"], fl["imm"]
            v = R(rd)
            if name == "li":
                self.setreg(n, rd, imm)
            elif name == "lih":
                self.setreg(n, rd, (v & 0xFF) | (imm << 8))
            elif name == "addi":
                si = imm - 256 if imm & 128 else imm
                r = v + (si & 0xFFFF)
                self.setreg(n, rd, r)
                n.Z, n.Cy = int(m16(r) == 0), r >> 16 & 1
            else:
                n.Z, n.Cy = int(m16(v - imm) == 0), int(v >= imm)
        elif name in ("jmp", "jmpa"):
            return ("redir", (o.PC + 1 + sext7(fl["rel"])) & 511 if name == "jmp" else fl["addr"])
        elif name == "call":
            n.L = (o.PC + 1) & 511
            return ("redir", fl["addr"])
        elif name == "wcfg":
            self.do_wcfg(o, n, fl["creg"], R(fl["rs"]), ctx)
        elif name == "rcfg":
            c = fl["creg"]
            v = {16: ctx_lfsr[0] & 0xFFFF, 17: ctx_lfsr[0] >> 16, 21: ctx_sig[0]}.get(c, 0)
            self.setreg(n, fl["rd"], v)
        else:
            self.alu(o, n, name, fl)
        return None

    def do_smp(self, o, n, sh, tp):
        inpin, inv, diff = (sh >> 5) & 3, (sh >> 7) & 1, (sh >> 8) & 1
        r1, podd = (tp >> 7) & 1, (tp >> 2) & 1
        l = self._lv[inpin]
        t = l ^ (o.LV if diff else 0) ^ inv
        n.LV = l
        if r1:
            n.RUN = min(o.RUN + 1, 7) if t else 0
        else:
            n.RUN = min(o.RUN + 1, 7) if t == o.B else 1
        n.B = t
        n.PAR = o.PAR ^ t
        return None

    def do_wcfg(self, o, n, c, v, ctx):
        if c <= 8:
            n.cfg[c] = v
            if c == 0:
                n.N, n.PAR, n.RUN = 0, (o.cfg[1] >> 2) & 1, 0
            if c in (3, 4):
                k = (c - 3) * 2
                n.bsup[k] = n.bsup[k + 1] = 1
        elif 16 <= c <= 20:
            ctx.lf.append(("write", c, v))

    def alu(self, o, n, name, fl):
        rd, rs = fl["rd"], fl["rs"]
        a, b = o.getreg(rd), o.getreg(rs)
        cy = o.Cy
        if name == "add":
            r = a + b
            cy = r >> 16 & 1
        elif name == "adc":
            r = a + b + o.Cy
            cy = r >> 16 & 1
        elif name in ("sub", "cmp"):
            r = a - b
            cy = int(a >= b)
        elif name in ("and", "tst"):
            r = a & b
        elif name == "or":
            r = a | b
        elif name == "xor":
            r = a ^ b
        elif name == "shl":
            r, cy = b << 1, b >> 15 & 1
        elif name == "shr":
            r, cy = b >> 1, b & 1
        elif name == "rlc":
            r, cy = (b << 1) | o.Cy, b >> 15 & 1
        elif name == "rrc":
            r, cy = (o.Cy << 15) | (b >> 1), b & 1
        else:
            raise IssError("alu " + name)
        n.Z, n.Cy = int(m16(r) == 0), cy
        if name not in ("cmp", "tst"):
            self.setreg(n, rd, r)


ctx_lfsr = [0]
ctx_sig = [0]


class Link:
    # host link, chip side (HOSTIF.md): receiver, parser, transmitter, H_RDY.
    def __init__(self, chip):
        self.chip = chip
        self.rate = 0
        self.rx_state, self.rx_cnt, self.rx_bits, self.rx_B, self.rx_i = "idle", 0, 0, 16, 0
        self.pstate, self.op, self.args, self.need = "data", 0, [], 0
        self.sel = None
        self.pulses_next = {}
        self.h_tx, self.h_rdy = 1, 0
        self.tx_bits, self.tx_cnt, self.tx_B = None, 0, 16
        self.outq = []            # bytes queued (whole escape sequences)
        self.reply = []           # reply bytes, the reply transmits before anything else
        self.reply_pending = 0    # reply words still to be read from memory
        self.reply_data = 0       # RDMEM data bytes at the end of reply
        self.notices = []
        self.cur = None
        self.served = 0
        self.wr_pending = None    # (addr, data)
        self.rd_queue = []        # addresses to read for RDMEM
        self.rd_flight = None
        self.wr_addr = 0
        self.wr_left = 0
        self.wr_lo = None
        self.prev_flags = [(0, 0, 0)] * 4
        self.h_tx_next, self.h_rdy_next = 1, 0

    def bit_time(self):
        return {0: 16, 1: 8, 2: 4, 3: 16}[self.rate]

    def err(self):
        if 0x8C not in self.notices:
            self.notices.append(0x8C)

    def mem_req(self):
        if self.wr_pending is not None:
            return ("w",) + self.wr_pending
        if self.rd_queue and self.rd_flight is None and self.reply_data == 0:
            return ("r", self.rd_queue[0], None)
        return None

    def step(self, env, h_gnt, hreq):
        chip = self.chip
        # memory client
        if self.rd_flight is not None:
            d = self.rd_flight
            self.reply += [d & 0xFF, d >> 8]
            self.reply_data += 2
            self.rd_flight = None
            self.reply_pending -= 1
        if hreq is not None and h_gnt:
            if hreq[0] == "w":
                env.mem_write_h = (hreq[1], hreq[2])
                self.wr_pending = None
            else:
                d = env.mem[hreq[1]]
                self.rd_flight = 0 if d is None else d
                self.rd_queue.pop(0)
        # notices on rising flags
        for e, eng in enumerate(chip.snap):
            fl = (eng.ovr, eng.halted, eng.trap)
            for k, code in enumerate((0x80, 0x84, 0x88)):
                if fl[k] and not self.prev_flags[e][k] and (code | e) not in self.notices:
                    self.notices.append(code | e)
            self.prev_flags[e] = fl
        # receiver
        rxl = (env.lvl_raw >> 3) & 1
        byte = None
        if self.rx_state == "idle":
            if rxl == 0:
                self.rx_state, self.rx_B, self.rx_cnt, self.rx_i, self.rx_bits = "rx", self.bit_time(), 0, 0, 0
                self.rx_cnt = self.rx_B // 2
        elif self.rx_state == "rx":
            self.rx_cnt -= 1
            if self.rx_cnt == 0:
                i = self.rx_i
                if i == 0:
                    if rxl:
                        self.rx_state = "idle"
                elif i <= 8:
                    self.rx_bits |= rxl << (i - 1)
                else:
                    if rxl:
                        byte = self.rx_bits
                        self.rx_state = "idle"
                    else:
                        self.err()
                        if self.rx_bits == 0:
                            self.pstate, self.need = "data", 0
                        self.rx_state = "wait"
                self.rx_i += 1
                self.rx_cnt = self.rx_B
        elif self.rx_state == "wait":
            if rxl:
                self.rx_state = "idle"
        if byte is not None:
            self.parse(byte, env)
        # H_RDY (registered): data or ESC state (D080), a stream selected, fewer than 2 free entries
        self.h_rdy_next = int(self.pstate in ("data", "op") and self.sel is not None and chip.rx[self.sel].free() < 2)
        # transmitter
        self.tx_step(env)

    def parse(self, b, env):
        chip = self.chip
        if self.pstate == "data":
            if b == ESC:
                self.pstate = "op"
            else:
                self.data_byte(b, env)
            return
        if self.pstate == "op":
            self.op = b
            self.args = []
            if b == ESC:
                self.pstate = "data"
                self.data_byte(ESC, env)
                return
            need = 0
            if 0x60 <= b <= 0x63:
                need = 2
            elif b in (0x70, 0x71, 0x72):
                need = 3
            elif b == 0x73:
                need = 1
            elif b <= 0x03 or (0x20 <= b <= 0x5F) or b == 0x74:
                need = 0
            else:
                self.err()
                self.pstate = "data"
                return
            if need:
                self.pstate, self.need = "args", need
                return
            self.pstate = "data"
            self.command(b, [], env)
            return
        if self.pstate == "args":
            self.args.append(b)
            if len(self.args) == self.need:
                self.pstate = "data"
                self.command(self.op, self.args, env)
            return
        if self.pstate == "wrdata":
            if self.wr_lo is None:
                self.wr_lo = b
                return
            w = self.wr_lo | b << 8
            self.wr_lo = None
            if self.wr_pending is not None:
                self.err()
            self.wr_pending = (self.wr_addr, w)
            self.wr_addr = (self.wr_addr + 1) & 511
            self.wr_left -= 1
            if self.wr_left == 0:
                self.pstate = "data"

    def data_byte(self, b, env):
        if self.sel is None:
            self.err()
            return
        env.rx_wr[self.sel] = b

    def command(self, op, a, env):
        chip = self.chip
        p = self.pulses_next
        if op <= 3:
            self.sel = op
        elif 0x20 <= op <= 0x5F:
            kind = {2: "stop", 3: "run", 4: "step", 5: "rst"}[op >> 4]
            for e in range(4):
                if op >> e & 1:
                    if kind in ("run", "step") and chip.snap[e].xm:
                        self.err()
                        continue
                    p.setdefault(e, {})[kind] = True
        elif 0x60 <= op <= 0x63:
            e = op & 3
            if chip.snap[e].run or chip.snap[e].xm:
                self.err()
            else:
                p.setdefault(e, {})["exec"] = a[0] | a[1] << 8
        elif op == 0x70:
            self.wr_addr, self.wr_left, self.wr_lo = (a[0] | a[1] << 8) & 511, a[2] + 1, None
            self.pstate = "wrdata"
        elif op == 0x71:
            addr, cnt = (a[0] | a[1] << 8) & 511, a[2] + 1
            self.rd_queue = [(addr + k) & 511 for k in range(cnt)]
            self.rd_flight = None             # a new reply replaces one in progress, as jspe_link (D085)
            self.reply = [ESC, 0xC1]
            self.reply_pending, self.reply_data = cnt, 0
        elif op == 0x72:
            self.wrreg(a[0], a[1] | a[2] << 8)
        elif op == 0x73:
            v = self.rdreg(a[0])
            self.reply, self.reply_pending, self.reply_data = [ESC, 0xC3, v & 0xFF, v >> 8], 0, 0
            self.rd_queue, self.rd_flight = [], None
        elif op == 0x74:
            self.reply, self.reply_pending, self.reply_data = [ESC, 0xC4, ID & 0xFF, ID >> 8], 0, 0
            self.rd_queue, self.rd_flight = [], None

    def wrreg(self, r, v):
        chip = self.chip
        if r == 0x01:
            self.rate = v & 3
        elif r == 0x02:
            if any(x.run or x.xm for x in chip.snap):
                self.err()
            else:
                chip.slotmap = v & 0xFF
        elif r == 0x03:
            chip.sig_host = v & 0xFF
        elif r == 0x04:
            chip.filt = v & 0xFFFF
        elif r == 0x06:
            for e in range(4):
                if v >> e & 1:
                    chip.eng[e].ovr = 0
                if v >> (4 + e) & 1:
                    chip.eng[e].halted = chip.eng[e].trap = 0
        elif 0x10 <= r <= 0x1F:
            idx = r - 0x10
            if idx not in (4, 5):
                chip.pad_wr.append((idx, v & 0x7F))

    def rdreg(self, r):
        chip = self.chip
        if r == 0x00:
            return ID
        if r == 0x01:
            return self.rate
        if r == 0x02:
            return chip.slotmap
        if r == 0x03:
            return chip.sig
        if r == 0x05:
            return chip.lvl_raw
        if 0x08 <= r <= 0x0B:
            e = r - 8
            return chip.snap[e].est
        return 0

    def next_sequence(self, env):
        if self.reply:
            # one RDMEM word is buffered at a time (D078): the next read waits until both bytes are taken
            if len(self.reply) <= self.reply_data:
                self.reply_data -= 1
            return [self.reply.pop(0)]
        if self.reply_pending:
            return None
        if self.notices:
            c = min(self.notices)       # pending notices are flags, lowest code first (D078)
            self.notices.remove(c)
            return [ESC, c]
        chip = self.chip
        waiting = [e for e in range(4) if not chip.tx[e].empty() and e not in env.tx_rd]
        if not waiting:
            return None
        e = self.cur
        if e is None or e not in waiting or (self.served >= 4 and any(x != e for x in waiting)):
            nxt = [x for x in waiting if e is None or x > e] or waiting
            e2 = nxt[0]
            if e2 != self.cur:
                self.cur, self.served = e2, 0
                return [ESC, e2]
            self.served = 0
        ent = chip.tx[e].head()
        env.tx_rd[e] = True
        self.served += 1
        if ent & 0x100:
            return [ESC, 0x40 | (ent & 63)]
        return [ESC, ESC] if ent == ESC else [ent]

    def tx_step(self, env):
        if self.tx_bits is None:
            if not self.outq:
                seq = self.next_sequence(env)
                if seq:
                    self.outq += seq
            if self.outq:
                b = self.outq.pop(0)
                self.tx_bits = [0] + [(b >> k) & 1 for k in range(8)] + [1]
                self.tx_B = self.bit_time()
                self.tx_cnt = 0
        if self.tx_bits is not None:
            bit = self.tx_bits[self.tx_cnt // self.tx_B]
            self.h_tx_next = bit
            self.tx_cnt += 1
            if self.tx_cnt == 10 * self.tx_B:
                self.tx_bits = None
        else:
            self.h_tx_next = 1


def pin_filter(sync, h1, h2, held, filt):
    # 3-sample filter (ARCH 2.1): h1, h2 = the two previous synchronized samples; returns (lvl, next held)
    eq = ~(sync ^ h1) & ~(sync ^ h2) & 0xFFFF
    out = (sync & eq) | (held & ~eq & 0xFFFF)
    return (sync & ~filt & 0xFFFF) | (out & filt), out


def pad_outputs(pad, val, oe, h_tx, h_rdy):
    # registered pad values (ARCH 2.1): pad = 16 configurations, val/oe = per engine 4 logical pins
    uo, uout, uoe = 0, 0, 0
    for j in range(16):
        c = pad[j]
        e, lp_ = (c >> 2) & 3, c & 3
        v = val[e][lp_] ^ ((c >> 4) & 1)
        o = oe[e][lp_]
        en, od = (c >> 6) & 1, (c >> 5) & 1
        if j < 8:
            uo |= (v if en else 0) << j
        elif en:
            k = j - 8
            if od:
                uoe |= (o & (1 - v)) << k
            else:
                uout |= v << k
                uoe |= o << k
    return (uo & ~0x30) | h_tx << 4 | h_rdy << 5, uout, uoe


class Chip:
    def __init__(self, direct=True, trace_file=None):
        self.direct = direct
        self.trace_file = trace_file
        self.reset()

    def reset(self):
        self.cycle = 0
        self.eng = [Engine(k) for k in range(4)]
        self.rx = [Fifo() for _ in range(4)]
        self.tx = [Fifo() for _ in range(4)]
        self.mem = [None] * 512
        self.slot = 0
        self.slotmap = 0xE4
        self.sig = 0
        self.sig_host = None
        self.lfsr, self.poly, self.lf_right = 0, 0, 0
        self.filt = 0
        self.ff1 = self.ff2 = 0
        self.fhist = [0, 0]
        self.fheld = 0
        self.pad = [0] * 16
        self.pad_wr = []
        self.uo, self.uio_out, self.uio_oe = 0x10, 0, 0
        self.link = Link(self)
        self.pulses = {}
        self.host_in = [[] for _ in range(4)]      # direct mode: bytes waiting for the RX FIFO
        self.host_out = [[] for _ in range(4)]     # direct mode: (cycle, entry) popped from the TX FIFO
        self.tx_block = [False] * 4
        self.pad_trace = []
        self.undef_reads = 0
        self.lvl_raw = 0
        self.fifo_log = None       # list of (cycle, engine, kind) when enabled

    # ---------------------------------------------------------------- loading (direct, by backdoor)
    def load_image(self, img):
        for a, w in img.words.items():
            self.mem[a] = w
        if img.slotmap is not None:
            self.slotmap = img.slotmap
        self.filt = img.filt

    def pulse(self, e, kind, word=None):
        d = self.pulses.setdefault(e, {})
        d[kind] = word if kind == "exec" else True

    def exec_word(self, e, w, limit=10000):
        self.pulse(e, "exec", w)
        self.step()
        for _ in range(limit):
            if not self.eng[e].xm:
                return
            self.step()
        raise IssError("injected instruction %04x did not complete" % w)

    def boot(self, img, phase=None, run=True, engines=None):
        self.load_image(img)
        engs = sorted(img.engines) if engines is None else engines
        for e in engs:
            for p, c in img.engines[e]["pads"].items():
                self.pad[p] = c
        for e in engs:
            for w in img.exec_words(e):
                self.exec_word(e, w)
        for e in engs:
            for p, c in img.engines[e]["pads"].items():
                self.pad[p] = c | 0x40
        if run:
            self.start(engs, phase)

    def start(self, engs, phase=None):
        if phase is not None:
            while self.slot != phase:
                self.step()
        self.t_run = self.cycle          # trace analysis starts here (EXEC fires carry the program's PC)
        for e in engs:
            self.pulse(e, "run")

    def est(self, e):
        eng = self.eng[e]
        return (eng.PC | eng.run << 9 | eng.waiting << 10 | eng.ovr << 11 | eng.trap << 12 | eng.halted << 13 |
                int(not self.rx[e].empty()) << 14 | int(not self.tx[e].empty()) << 15)

    # ---------------------------------------------------------------- one clock
    def step(self, ui_in=0, uio_in=0):
        sync = self.ff2
        self.lvl_raw = sync
        filtered, newheld = pin_filter(sync, self.fhist[0], self.fhist[1], self.fheld, self.filt)
        env = SimpleNamespace(cycle=self.cycle, lvl=filtered, lvl_raw=sync, sig=self.sig, mem=self.mem,
                              owner=(self.slotmap >> (2 * self.slot)) & 3, rx=self.rx, tx=self.tx,
                              pulses=self.link.pulses_next, flush={}, tx_push={},
                              rx_pop={}, sig_set=0, sig_clr=0, lf=[], mem_write=None, mem_write_h=None,
                              undef_reads=0, rx_wr={}, tx_rd={})
        for e, p in self.pulses.items():
            env.pulses.setdefault(e, {}).update(p)
        self.pulses = {}
        self.link.pulses_next = {}
        ctx_lfsr[0], ctx_sig[0] = self.lfsr, self.sig
        # the link sees engine outputs as registered: state at the start of the clock (D077)
        self.snap = [SimpleNamespace(run=g.run, xm=g.xm, ovr=g.ovr, halted=g.halted, trap=g.trap, est=self.est(e))
                     for e, g in enumerate(self.eng)]
        # pin state at the start of the clock: the pad register loads it (D081)
        pin_val, pin_oe = [e.val[:] for e in self.eng], [e.oe[:] for e in self.eng]
        for eng in self.eng:
            eng._lv = None
        for eng in self.eng:
            self._prep_lv(eng, env)
            eng.step(env)
        self.undef_reads += env.undef_reads
        # host memory client: only slots whose owner does not request
        if not self.direct:
            hreq = self.link.mem_req()
            h_gnt = hreq is not None and self.eng[env.owner].req is None
            self.link.step(env, h_gnt, hreq)
        # direct host model
        if self.direct:
            for e in range(4):
                if self.host_in[e] and e not in env.rx_wr:
                    env.rx_wr[e] = self.host_in[e][0]
                if not self.tx[e].empty() and not self.tx_block[e] and e not in env.tx_rd:
                    env.tx_rd[e] = True
                    self.host_out[e].append((self.cycle, self.tx[e].head()))
        if self.fifo_log is not None:
            for e in range(4):
                for kind, d in (("push", env.tx_push), ("pop", env.rx_pop), ("hwr", env.rx_wr), ("hrd", env.tx_rd)):
                    if e in d and d[e] is not False:
                        self.fifo_log.append((self.cycle, e, kind))
        # commit shared state
        for e in range(4):
            fl = env.flush.get(e, False)
            wr = env.rx_wr.get(e)
            ok = self.rx[e].commit(wr, env.rx_pop.get(e, False), fl)
            if wr is not None and not fl:
                if ok:
                    if self.direct and self.host_in[e]:
                        self.host_in[e].pop(0)
                elif not self.direct:
                    self.eng[e].ovr = 1
            self.tx[e].commit(env.tx_push.get(e), env.tx_rd.get(e, False), fl)
        if env.mem_write is not None:
            self.mem[env.mem_write[0]] = env.mem_write[1] & 0xFFFF
        if env.mem_write_h is not None:
            self.mem[env.mem_write_h[0]] = env.mem_write_h[1]
        self.sig = (self.sig | env.sig_set) & ~env.sig_clr & 0xFF
        if self.sig_host is not None:
            self.sig, self.sig_host = self.sig_host, None
        self.lfsr_commit(env.lf)
        # pads (registered): owner mux of the registered pin state (D081), pad mode
        # H_TX / H_RDY: registered in the link (ARCH 2.5) and again in the pad register (D078)
        uo, uout, uoe = pad_outputs(self.pad, pin_val, pin_oe,
                                    self.link.h_tx, self.link.h_rdy)
        self.link.h_tx, self.link.h_rdy = self.link.h_tx_next, self.link.h_rdy_next
        for idx, v in self.pad_wr:
            self.pad[idx] = v
        self.pad_wr = []
        if (uo, uout, uoe) != (self.uo, self.uio_out, self.uio_oe):
            self.pad_trace.append((self.cycle + 1, uo, uout, uoe))
            if self.trace_file:
                self.trace_file.write("%d pads uo=%02x uio=%02x oe=%02x\n" % (self.cycle + 1, uo, uout, uoe))
        if self.trace_file:
            for eng in self.eng:
                if eng.run or eng.xm:
                    self.trace_file.write("%d e%d pc=%03x irv=%d\n" % (self.cycle, eng.idx, eng.PC, eng.irv))
        self.uo, self.uio_out, self.uio_oe = uo, uout, uoe
        self.fheld = newheld
        self.fhist = [sync, self.fhist[0]]
        self.ff2, self.ff1 = self.ff1, (ui_in & 0xFF) | (uio_in & 0xFF) << 8
        self.slot = (self.slot + 1) & 3
        self.cycle += 1

    def _prep_lv(self, eng, env):
        lv = []
        for k in range(4):
            reg = eng.cfg[3 + (k >> 1)]
            s = (k & 1) * 8
            src, iv = (reg >> s) & 31, (reg >> (s + 5)) & 1
            if src < 16:
                b = (env.lvl >> src) & 1
            elif src < 24:
                b = (env.sig >> (src - 16)) & 1
            else:
                b = 1 if src == 25 else 0
            lv.append(b ^ iv)
        eng._lv = lv

    def lfsr_commit(self, ops):
        writes = sorted([op for op in ops if op[1] == "write"], key=lambda x: x[0])
        steps = sorted([op for op in ops if op[1] == "step"], key=lambda x: x[0])
        if writes:
            _, _, c, v = writes[0]
            if c == 16:
                self.lfsr = (self.lfsr & 0xFFFF0000) | v
            elif c == 17:
                self.lfsr = (self.lfsr & 0xFFFF) | v << 16
            elif c == 18:
                self.poly = (self.poly & 0xFFFF0000) | v
            elif c == 19:
                self.poly = (self.poly & 0xFFFF) | v << 16
            else:
                self.lf_right = v & 1
        elif steps:
            bit = steps[0][2]
            L = self.lfsr
            if self.lf_right:
                fb = bit ^ (L & 1)
                L = (L >> 1) ^ (self.poly if fb else 0)
            else:
                fb = bit ^ (L >> 31 & 1)
                L = ((L << 1) & 0xFFFFFFFF) ^ (self.poly if fb else 0)
            self.lfsr = L

    # ---------------------------------------------------------------- helpers for tests
    def run(self, n, drive=None):
        for _ in range(n):
            ui, uio = drive(self) if drive else (0, 0)
            self.step(ui, uio)

    def host_bytes(self, e):
        return [v for _, v in self.host_out[e] if not v & 0x100]


class HostLink:
    # host side of HOSTIF.md, clocked with the chip: drives H_RX, decodes H_TX, honours H_RDY.
    def __init__(self, B=16):
        self.B = B
        self.q = []             # (byte, is_data)
        self.bits, self.cnt = None, 0
        self.rx_state, self.rx_cnt, self.rx_bits, self.rx_i = "idle", 0, 0, 0
        self.got = []
        self.prev_tx = 1

    def send(self, data, is_data=False):
        for b in data:
            self.q.append((b, is_data))

    def level(self, chip):
        # value of H_RX during this clock; call once per clock before chip.step
        if self.bits is None and self.q:
            b, isd = self.q[0]
            if not (isd and chip.link.h_rdy):
                self.q.pop(0)
                self.bits = [0] + [(b >> k) & 1 for k in range(8)] + [1]
                self.cnt = 0
        if self.bits is None:
            return 1
        v = self.bits[self.cnt // self.B]
        self.cnt += 1
        if self.cnt == 10 * self.B:
            self.bits = None
        return v

    def sample(self, chip):
        # decode H_TX (uo_out[4]) after a chip step
        t = (chip.uo >> 4) & 1
        if self.rx_state == "idle":
            if t == 0:
                self.rx_state, self.rx_cnt, self.rx_i, self.rx_bits = "rx", self.B // 2, 0, 0
        else:
            self.rx_cnt -= 1
            if self.rx_cnt == 0:
                if 1 <= self.rx_i <= 8:
                    self.rx_bits |= t << (self.rx_i - 1)
                if self.rx_i == 9:
                    self.got.append(self.rx_bits)
                    self.rx_state = "idle"
                self.rx_i += 1
                self.rx_cnt = self.B

    def idle(self):
        return self.bits is None and not self.q


def play_bin(data):
    # .bin -> list of host operation byte strings (HOSTIF.md 7)
    import struct
    assert data[:4] == b"JSPE"
    cnt = struct.unpack_from("<H", data, 6)[0]
    p, out = 8, []
    for _ in range(cnt):
        t = data[p]
        p += 1
        if t == 1:
            a, nw = struct.unpack_from("<HH", data, p)
            p += 4
            words = struct.unpack_from("<%dH" % nw, data, p)
            p += 2 * nw
            for k in range(0, nw, 256):
                ch = words[k:k + 256]
                ad = a + k
                out.append(bytes([ESC, 0x70, ad & 0xFF, ad >> 8, len(ch) - 1]) +
                           b"".join(bytes([w & 0xFF, w >> 8]) for w in ch))
        elif t == 2:
            r, v = struct.unpack_from("<BH", data, p)
            p += 3
            out.append(bytes([ESC, 0x72, r, v & 0xFF, v >> 8]))
        elif t == 3:
            e, w = struct.unpack_from("<BH", data, p)
            p += 3
            out.append(bytes([ESC, 0x60 | e, w & 0xFF, w >> 8]))
        elif t == 4:
            out.append(bytes([ESC, 0x30 | data[p]]))
            p += 1
        elif t == 5:
            m = data[p]
            p += 1
            out.append(bytes([ESC, 0x20 | m, ESC, 0x50 | m]))
        elif t == 6:
            p += 4                                  # CLK: checked by the host, nothing sent
        else:
            raise ValueError("record type %d" % t)
    return out


if __name__ == "__main__":
    from jspe_asm import assemble
    img = assemble(sys.argv[1:])
    c = Chip()
    c.boot(img)
    c.run(2000)
    for e in range(4):
        print("e%d pc=%03x run=%d ovr=%d trap=%d" % (e, c.eng[e].PC, c.eng[e].run, c.eng[e].ovr, c.eng[e].trap))
