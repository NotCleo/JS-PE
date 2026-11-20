# ISS trace recorder for the engine lockstep (VERIF_PLAN 3, 5): per clock the inputs one engine sees in the ISS
# and what it must answer. Scenarios are plain ISS code (sw/tests); the cocotb test replays the traces on the DUT.
import random

import jspe_iss
from jspe_iss import Chip, Engine
from jspe_asm import decode

STIM = [("rst", 1), ("run", 1), ("stop", 1), ("step", 1), ("exec", 1), ("exec_word", 16), ("lvl", 16), ("sig", 8),
        ("gnt", 1), ("mem_v", 1), ("mem_rdata", 16), ("rx_rdata", 8), ("rx_empty", 1), ("rx_ovf", 1),
        ("tx_full", 1), ("tx_empty", 1), ("lfsr", 32), ("clr_ovr", 1), ("clr_ht", 1)]
EXP = [("req", 1), ("we", 1), ("addr", 9), ("wd_v", 1), ("wdata", 16), ("tx_wr", 1), ("tx_wdata", 9), ("rx_rd", 1),
       ("val", 4), ("oe", 4), ("sig_set", 8), ("sig_clr", 8), ("lf_step", 1), ("lf_bit", 1), ("lf_we", 1),
       ("lf_idx", 3), ("lf_wdata", 16), ("stat", 16), ("ovr", 1), ("halted", 1), ("trap", 1)]
# internal state: DUT path, width (order = vals())
STATE = [("u_seq.run", 1), ("u_seq.xm", 1), ("u_seq.stepm", 1), ("u_seq.irv", 1), ("u_seq.ir", 16), ("u_seq.pc", 9),
         ("u_seq.hrv", 1), ("u_seq.hr", 16), ("u_seq.pfv", 1), ("u_seq.pf", 16), ("u_seq.fp", 9), ("u_seq.l", 9),
         ("u_seq.fl_v", 1), ("u_seq.fl_ld", 1), ("u_seq.mo_v", 1), ("u_seq.wen", 2), ("u_seq.wprev", 2),
         ("u_seq.f", 10), ("u_seq.d", 10), ("u_seq.ovr", 1), ("u_seq.z", 1), ("u_seq.cy", 1), ("u_seq.halted", 1),
         ("u_seq.trap", 1), ("u_seq.waiting", 1), ("u_seq.cc0", 14), ("u_seq.cc1", 14), ("u_seq.wv0", 9),
         ("u_seq.wv1", 9),
         ("u_alu.r0", 16), ("u_alu.r1", 16), ("u_alu.r2", 16),
         ("u_shift.s", 16), ("u_shift.n", 4), ("u_shift.b", 1), ("u_shift.par", 1), ("u_shift.runl", 3),
         ("u_shift.lvq", 1), ("u_shift.ldq", 1), ("u_shift.shcfg", 11), ("u_shift.tapcfg", 8),
         ("u_timer.t", 16), ("u_timer.p", 16), ("u_timer.c", 16), ("u_timer.fa", 8), ("u_timer.psc", 8),
         ("u_timer.trun", 1), ("u_timer.tone", 1), ("u_timer.skip", 1), ("u_timer.tcfg", 15),
         ("u_epins.bnd", 24), ("u_epins.prev", 4), ("u_epins.bsup", 2), ("u_epins.oe", 4), ("u_epins.val", 4),
         ("u_epins.fol", 4)]
# a field is compared only when the named valid bit is 1
CARE = {"u_seq.ir": "u_seq.irv", "u_seq.hr": "u_seq.hrv", "u_seq.pf": "u_seq.pfv", "u_seq.fl_ld": "u_seq.fl_v"}


def layout(spec):
    out, off = {}, 0
    for name, w in spec:
        out[name] = (off, (1 << w) - 1)
        off += w
    return out


L_STIM, L_EXP, L_STATE = layout(STIM), layout(EXP), layout(STATE)
STATE_OFF = [L_STATE[n] for n, _ in STATE]


def pack(lay, **kw):
    v = 0
    for k, x in kw.items():
        off, m = lay[k]
        v |= (int(x) & m) << off
    return v


def get(lay, v, name):
    off, m = lay[name]
    return (v >> off) & m


def bits(lst):
    v = 0
    for k, x in enumerate(lst):
        if x:
            v |= 1 << k
    return v


