# Cross-check of sw/pio_emu.py against an independent PIO emulator (rp2040-pio-emulator, Apache-2.0), not CI:
# host/jspe.py's link TX/RX programs run on both; pin waveform, instruction issue trace (cycle, pc, incl. stalls)
# and RX FIFO must agree cycle for cycle. rp2040-pio-emulator needs pytest >= 9, so it gets its own venv:
#   python3 -m venv build/pioemu-venv && build/pioemu-venv/bin/pip install rp2040-pio-emulator==0.88.0
#   build/pioemu-venv/bin/python scripts/pio_check_xemu.py
# rp2040-pio-emulator has no in_base (WAIT PIN n / IN PINS read GPIO n from 0), so inputs sit on GPIO 0 in both.
import os
import random
import sys
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "sw"), os.path.join(ROOT, "sw", "tests")]
from pioemu import emulate, State   # noqa: E402
from pio_emu import SM              # noqa: E402
import fake_rp2                     # noqa: E402

TXP = 1                              # H_RX output pin; input (H_RDY or H_TX) on GPIO 0
fails = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        fails.append(what)


def ours(words, prog, inp, n, tx):
    sm = SM(prog, out_base=TXP, set_base=TXP, in_base=0, jmp_pin=0, levels={TXP: 1} if tx else {})
    sm.txf.extend(words)
    sm.trace, wave, rxf = [], [], []
    for t in range(n):
        sm.step(lambda g: inp(t) if g == 0 else 1)
        wave.append(sm.levels.get(TXP, 1))
    return wave, sm.trace, [w for w in sm.rxf]


def theirs(words, prog, inp, n, tx):
    def src(clock: int) -> int:
        return inp(clock)
    st = State(transmit_fifo=deque(words), pin_directions=1 << TXP if tx else 0, pin_values=1 << TXP if tx else 0)
    wave, trace, last = [None] * n, [], st
    for prev, cur in emulate(prog, stop_when=lambda op, s: s.clock >= n, initial_state=st, input_source=src,
                             shift_osr_right=True, shift_isr_right=True, out_base=TXP, out_count=1,
                             set_base=TXP, set_count=1, jmp_pin=0):
        trace.append((prev.clock, prev.program_counter))
        for c in range(prev.clock, min(cur.clock, n)):
            wave[c] = cur.pin_values >> TXP & 1
        last = cur
    return wave, trace, list(last.receive_fifo)


def compare(name, words, prog, inp, n, tx):
    a, b = ours(words, prog, inp, n, tx), theirs(words, prog, inp, n, tx)
    if tx:
        bad = next((t for t in range(n) if a[0][t] != b[0][t]), None)
        check(bad is None, "%s: H_RX waveform, %d cycles%s" % (name, n, "" if bad is None else " (first diff %d)" % bad))
    k = next((i for i, (x, y) in enumerate(zip(a[1], b[1])) if x != y), None)
    check(k is None and len(a[1]) >= len(b[1]) - 1,
          "%s: issue trace, %d issues%s" % (name, len(b[1]), "" if k is None else " (first diff %s)" % (a[1][k:k + 2],)))
    if not tx:
        check(a[2] == b[2] and a[2], "%s: RX FIFO %s" % (name, [hex(w >> 24) for w in a[2]]))


def frame(b, stop=1, cyc=8):
    return [0] * cyc + sum(([b >> j & 1] * cyc for j in range(8)), []) + [stop] * cyc


def main():
    _, jspe = fake_rp2.install(None)
    tx, rx = jspe.pio_progs()
    rnd = random.Random(1)
    data = [0x00, 0xFF, 0x1B, 0x55, 0xA5, 0x80, 0x01, 0x3C]
    compare("TX forced, H_RDY high", [b << 1 | 1 for b in data], tx.words, lambda t: 1, 900, True)
    compare("TX unforced, H_RDY low", [b << 1 for b in data], tx.words, lambda t: 0, 900, True)
    pulses = sorted(rnd.sample(range(2000), 60))
    rdy = lambda t: sum(p <= t for p in pulses) & 1
    words = [b << 1 | rnd.randint(0, 1) for b in data * 3]
    compare("TX mixed, H_RDY toggling (60 random edges)", words, tx.words, rdy, 2500, True)
    for lead in range(8):
        line = [1] * (3 + lead) + sum((frame(b) for b in data[:3]), []) + frame(0x5A) + [1] * 20
        compare("RX phase %d" % lead, [], rx.words, lambda t, L=line: L[t] if t < len(L) else 1, len(line), False)
    line = [1] * 4 + frame(0x81) + [0] * 150 + [1] * 15 + frame(0x22, stop=0) + [1] * 15 + frame(0x7E) + [1] * 20
    compare("RX break + framing error", [], rx.words, lambda t: line[t] if t < len(line) else 1, len(line), False)
    print("%d failures" % len(fails))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
