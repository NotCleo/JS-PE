# Fake MicroPython rp2 / machine / uctypes / time for running host/jspe.py's PIO link in CPython: asm_pio emits
# RP2040-datasheet opcodes, StateMachine runs them on sw/pio_emu.py, DMA fills the ring, World clocks it with the ISS.
import os
import sys
import types
from pio_emu import SM

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "host"))

CONST = dict(pins=0, x=1, y=2, null=3, pindirs=4, pc=5, isr=6, osr=7, gpio=0, pin=6, irq=2, not_x=1, x_dec=2,
             not_y=3, y_dec=4, x_not_y=5, not_osre=7, block=0x21, noblock=0x01, iffull=0x40, ifempty=0x40)


class _Ins:
    def __init__(self, a, w):
        self.a, self.i = a, len(a.words)
        a.words.append(w)

    def __getitem__(self, d):
        assert 0 <= d < 32
        self.a.words[self.i] |= d << 8
        return self


class _Asm:
    def __init__(self):
        self.labels, self.words, self.wrap_target, self.wrap = {}, [], 0, None

    def ns(self):
        a = self

        def e(w):
            return _Ins(a, w)

        def label(n):
            a.labels[n] = len(a.words)

        def jmp(cond, lab=None):
            if lab is None:
                cond, lab = 0, cond
            return e(cond << 5 | a.labels.get(lab, 0))

        def wait(pol, src, idx):
            return e(0x2000 | pol << 7 | {0: 0, 6: 1, 2: 2}[src] << 5 | idx)

        def push(v=0, v2=0):
            v |= v2
            return e(0x8000 | ((v | 0x20) if not v & 1 else v) & 0x60)

        def pull(v=0, v2=0):
            v |= v2
            return e(0x8080 | ((v | 0x20) if not v & 1 else v) & 0x60)

        def wrap_target():
            a.wrap_target = len(a.words)

        def wrap():
            a.wrap = len(a.words) - 1

        return dict(CONST, label=label, jmp=jmp, wait=wait, push=push, pull=pull, wrap=wrap,
                    wrap_target=wrap_target, nop=lambda: e(0xA042),
                    in_=lambda s, n: e(0x4000 | s << 5 | n & 31), out=lambda d, n: e(0x6000 | d << 5 | n & 31),
                    mov=lambda d, s: e(0xA000 | d << 5 | s), set=lambda d, v: e(0xE000 | d << 5 | v))


class Prog:
    def __init__(self, words, kw, wt, wr):
        self.words, self.kw, self.wrap_target, self.wrap = words, kw, wt, wr


def asm_pio(**kw):
    def dec(f):
        g, saved = f.__globals__, dict(f.__globals__)
        try:
            a = _Asm()
            for _ in range(2):                    # pass 1 resolves forward labels
                a.words = []
                g.update(a.ns())
                f()
        finally:
            g.clear()
            g.update(saved)
        return Prog(a.words, kw, a.wrap_target, a.wrap)
    return dec


def assemble1(text):
    a = _Asm()
    eval(text, a.ns())
    return a.words[0]


class PIO:
    OUT_LOW, OUT_HIGH, IN_LOW, IN_HIGH, SHIFT_LEFT, SHIFT_RIGHT = 2, 3, 0, 1, 0, 1
    bases = {}

    def __init__(self, n):
        self.n = n

    def gpio_base(self, b=None):
        if b is not None:
            PIO.bases[self.n] = int(b)
        return PIO.bases.get(self.n, 0)


class StateMachine:
    def __init__(self, sid, prog=None, freq=-1, **kw):
        self.sid = sid
        if prog is not None:
            self.init(prog, freq, **kw)

    def init(self, prog, freq=-1, in_base=0, out_base=0, set_base=0, jmp_pin=0, **_):
        w = World.cur
        k = prog.kw
        assert w.sys_hz % freq == 0, (w.sys_hz, freq)
        lv = {}
        for key, base in (("set_init", set_base), ("out_init", out_base)):
            if key in k:
                lv[int(base)] = 1 if k[key] == PIO.OUT_HIGH else 0
        self.sm = SM(prog.words, div=w.sys_hz // freq, in_base=int(in_base), out_base=int(out_base),
                     set_base=int(set_base), jmp_pin=int(jmp_pin),
                     in_shift_right=k.get("in_shiftdir", 0) == 1, out_shift_right=k.get("out_shiftdir", 0) == 1,
                     wrap_target=prog.wrap_target, wrap=prog.wrap, levels=lv)
        self.on = False
        w.sms[self.sid] = self

    def active(self, v=None):
        if v is not None:
            self.on = bool(v)
        return self.on

    def put(self, v, shift=0):
        w = World.cur
        for x in (v if hasattr(v, "__len__") else [v]):
            n = 0
            while len(self.sm.txf) >= 4:
                w.step()
                n += 1
                if n > w.put_limit:
                    raise TimeoutError("sm.put blocked")
            self.sm.put(x << shift)

    def get(self):
        w = World.cur
        while not self.sm.rxf:
            w.step()
        return self.sm.rxf.popleft()

    def tx_fifo(self):
        World.cur.run(8)                          # polling takes time
        return len(self.sm.txf)

    def rx_fifo(self):
        return len(self.sm.rxf)

    def exec(self, text):
        self.sm.exec_now(assemble1(text))


