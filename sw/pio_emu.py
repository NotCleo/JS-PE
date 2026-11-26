# Cycle-stepped model of one RP2040/RP2350 PIO state machine (subset used by the host PIO link): 16-bit opcodes,
# integer clock divider, delays, stalls, 4-deep FIFOs, no autopush/autopull, no side-set, no IRQ.
# Inputs are passed in already synchronized (the 2-FF input synchronizer is modelled by the caller, per GPIO).
from collections import deque


class SM:
    def __init__(self, prog, div=1, in_base=0, out_base=0, set_base=0, jmp_pin=0, out_count=1, set_count=1,
                 in_shift_right=True, out_shift_right=True, push_thresh=32, pull_thresh=32, wrap_target=0,
                 wrap=None, levels=None):
        assert 1 <= div == int(div)
        self.prog, self.div = list(prog), int(div)
        self.in_base, self.out_base, self.set_base, self.jmp_pin = in_base, out_base, set_base, jmp_pin
        self.out_count, self.set_count = out_count, set_count
        self.isr_right, self.osr_right = in_shift_right, out_shift_right
        self.push_thresh, self.pull_thresh = push_thresh, pull_thresh
        self.wrap_target, self.wrap = wrap_target, len(self.prog) - 1 if wrap is None else wrap
        self.levels = dict(levels or {})      # GPIO -> level driven by out/set
        self.pc = self.wrap_target
        self.x = self.y = self.isr = self.osr = 0
        self.isr_cnt, self.osr_cnt = 0, 32     # OSR starts empty
        self.delay = self.cnt = 0
        self.txf, self.rxf = deque(), deque()
        self.stalled = False

    def put(self, w):
        assert len(self.txf) < 4
        self.txf.append(w & 0xFFFFFFFF)

    def _next(self):
        self.pc = self.wrap_target if self.pc == self.wrap else (self.pc + 1) & 31

    def _pins(self, base, n, v):
        for k in range(n):
            self.levels[base + k] = (v >> k) & 1

    def step(self, inp):
        # one system clock; inp(gpio) -> synchronized input level
        self.cnt += 1
        if self.cnt < self.div:
            return
        self.cnt = 0
        if self.delay:
            self.delay -= 1
            return
        op = self.prog[self.pc]
        jumped = self.execute(op, inp)
        self.stalled = jumped is None
        if jumped is None:
            return
        if not jumped:
            self._next()
        self.delay = (op >> 8) & 31

    def exec_now(self, op, inp=lambda g: 1):
        # SMx_INSTR: runs at once, delay ignored
        self.execute(op, inp)

    def execute(self, op, inp):
        # True: pc set by a jump, False: advance, None: stall
        code, lo = op >> 13, op & 0xFF
        if code == 0:
            cond, addr = lo >> 5, lo & 31
            if cond == 0:
                t = True
            elif cond == 1:
                t = self.x == 0
            elif cond == 2:
                t = self.x != 0
                self.x = (self.x - 1) & 0xFFFFFFFF
            elif cond == 3:
                t = self.y == 0
            elif cond == 4:
                t = self.y != 0
                self.y = (self.y - 1) & 0xFFFFFFFF
            elif cond == 5:
                t = self.x != self.y
            elif cond == 6:
                t = inp(self.jmp_pin) == 1
            else:
                t = self.osr_cnt < self.pull_thresh
            if t:
                self.pc = addr
            return t
        if code == 1:
            pol, src, idx = lo >> 7 & 1, lo >> 5 & 3, lo & 31
            assert src in (0, 1), "wait irq not modelled"
            g = idx if src == 0 else self.in_base + idx
            return False if inp(g) == pol else None
        if code in (2, 3):
            sd, n = lo >> 5 & 7, (lo & 31) or 32
            m = (1 << n) - 1
            if code == 2:
                v = {0: sum(inp(self.in_base + k) << k for k in range(n)), 1: self.x, 2: self.y, 3: 0}[sd] & m
                if self.isr_right:
                    self.isr = ((self.isr >> n) | (v << (32 - n))) & 0xFFFFFFFF
                else:
                    self.isr = ((self.isr << n) | v) & 0xFFFFFFFF
                self.isr_cnt = min(32, self.isr_cnt + n)
                return False
            if self.osr_right:
                v = self.osr & m
                self.osr = self.osr >> n if n < 32 else 0
            else:
                v = self.osr >> (32 - n) & m
                self.osr = (self.osr << n) & 0xFFFFFFFF
            self.osr_cnt = min(32, self.osr_cnt + n)
            assert sd in (0, 1, 2, 3), "out dest not modelled"
            if sd == 0:
                self._pins(self.out_base, n if n < self.out_count else self.out_count, v)
            elif sd == 1:
                self.x = v
            elif sd == 2:
                self.y = v
            return False
        if code == 4:
            cond, block = lo >> 6 & 1, lo >> 5 & 1
            if lo & 0x80:
                if cond and self.osr_cnt < self.pull_thresh:
                    return False
                if not self.txf:
                    if block:
                        return None
                    self.osr = self.x
                else:
                    self.osr = self.txf.popleft()
                self.osr_cnt = 0
                return False
            if cond and self.isr_cnt < self.push_thresh:
                return False
            if len(self.rxf) >= 4:
                if block:
                    return None
            else:
                self.rxf.append(self.isr)
            self.isr, self.isr_cnt = 0, 0
            return False
        if code == 5:
            dest, mop, src = lo >> 5 & 7, lo >> 3 & 3, lo & 7
            v = {1: self.x, 2: self.y, 3: 0, 6: self.isr, 7: self.osr}[src]
            v = ~v & 0xFFFFFFFF if mop == 1 else v
            assert dest in (1, 2, 6, 7) and mop in (0, 1), "mov form not modelled"
            if dest == 1:
                self.x = v
            elif dest == 2:
                self.y = v
            elif dest == 6:
                self.isr, self.isr_cnt = v, 0
            else:
                self.osr, self.osr_cnt = v, 0
            return False
        if code == 7:
            dest, v = lo >> 5 & 7, lo & 31
            assert dest in (0, 1, 2), "set dest not modelled"
            if dest == 0:
                self._pins(self.set_base, self.set_count, v)
            elif dest == 1:
                self.x = v
            else:
                self.y = v
            return False
        raise AssertionError("irq not modelled")