def vals(e):
    c, fl = e.cfg, e.flight
    return (e.run, e.xm, e.stepm, e.irv, e.IR or 0, e.PC, e.hrv, e.HR or 0, e.pfv, e.PF or 0, e.FP, e.L,
            1 if fl is not None else 0, 1 if fl is not None and fl[0] == "ld" else 0,
            1 if e.memop is not None else 0, e.wen, bits(e.wprev), bits(e.F), bits(e.D), e.ovr, e.Z, e.Cy,
            e.halted, e.trap, e.waiting, c[5] & 0x3FFF, c[6] & 0x3FFF, c[7] & 511, c[8] & 511,
            e.R[0], e.R[1], e.R[2],
            e.S, e.N, e.B, e.PAR, e.RUN, e.LV, e.LD, c[0] & 0x7FF, c[1] & 0xFF,
            e.T, e.P, e.C, e.FA, e.PSC, e.trun, e.tone, e.skip, c[2] & 0x7FFF,
            (c[3] & 63) | (c[3] >> 8 & 63) << 6 | (c[4] & 63) << 12 | (c[4] >> 8 & 63) << 18,
            bits(e.prev), (1 if e.bsup[0] else 0) | (2 if e.bsup[2] else 0), bits(e.oe), bits(e.val), bits(e.fol))


def pack_state(v):
    s = 0
    for x, (off, m) in zip(v, STATE_OFF):
        s |= (int(x) & m) << off
    return s


class Trace:
    def __init__(self, idx, label):
        self.idx, self.label = idx, label
        self.recs = []            # (stim, exp, state, deposit)
        self.post = None
        self.pend = None
        self.inj = {}


class Recorder:
    cur = None

    def __init__(self, label=""):
        self.traces = []
        self.label = label

    def __enter__(self):
        Recorder.cur = self
        return self

    def __exit__(self, *a):
        Recorder.cur = None

    def clocks(self):
        return sum(len(t.recs) for t in self.traces)


def inject(eng, **kw):
    # host-side pulses for the next clock: clr_ovr, clr_ht, rx_ovf (the link's job at system level)
    eng._tr.inj.update(kw)


_orig_eng, _orig_chip = Engine.step, Chip.step


def _eng_step(self, env):
    tr = self.__dict__.get("_tr")
    pul = env.pulses.get(self.idx)
    if tr is None:
        rec = Recorder.cur
        if rec is None or not (pul or self.run or self.xm):
            return _orig_eng(self, env)
        tr = self._tr = Trace(self.idx, "%s #%d e%d" % (rec.label, len(rec.traces), self.idx))
        rec.traces.append(tr)
    pul = pul or {}
    e = self.idx
    v = vals(self)
    fl = self.flight
    rx, tx = env.rx[e], env.tx[e]
    rst = 1 if pul.get("rst") else 0
    stim = pack(L_STIM, rst=rst, run=1 if pul.get("run") else 0, stop=1 if pul.get("stop") else 0,
                step=1 if pul.get("step") else 0, exec=1 if "exec" in pul else 0, exec_word=pul.get("exec", 0),
                lvl=env.lvl, sig=env.sig, gnt=env.owner == e, mem_v=fl is not None,
                mem_rdata=(fl[2] or 0) if fl is not None else 0, rx_rdata=rx.head(), rx_empty=rx.empty(),
                tx_full=tx.full(), tx_empty=tx.empty(), lfsr=jspe_iss.ctx_lfsr[0])
    stat = (self.PC | self.run << 9 | self.waiting << 10 | self.ovr << 11 | self.trap << 12 | self.halted << 13 |
            int(not rx.empty()) << 14 | int(not tx.empty()) << 15)
    pre = dict(stat=stat, ovr=self.ovr, halted=self.halted, trap=self.trap, val=bits(self.val), oe=bits(self.oe))  # registered (D081)
    sset, sclr, nlf = env.sig_set, env.sig_clr, len(env.lf)
    env.sig_set = env.sig_clr = 0
    _orig_eng(self, env)
    if rst:
        exp = pack(L_EXP, **pre)
    else:
        rq = self.req
        mw = env.mem_write if env.owner == e else None
        txp = env.tx_push.get(e)
        lf = [op for op in env.lf[nlf:]]
        st = [op for op in lf if op[1] == "step"]
        wr = [op for op in lf if op[1] == "write"]
        exp = pack(L_EXP, req=rq is not None, we=rq is not None and rq[0] == "st", addr=rq[1] if rq else 0,
                   wd_v=mw is not None, wdata=mw[1] if mw else 0, tx_wr=txp is not None, tx_wdata=txp or 0,
                   rx_rd=env.rx_pop.get(e, False), sig_set=env.sig_set,
                   sig_clr=env.sig_clr, lf_step=bool(st), lf_bit=st[0][2] if st else 0, lf_we=bool(wr),
                   lf_idx=(wr[0][2] - 16) if wr else 0, lf_wdata=wr[0][3] if wr else 0, **pre)
    env.sig_set |= sset
    env.sig_clr |= sclr
    tr.pend = (stim, exp, pack_state(v), v != tr.post, self.ovr, self.halted, self.trap)


