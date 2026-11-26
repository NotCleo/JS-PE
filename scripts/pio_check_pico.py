# Validation of sw/pio_emu.py against Raspberry Pi's pico-examples UART programs (not CI; needs network once).
# (1) pico uart_tx / uart_rx on pio_emu reproduce the timing their comments document; (2) our host-link TX
# (host/jspe.py) with forced bytes gives the pico TX waveform cycle for cycle, our RX = pico uart_rx minus `irq`.
# Usage: .venv/bin/python scripts/pio_check_pico.py [dir with uart_tx.pio, uart_rx.pio]
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "sw"), os.path.join(ROOT, "sw", "tests")]
import adafruit_pioasm   # noqa: E402
from pio_emu import SM   # noqa: E402
import fake_rp2          # noqa: E402

URL = "https://raw.githubusercontent.com/raspberrypi/pico-examples/master/pio/%s/%s.pio"
fails = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        fails.append(what)


def programs(path):
    text = re.sub(r"% c-sdk \{.*?%\}", "", open(path).read(), flags=re.S)
    text = "\n".join(ln for ln in text.splitlines() if not ln.startswith(".pio_version"))
    out = {}
    for blk in text.split(".program ")[1:]:
        name, body = blk.split("\n", 1)
        out[name.strip()] = adafruit_pioasm.Program(body)
    return out


def fetch(d):
    os.makedirs(d, exist_ok=True)
    for n in ("uart_tx", "uart_rx"):
        p = os.path.join(d, n + ".pio")
        if not os.path.exists(p):
            urllib.request.urlretrieve(URL % (n, n), p)


def tx_wave(sm, words, n, inp=lambda g: 0):
    q, wave = list(words), []
    for _ in range(n):
        while q and len(sm.txf) < 4:
            sm.txf.append(q.pop(0))
        sm.step(inp)
        wave.append(sm.levels[1])
    return wave


def frames(wave, cyc=8):
    # (start cycle, byte) from exact 8-cycle bit cells; asserts every cell is constant
    out, t = [], 0
    while 0 in wave[t:]:
        t = wave.index(0, t)
        if t + 10 * cyc > len(wave):
            break
        cells = [wave[t + cyc * j:t + cyc * (j + 1)] for j in range(10)]
        if not all(len(set(c)) == 1 for c in cells[:9]) or cells[9][0] != 1:
            return out + [(t, None)]
        out.append((t, sum(cells[j + 1][0] << j for j in range(8))))
        t += 9 * cyc + 1
    return out


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build", "pico-examples")
    fetch(d)
    ptx = programs(os.path.join(d, "uart_tx.pio"))["uart_tx"]
    prx = programs(os.path.join(d, "uart_rx.pio"))["uart_rx"]
    data = [0x00, 0xFF, 0x55, 0xA5, 0x1B, 0x80, 0x01]

    # pico uart_tx: "assert start bit for 8 clocks", "each loop iteration is 8 cycles", stop = pull side 1 [7]
    ss = ptx.pio_kwargs
    sm = SM(ptx.assembled, out_base=1, sideset_base=1, sideset_count=ss["sideset_pin_count"],
            sideset_opt=ss.get("sideset_enable", False), levels={1: 1})
    pw = tx_wave(sm, list(data), 900)
    fr = frames(pw)
    check([b for _, b in fr] == data, "pico uart_tx: 8N1 bytes with 8-cycle bit cells")
    check([b - a for (a, _), (b, _) in zip(fr, fr[1:])] == [80] * (len(data) - 1),
          "pico uart_tx: back to back = 80 cycles per byte (stop bit 8 cycles)")
    check(pw[:3] == [1, 1, 1] and pw[-100:] == [1] * 100, "pico uart_tx: idle high (side 1 while pull stalls)")

    # our TX, every byte forced: the same waveform, shifted
    _, jspe = fake_rp2.install(None)
    tx, rx = jspe.pio_progs()
    sm = SM(tx.words, out_base=1, set_base=1, in_base=0, levels={1: 1})
    ow = tx_wave(sm, [b << 1 | 1 for b in data], 900)
    a, b = ow.index(0), pw.index(0)
    check(ow[a:a + 700] == pw[b:b + 700] and 0 in pw[b + 500:b + 700],
          "host TX (forced) == pico uart_tx waveform cycle for cycle from the first start bit (ours %d cycles "
          "earlier: no pull delay)" % (b - a))
    sm = SM(tx.words, out_base=1, set_base=1, in_base=0, levels={1: 1})
    fr = frames(tx_wave(sm, [b << 1 for b in data], 900))
    check([b - a for (a, _), (b, _) in zip(fr, fr[1:])] == [81] * (len(data) - 1),
          "host TX (H_RDY low, unforced): 81 cycles per byte")

    # pico uart_rx: first `in` 12 cycles after the wait sees the start bit, then every 8; stop by `jmp pin`
    sm = SM(prx.assembled, in_base=0, jmp_pin=0, sm_index=1)
    sm.trace = []
    e = 37
    line = [1] * e + [0] * 8 + sum(([0x5A >> j & 1] * 8 for j in range(8)), []) + [1] * 30
    for t in range(len(line)):
        sm.step(lambda g: line[t])
    ins = [t for t, pc in sm.trace if prx.assembled[pc] >> 13 == 2]
    wt = [t for t, pc in sm.trace if pc == 0]
    check(ins == [e + 12 + 8 * j for j in range(8)] and wt[-1] >= e,
          "pico uart_rx: samples at 12 + 8j cycles after the start edge (comment: 12 cycles incl wait, set)")
    check([w >> 24 for w in sm.rxf] == [0x5A] and sm.irq == 0, "pico uart_rx: byte pushed, no irq")
    sm = SM(prx.assembled, in_base=0, jmp_pin=0, sm_index=1)
    line = [1] * 5 + [0] * 200 + [1] * 20 + [0] * 8 + sum(([0x33 >> j & 1] * 8 for j in range(8)), []) + [1] * 20
    for t in range(len(line)):
        sm.step(lambda g: line[t])
    check([w >> 24 for w in sm.rxf] == [0x33] and sm.irq == 1 << 5,
          "pico uart_rx: break -> irq 4 rel (sm 1 -> flag 5), no push, waits for idle, next byte ok")

    # our RX = pico uart_rx without `irq 4 rel` (jump targets shift by one)
    p = list(prx.assembled)
    i = next(j for j, w in enumerate(p) if w >> 13 == 6)
    exp = [w - 1 if w >> 13 == 0 and (w & 31) > i else w for w in p[:i] + p[i + 1:]]
    check(rx.words == exp, "host RX == pico uart_rx minus irq, opcode for opcode")
    # pico TX into our RX (loopback through the 2-cycle synchronizer)
    sm = SM(rx.words, in_base=0, jmp_pin=0)
    got = []
    for t in range(len(pw)):
        v = pw[t - 2] if t >= 2 else 1
        sm.step(lambda g: v)
        while sm.rxf:
            got.append(sm.rxf.popleft() >> 24)
    check(got == data, "pico uart_tx -> host RX: all bytes")
    print("%d failures" % len(fails))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
