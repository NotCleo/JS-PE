# Fault-injection API (docs/spec/programs/fault_design.md) for the -D FAULTS=1 program variants; MicroPython and
# CPython. Word: ARMED[15] TYPE[14:12] POS[11:8] ARG[7:0] at FAULT_W + engine; the program writes 0 once it fired.
FAULT_W = 475           # cleo_mem.inc FAULT_W (checked by sw/tests/test_ei1.py)
ARMED = 0x8000

# program -> {type: (name, pos max, arg min, arg max)}
SPEC = {
    "uart_tx": {1: ("parity", 0, 0, 0), 2: ("stop0", 0, 0, 0), 3: ("break", 0, 2, 255)},
    "i2c_tgt": {1: ("nack", 15, 1, 255), 2: ("stretch", 15, 1, 255), 3: ("ignore", 15, 0, 0), 4: ("stuck", 15, 1, 255)},
    "spi_tgt": {1: ("flip", 15, 1, 255), 2: ("short", 15, 1, 7), 3: ("long", 15, 1, 8)},
    "can": {1: ("crc", 0, 1, 255)},
}


def word(prog, typ, pos=0, arg=0):
    name, pmax, amin, amax = SPEC[prog][typ]
    if not 0 <= pos <= pmax or not amin <= arg <= amax:
        raise ValueError("%s %s: pos 0..%d, arg %d..%d" % (prog, name, pmax, amin, amax))
    return ARMED | typ << 12 | pos << 8 | arg


def check(w):
    # only 0 (disarm) or an armed word may be written: an unarmed non-zero word has no defined meaning
    if w != 0 and not w & ARMED:
        raise ValueError("unarmed non-zero fault word 0x%04x" % w)
    return w


def arm(link, eng, prog, typ, pos=0, arg=0):
    link.wrmem(FAULT_W + eng, [check(word(prog, typ, pos, arg))])


def disarm(link, eng):
    link.wrmem(FAULT_W + eng, [0])


def fired(link, eng):
    return link.rdmem(FAULT_W + eng, 1)[0] == 0


class Rng:
    # xorshift32: the same sequence on the PC and on the RP2350 for a given seed
    def __init__(self, seed):
        self.s = (seed & 0xFFFFFFFF) or 0x9E3779B9

    def next(self):
        s = self.s
        s ^= (s << 13) & 0xFFFFFFFF
        s ^= s >> 17
        s ^= (s << 5) & 0xFFFFFFFF
        self.s = s
        return s

    def below(self, n):
        return self.next() % n

    def between(self, a, b):
        return a + self.below(b - a + 1)


def campaign(seed, prog, ntrans, nfaults, types=None, pos_max=None, arg_range=None):
    # nfaults distinct transactions out of ntrans, each with (type, pos, arg) drawn within SPEC (or the narrower
    # pos_max / arg_range {type: (lo, hi)} a test bench needs to observe the fault); returns [(trans, word)] sorted
    r = Rng(seed)
    types = types or sorted(SPEC[prog])
    picked = []
    while len(picked) < nfaults:
        k = r.below(ntrans)
        if k not in picked:
            picked.append(k)
    out = []
    for k in sorted(picked):
        typ = types[r.below(len(types))]
        name, pmax, amin, amax = SPEC[prog][typ]
        if pos_max is not None:
            pmax = min(pmax, pos_max)
        if arg_range and typ in arg_range:
            amin, amax = arg_range[typ]
        out.append((k, word(prog, typ, r.between(0, pmax), r.between(amin, amax))))
    return out