def _chip_step(self, ui_in=0, uio_in=0):
    _orig_chip(self, ui_in, uio_in)
    for eng in self.eng:
        tr = eng.__dict__.get("_tr")
        if tr is None or tr.pend is None:
            continue
        stim, exp, state, dep, ovr, halted, trap = tr.pend
        tr.pend = None
        inj, tr.inj = tr.inj, {}
        if inj.get("clr_ovr"):
            eng.ovr = 0
        if inj.get("clr_ht"):
            eng.halted = eng.trap = 0
        if inj.get("rx_ovf"):
            eng.ovr = 1
        stim |= pack(L_STIM, clr_ovr=bool(inj.get("clr_ovr")) or (ovr and not eng.ovr),
                     clr_ht=bool(inj.get("clr_ht")) or (halted and not eng.halted) or (trap and not eng.trap),
                     rx_ovf=bool(inj.get("rx_ovf")) or (not ovr and eng.ovr))
        tr.recs.append((stim, exp, state, dep))
        tr.post = vals(eng)


Engine.step, Chip.step = _eng_step, _chip_step


# ---------------------------------------------------------------- random programs (VERIF_PLAN 5, jspe_engine row)
G_W = [62, 4, 3, 3, 3, 2, 2, 1, 3, 3, 2, 2, 3, 3, 2, 2]


def rnd_word(r, undef):
    while True:
        f = r.choices(range(9), [28, 7, 12, 20, 10, 6, 5, 8, 4])[0]
        g = r.getrandbits
        w = (g(15), 0x8000 | g(14), 0xC000 | g(12), 0xD000 | g(12), 0xE000 | g(12), 0xF000 | g(10), 0xF400 | g(10),
             0xF800 | g(8) << 1, 0xFA00 | g(9))[f]
        if f in (0, 1, 3):
            w = (w & ~15) | r.choices(range(16), G_W)[0]
        if f == 0 and r.random() < 0.6:
            w &= ~0x30
        if undef and r.random() < 0.03:
            w = g(16)
        if decode(w)[0] is not None or undef:
            return w


def rnd_exec(r):
    # an injected word that a host could send: no relative jump, no call, no loop
    while True:
        w = rnd_word(r, False)
        i, _ = decode(w)
        if i["format"] in ("JF", "JP") or i["name"] == "call":
            continue
        return w & ~0x30 if i["format"] == "E" else w


def li16(rd, v):
    return [0xE000 | rd << 8 | (v & 255), 0xE400 | rd << 8 | (v >> 8 & 255)]