class DMA:
    n = 0

    def __init__(self):
        self.channel = DMA.n
        DMA.n += 1
        self._count = 0
        World.cur.dmas.append(self)

    def pack_ctrl(self, **kw):
        return kw

    def config(self, read, write, count, ctrl, trigger=False):
        self.read, self.write_addr, self._count, self.reload, self.ctrl = read, write, count, count, ctrl
        k = ctrl["treq_sel"]
        self.sid = (k // 8) * 4 + k % 4
        assert k % 8 >= 4 and read == 0x50200000 + 0x100000 * (k // 8) + 0x20 + 4 * (k % 4) + 3

    @property
    def count(self):
        World.cur.run(World.cur.poll_cycles)     # a Python poll takes time
        return self._count

    def service(self, w):
        s = w.sms.get(self.sid)
        if not s or not s.sm.rxf or not self._count:
            return
        R = 1 << self.ctrl["ring_size"]
        buf, base = w.mem_at(self.write_addr)
        buf[self.write_addr - base] = s.sm.rxf.popleft() >> 24 & 0xFF
        self.write_addr = (self.write_addr & ~(R - 1)) | ((self.write_addr + 1) & (R - 1))
        self._count -= 1
        if self._count == 0 and self.ctrl.get("chain_to") == self.channel:
            self._count = self.reload


class World:
    # chip ISS at clk = sys / 2; GPIO20 -> ui_in[3]; uo_out[4] -> GPIO37, uo_out[5] -> GPIO38 (+ pad delay, 2-FF sync)
    cur = None

    def __init__(self, chip, sys_hz=96_000_000, pad=0, ui=lambda c: 0x80, poll_cycles=96, put_limit=2_000_000):
        World.cur = self
        DMA.n = 0
        self.chip, self.sys_hz, self.pad, self.ui = chip, sys_hz, pad, ui
        self.poll_cycles, self.put_limit = poll_cycles, put_limit
        self.sms, self.dmas, self.bufs = {}, [], []
        self.cycle = 0
        self.hist = [(1, 0)] * (pad + 3)
        self.on_clk = None
        self.time = types.SimpleNamespace(ticks_ms=lambda: self.cycle * 1000 // self.sys_hz,
                                          ticks_diff=lambda a, b: a - b, sleep_us=self.sleep_us,
                                          sleep_ms=lambda ms: self.sleep_us(1000 * ms))

    def sleep_us(self, us):
        self.run(us * self.sys_hz // 1_000_000)

    def addressof(self, buf):
        a = 0x20000000 + 0x123 + sum(len(b) + 0x40 for b in self.bufs)
        self.bufs.append(buf)
        return a

    def mem_at(self, addr):
        a = 0x20000000 + 0x123
        for b in self.bufs:
            if a <= addr < a + len(b):
                return b, a
            a += len(b) + 0x40
        raise AssertionError("DMA write outside known buffers")

    def tx_level(self):
        s = self.sms.get(0)
        return s.sm.levels.get(20, 1) if s else 1

    def step(self):
        c = self.chip
        self.hist.append(((c.uo >> 4) & 1, (c.uo >> 5) & 1))
        self.hist.pop(0)
        h_tx, h_rdy = self.hist[0]                  # pad delay + 2-cycle input synchronizer
        lv = {37: h_tx, 38: h_rdy}
        for s in list(self.sms.values()):
            if s.on:
                s.sm.step(lambda g: lv.get(g, 1))
        for d in self.dmas:
            d.service(self)
        if self.cycle & 1:
            ui = self.ui(c) & ~8 | self.tx_level() << 3
            c.step(ui, (~c.uio_oe | c.uio_out) & 0xFF)
            if self.on_clk:
                self.on_clk(c)
        self.cycle += 1

    def run(self, n):
        for _ in range(n):
            self.step()


def install(chip, **kw):
    # fake modules into sys.modules, a fresh World; returns (world, jspe) with jspe.time on the world clock
    w = World(chip, **kw)
    rp2 = types.ModuleType("rp2")
    rp2.asm_pio, rp2.PIO, rp2.StateMachine, rp2.DMA = asm_pio, PIO, StateMachine, DMA
    machine = types.ModuleType("machine")
    machine.Pin = type("Pin", (int,), {})
    machine.freq = lambda *a: w.sys_hz
    uct = types.ModuleType("uctypes")
    uct.addressof = w.addressof
    sys.modules.update(rp2=rp2, machine=machine, uctypes=uct)
    import jspe
    jspe._progs = None
    jspe.time = w.time
    return w, jspe