def host_sequence(r):
    # what a host does to a stopped engine: configuration by EXEC, then RUN or a few STEPs
    q = []
    for _ in range(r.choice((0, 0, 1, 1, 2, 3))):
        k = r.randrange(9)
        if k == 0:                                # timer period, compare, start
            p = r.randrange(3, 50)
            q += [0xE000 | p, 0xD200, 0xE000 | r.randrange(0, p + 1), 0xD280, r.choice((0xD800, 0xD880))]
        elif k == 1:                              # pin binding
            v = r.choice((r.randrange(16), r.randrange(32))) | r.getrandbits(1) << 5
            v |= (r.choice((r.randrange(16), r.randrange(32))) | r.getrandbits(1) << 5) << 8
            q += li16(0, v) + [0xFA00 | r.choice((3, 4)) << 3]
        elif k == 2:                              # condition cell, vector, watcher enable
            q += li16(0, r.getrandbits(14)) + [0xFA00 | r.choice((5, 6)) << 3]
            q += li16(0, r.getrandbits(9)) + [0xFA00 | r.choice((7, 8)) << 3, 0xDA80 | r.getrandbits(2) << 4]
        elif k == 3:                              # shifter
            v = r.choice((0, 0, 1, 3, 7, 7, 8, 15)) | r.getrandbits(4) << 4 | r.getrandbits(3) << 8
            q += li16(0, v) + [0xFA00]
        elif k == 4:
            q += li16(0, r.getrandbits(8)) + [0xFA00 | 1 << 3]
        elif k == 5:                              # timer configuration
            v = r.getrandbits(8) | r.choice((0, 0, 0, 1, 2)) << 8 | r.getrandbits(5) << 10
            q += li16(0, v) + [0xFA00 | 2 << 3]
        elif k == 6:
            q += [r.choice((0xD840, 0xD830, 0xD810))]
        elif k == 7:
            q += [rnd_exec(r)]
        else:
            q += li16(r.randrange(4), r.getrandbits(16))
    q = [("exec", w) for w in q]
    if r.random() < 0.5:
        q.append(("exec", 0xF400 | r.getrandbits(9)))
    return q + (r.choice(([("run",)], [("run",)], [("step",)] * r.randrange(1, 6))))


def random_scenario(seed, nclk, undef=False):
    r = random.Random(seed)
    c = Chip()
    e = seed % 4
    for k in range(4):
        if k != e:
            c.eng[k].step = lambda env: None
    eng = c.eng[e]
    c.mem = [rnd_word(r, undef) for _ in range(512)]
    c.lfsr, c.poly, c.lf_right = r.getrandbits(32), r.getrandbits(32), r.getrandbits(1)
    eng.R = [r.getrandbits(16) for _ in range(3)]
    eng.S, eng.P, eng.C, eng.T = r.getrandbits(16), r.randrange(4, 60), r.randrange(0, 40), r.getrandbits(16)
    smap = r.choice((0xE4, 0x00, 0x44, 0x24, None, None))
    if smap is not None:                      # engine e takes the slots of engine 0 in the named map
        m = 0
        for s in range(4):
            o = (smap >> (2 * s)) & 3
            m |= ((e if o == 0 else (0 if o == e else o)) & 3) << (2 * s)
        c.slotmap = m
    pgrant = r.choice((0.05, 0.25, 0.5, 0.9))
    ptog = [r.choice((0.5, 0.25, 0.12, 0.06, 0.03)) for _ in range(16)]
    limit = r.choice((25, 60, 150))
    pads = r.getrandbits(16)
    q = host_sequence(r)
    xm_age = park = 0
    seen = (None, None)
    for t in range(nclk):
        for k in range(16):
            if r.random() < ptog[k]:
                pads ^= 1 << k
        if smap is None:
            c.slotmap = (e if r.random() < pgrant else (e + 1) & 3) * 0x55
        if r.random() < 0.05 and len(c.host_in[e]) < 3:
            c.host_in[e].append(r.getrandbits(8))
        if r.random() < 0.02:
            c.tx_block[e] = not c.tx_block[e]
        if r.random() < 0.01:
            c.sig_host = r.getrandbits(8)
        if eng.PC in seen:
            park += 1
        else:
            seen, park = (seen[1], eng.PC), 0
        idle = not eng.run and not eng.xm
        xm_age = xm_age + 1 if eng.xm else 0
        x = r.random()
        if x < 0.0015:
            c.pulse(e, "rst")
            q = host_sequence(r)
        elif x < 0.004 and "_tr" in eng.__dict__:
            inject(eng, clr_ovr=r.random() < 0.7, clr_ht=r.random() < 0.5, rx_ovf=r.random() < 0.2)
        elif eng.xm:
            if r.random() < (0.2 if xm_age > 12 else 0.06):
                c.pulse(e, "stop")                # cancel an injected word that waits (D045, D069)
        elif not idle:
            if x < 0.008 or park > limit:
                c.pulse(e, "stop")
                park = 0
                if not q:
                    q = host_sequence(r)
        elif q:
            a = q.pop(0)
            c.pulse(e, *a)
            if a[0] == "run" and (eng.halted or eng.trap) and "_tr" in eng.__dict__:
                inject(eng, clr_ht=True)
        elif r.random() < 0.3:
            q = host_sequence(r)
        c.step(pads & 255, pads >> 8)
    return c
