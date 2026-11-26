# T6 (D100): assembler bound >= ISS-measured arming delay for every guarded instruction of every paper program.
# Part 1 (model): random pad toggles and host bytes with event spacing down to 0 clk, slot maps 0xE4, 0x00, 0x44,
# 0x24 x 4 slot phases; each step is checked against walk_bound for the path taken at the measured wait.
# Part 2 (contract): every `.assume armed` holds and every stated bound covers the measured delay in the
# in-contract worst cases (header rates, minimum spacings, +-2 %, jitter).
import bisect
import os
import random
import pytest
from harness import PROG, arm_steps, boot, simulate, g_map, record
from jspe_asm import Assembler, slot_gap, slot_chain, decode
from models import UartSender, ManchesterSender, SpiController
import test_bounds as tb
from test_first import run_spi_tgt
from test_i2c import i2c_run
from models import I2cControllerModel, I2cTargetModel

MAPS = (0xE4, 0x00, 0x44, 0x24)
FULL = os.environ.get("JSPE_SWEEP_FULL") == "1"
# program, .equ overrides, engines measured on map 0x00 (the only engine with slots is E0)
PROGS = [("uart_tx", {"ENG": 0}), ("uart_rx", {"ENG": 0}), ("spi_ctrl", {"ENG": 0}), ("spi_tgt", {"ENG": 0}),
         ("i2c_ctrl", {"ENG": 0}), ("i2c_tgt", {"ENG": 0}), ("ws2812", {"ENG": 0}),
         ("manchester", {"ENG_TX": 0, "ENG_RX": 1}), ("manchester", {"ENG_TX": 1, "ENG_RX": 0}),
         ("manchester", {"ENG_TX": 0, "ENG_RX": 1, "CVCHK": 0}),
         ("can", {"ENG_TX": 0, "ENG_RX": 1}), ("can", {"ENG_TX": 1, "ENG_RX": 0}),
         ("meter", {"ENG": 0}), ("meter", {"ENG": 0, "EXT": 1}), ("freq", {"ENG": 0, "GATE": 2000}),
         ("autobaud", {"ENG": 0}), ("nec", {"ENG": 0, "CLK_HZ": 200000, "PRE": 0}),
         ("jtag_ctrl", {"ENG": 0})]


def never(img, a):
    # `@never` waits end only by a watcher: no firing, no step to measure
    return decode(img.words[a])[1].get("g") == 7


def build(name, m, defs):
    src = open(os.path.join(PROG, name + ".s")).read()
    src += "\n.slots %s\n" % ", ".join(str((m >> (2 * s)) & 3) for s in range(4))
    return Assembler([(name + ".s", src)], {k: str(v) for k, v in defs.items()}).run()


def stimulus(seed, engines):
    # pads: random bit flips, probability per clk cycling 1, 0.3, 0.05, 0.005 every 700 clk; host: random bursts
    r = random.Random(seed)
    st = {"ui": r.getrandbits(8) | 0x08, "uio": r.getrandbits(8)}

    def inputs(t, c):
        p = (1.0, 0.3, 0.05, 0.005)[int(t // 700) % 4]
        if r.random() < p:
            st["ui"] ^= 1 << r.randrange(8)
        if r.random() < p:
            st["uio"] ^= 1 << r.randrange(8)
        if r.random() < p / 4:
            e = r.choice(engines)
            if len(c.host_in[e]) < 8:
                c.host_in[e] += [r.getrandbits(8) for _ in range(r.randint(1, 4))]
        return st["ui"] | 0x08, st["uio"]
    return inputs


def stress(name, img, ph, seed, live):
    # protocol traffic far outside the header rates (events down to every clock), for the G random pads miss
    r = random.Random(seed)
    if name == "spi_tgt":
        T = r.choice((2, 3, 4, 6, 25))
        return run_spi_tgt(img, T, [r.randrange(8) for _ in range(3)], 3, ph, phase=r.random())[0]
    if name == "i2c_tgt":
        c = boot(img, ph)
        c.host_in[0] += [r.getrandbits(8) for _ in range(6)]
        th = r.choice((3, 8, 60))
        script = [("S",), ("W", 0x84), ("W", r.getrandbits(8)), ("S",), ("W", 0x85), ("R", 0), ("R", 0), ("R", 1),
                  ("P",), ("S",), ("W", 0x84), ("W", 0x5A), ("P",)]
        i2c_run(c, I2cControllerModel(script, th, th + 2), 30000, r.choice((0, 3)))
        return c
    if name == "i2c_ctrl":
        c = boot(img, ph)
        t = I2cTargetModel(0x50, tx=[0x9C, 0x42, 0x17],
                           stretch={("wr", r.randrange(8)): r.choice((3, 40)), ("addr", 8): r.choice((0, 400, 3000))})
        for st in [(7, 0xA0), (6, 0x11), (14, 0x22), (7, 0xA1), (2, 0xFF), (14, 0xFF), (7, 0xB0), (14, 0x00)]:
            c.host_in[0] += list(st)
        i2c_run(c, t, 60000, r.choice((0, 15)))
        return c
    if name == "manchester":
        c = boot(img, ph)
        bit = r.choice((6, 10, 22, 50))
        jit = r.choice((0.0, bit / 4))
        tx = [e for e in img.engines if img.engines[e]["pads"]][0]
        c.tx_block[1 - tx] = True                      # host FIFO full: the RX data loop ends, push drops (OVR)
        c.host_in[tx] += [0x55, 0xD5] + [r.getrandbits(8) for _ in range(4)]
        first = ManchesterSender(50, [0x55, 0x55, 0xD5] + [r.getrandbits(8) for _ in range(10)], t0=c.cycle + 30)
        ss, t = [first], first.end + 4 * 50
        for _ in range(4):
            data = [0x55] * r.randint(1, 3) + [0xD5] + [r.getrandbits(8) for _ in range(r.randint(1, 8))]
            sd = ManchesterSender(bit * r.uniform(0.9, 1.1), data, t0=t, jitter=(lambda k: r.uniform(-jit, jit)))
            ss.append(sd)
            t = sd.end + r.randint(0, 4) * bit

        def inp(tt, ch):
            for sd in ss:
                if tt < sd.end + 1:
                    return sd.level(tt) << 4 | 8, 0
            return (ch.uo >> 3 & 1) << 4 | 8, 0          # then loop-back of the TX line
        simulate(c, int(t) + 3000, inp)
        return c
    if name == "can":
        return can_stress(img, ph, r)
    if name == "nec":
        return nec_stress(img, ph, r)
    if name == "jtag_ctrl":
        from test_jtag import scenario
        return scenario(img, ph, r.randrange(4))
    if name == "freq":                                 # 3-clk square: rises merge, the evt 2 path
        c = boot(img, ph)
        n, t0, fr = 7000, c.cycle + 20, r.random()
        simulate(c, n, lambda t, ch: ((1 if (t - t0 - fr) % 3 < 1.5 and t > t0 else 0) | 0x08, 0))
        return c
    return None


def nec_stress(img, ph, r):
    # structured IR traffic (CLK_HZ 200 kHz, PRE 0: 5 us steps as at 50 MHz / 256): frames with +-10 % jitter per
    # edge, a repeat code, a frame cut after a random bit (timeout path), a short leader, a short leader space
    from test_measure import nec_edges
    c = boot(img, ph)
    items = [("frame", r.randrange(256), r.randrange(256)), ("repeat",),
             ("frame", r.randrange(256), r.randrange(256), r.randint(1, 31)),
             ("lead", 4e-3, 4.5e-3), ("lead", 9e-3, 1e-3), ("frame", r.randrange(256), r.randrange(256))]
    edges, end = nec_edges(items, 200000, c.cycle + 20 + r.random(), r, jit=0.1)
    ts = [e[0] for e in edges]
    simulate(c, int(end - c.cycle) + 10, lambda t, ch: ((edges[bisect.bisect_right(ts, t) - 1][1] if t >= ts[0]
                                                          else 1) | 0x08, 0))
    return c


def can_stress(img, ph, r):
    # every path of can.s in one run: frames both ways, arbitration loss + retry, stuff / CRC / form / bit
    # errors, missing ACK, a glitch while idle
    from test_can import run as crun
    from models import CanNode, can_host_tx, can_stuff, can_frame
    etx = [e for e in img.engines if img.engines[e]["pads"]][0]
    c, n = boot(img, ph), CanNode(100)
    crun(c, n, 1300)
    g0 = c.cycle + 50
    crun(c, n, 300, glitch=lambda t: g0 <= t < g0 + 9)
    c.host_in[etx] += can_host_tx(0x123, [r.getrandbits(8) for _ in range(r.randint(0, 8))])
    crun(c, n, 14000)
    n.send(c.cycle + r.randint(0, 50), 0x0F8, [0xFF] * r.randint(0, 8))
    crun(c, n, 14000)
    n.send_on_sof(r.randint(0, 9), 0x100, [0x01])
    c.host_in[etx] += can_host_tx(0x101, [0x02])
    crun(c, n, 8000)
    c.host_in[etx] += can_host_tx(0x101, [0x02])
    crun(c, n, 8000)
    body = can_stuff(can_frame(0x123, [0x00, 0x42]))
    k = [i for i in range(20, len(body)) if body[i] != body[i - 1] and
         all(body[i - j] == body[i - 1] for j in range(1, 6))][0]
    n.send(c.cycle + 20, 0, [], bits=body[:k] + [body[k - 1]] + body[k + 1:])
    crun(c, n, 7000)
    fb = can_frame(0x2A5, [0x81])
    fb[-2] ^= 1
    n.send(c.cycle + 20, 0, [], bits=can_stuff(fb))
    crun(c, n, 7000)
    n.send(c.cycle + 20, 0x1E1, [0x3C])
    n.txbits[n.nbody] = 0                                  # dominant CRC delimiter: form error
    crun(c, n, 7000)
    n.ack = n.flags = False
    c.host_in[etx] += can_host_tx(0x456, [0x01])
    crun(c, n, 7000)
    n.ack = n.flags = True
    data = [0x0F, 0xF0]
    c.host_in[etx] += can_host_tx(0x2C3, data)
    crun(c, n, 300)
    t_sof = c.cycle - 300 + 1
    kb = [i for i in range(25, 40) if can_stuff(can_frame(0x2C3, data))[i] == 1][0]
    n.force = [(c.cycle + (kb - 3) * 100, c.cycle + (kb - 2) * 100)]
    crun(c, n, 9000)
    return c


def check(img, chips, live):
    # -> (steps checked, failures, G starts seen)
    n, bad, seen = 0, [], set()
    sm = img.slots()
    for c in chips:
        for e in sorted(img.engines):
            if e not in live:
                continue
            for s in arm_steps(c, img, e, slot_gap(sm, e), slot_chain(sm, e)):
                n += 1
                seen.add(s["g"])
                if s["bound"] is None or s["measured"] > s["bound"] or s["target"] != s["next"] or s["miss"]:
                    bad.append(dict(s, map=sm, e=e))
    return n, bad, seen


IDS = ["%s%s" % (p[0], "".join("-%s%s" % kv for kv in p[1].items())) for p in PROGS]


@pytest.mark.parametrize("name,defs", PROGS, ids=IDS)
def test_t6_every_step_default_map(name, defs):
    sweep(name, defs, (0xE4,), cover=False)        # coverage of every G: the 4-map run (swtest-all)


@pytest.mark.slow
@pytest.mark.parametrize("name,defs", PROGS, ids=IDS)
def test_t6_every_step(name, defs):
    sweep(name, defs, MAPS)


def sweep(name, defs, maps, cover=True):
    clk = 6000 if FULL else 2800
    total, bad, seen, gs = 0, [], set(), None
    for m in maps:
        img = build(name, m, defs)
        live = [e for e in img.engines if slot_gap(m, e) is not None]
        gs = {v[0] for a, v in g_map(img).items() if img.owner[a] in live and not never(img, v[0])} | (gs or set())
        chips = []
        for ph in range(4):
            for seed in range(2 if FULL else 1):
                c = boot(img, ph)
                simulate(c, clk, stimulus(1000 * m + 10 * ph + seed, live))
                chips.append(c)
                c = stress(name, img, ph, 1000 * m + 10 * ph + seed, live)
                if c is not None:
                    chips.append(c)
        n, b, s = check(img, chips, live)
        total, bad, seen = total + n, bad + b, seen | s
    record("T6 %s %s: bound >= measured on every step, maps %s x 4 phases, spacing down to 0" %
           (name, defs, "/".join("%02X" % m for m in maps)),
           not bad, "%d steps, %d of %d G seen, %d over the bound %s" % (total, len(seen & gs), len(gs), len(bad),
                                                                      bad[:2]))
    assert not bad, bad[:4]
    assert seen >= gs or not cover, ("G never reached", sorted(gs - seen))


# ---------------- part 2: in-contract worst cases
def contract_runs():
    # (label, image, chips, engines): header rates and minimum spacings of the programs that state an .assume
    out = []
    for name, defs, sc, engdef in tb.CASES:
        for m in MAPS:
            img = tb.build(name, m, **(dict(defs, **{engdef: 0}) if engdef else defs))
            if not engdef and m == 0x00:
                continue
            live = [e for e in img.engines if slot_gap(m, e) is not None]
            out.append(("T5 %s %02X" % (name, m), img, [sc(img, ph, pp, 0) for ph in range(4) for pp in range(4)],
                        live))
    # uart_rx at its limits, back to back, senders +-2 %
    for bit, v in ((15, {}), (19, {"PARITY": 1}), (19, {"DBITS": 9}), (23, {"PARITY": 1, "DBITS": 9})):
        img = tb.build("uart_rx", 0xE4, BIT=bit, ENG=0, **v)
        chips = []
        for ph in range(4):
            for rate in (-0.02, 0.02):
                c = boot(img, ph)
                s = UartSender(bit / (1 + rate), [0x55, 0x00, 0xFF, 0xA3] * 2, dbits=v.get("DBITS", 8),
                               parity=v.get("PARITY", 0), t0=c.cycle + 20 + ph * 0.3)
                simulate(c, int(s.end) + 60, lambda t, ch, s=s: (s.level(t) << 7 | 8, 0))
                chips.append(c)
        out.append(("uart_rx BIT %d %s" % (bit, v), img, chips, [0]))
    # uart_tx at BIT 11, every stop / data option, back to back
    for v in ({}, {"PARITY": 1}, {"STOP2": 1}, {"STOP15": 1}, {"DBITS": 9}, {"DBITS": 5}):
        img = tb.build("uart_tx", 0xE4, BIT=11, ENG=0, **v)
        chips = []
        for ph in range(4):
            c = boot(img, ph)
            simulate(c, 30)
            c.host_in[0] += [0x55, 0xA3, 0x00, 0xFF, 0x81, 0x7E] * 2
            simulate(c, 11 * 13 * 14)
            chips.append(c)
        out.append(("uart_tx BIT 11 %s" % v, img, chips, [0]))
    # manchester RX at BIT 16 and 22: 0x55 (half-bit-off lock) and 0xAA preambles, +-2 %, jitter (BIT/4 - 2)/2
    for bit in (18, 22):
        for cv in (1, 0):
            img = tb.build("manchester", 0xE4, BIT=bit, CVCHK=cv)
            rnd, chips = random.Random(bit), []
            for ph in range(4):
                for pp in range(4):
                    c = boot(img, ph)
                    simulate(c, 30)
                    jit = (bit / 4 - 2) / 2
                    ss, t = [], c.cycle + 30 + pp * 0.25
                    for pre, rate in ((0x55, 0.02), (0xAA, -0.02), (0x55, -0.02), (0xAA, 0.02)):
                        s = ManchesterSender(bit * (1 + rate), [pre, pre, 0xD5, 0x12, 0xA5, 0xFF], t0=t,
                                             jitter=lambda k: rnd.uniform(-jit, jit))
                        ss.append(s)
                        t = s.end + 4 * bit
                    c.host_in[0] += [0x55, 0xD5, 0x3C, 0xA5]

                    def inp(tt, ch, ss=ss):
                        for s in ss:
                            if tt < s.end + 1:
                                return s.level(tt) << 4 | 8, 0
                        return 8, 0
                    simulate(c, int(t) + 4 * bit, inp)
                    chips.append(c)
            out.append(("manchester BIT %d CVCHK %d" % (bit, cv), img, chips, [0, 1]))
    # spi_tgt: CS high 5..39 clk, first sampling edge at the D100 limits
    img = tb.build("spi_tgt", 0xE4, ENG=0)
    chips = []
    for ph in range(4):
        for hi in range(5, 40, 2):
            c = boot(img, ph)
            a = SpiController(25, 0, [1, 0, 0], t0=c.cycle + 40)
            r = a.cs_hi
            b = SpiController(25, 0, [2, 0, 0], t0=r + hi, cs_setup=max(32 - hi, 11 if hi < 25 else 7))

            def inputs(t, ch, a=a, b=b, r=r):
                miso = (ch.uio_out >> 2) & (ch.uio_oe >> 2) & 1
                if t < r:
                    return 8, a.sck(t) << 3 | a.cs(t) | a.mosi(t) << 1 | miso << 2
                if t < b.cs_lo:
                    return 8, 1 | 1 << 1 | miso << 2
                return 8, b.sck(t) << 3 | b.cs(t) | b.mosi(t) << 1 | miso << 2
            simulate(c, int(b.cs_hi) + 40, inputs)
            chips.append(c)
    out.append(("spi_tgt CS high 5..39", img, chips, [0]))
    # ws2812: bytes back to back and with pauses
    img = tb.build("ws2812", 0xE4, ENG=0)
    chips = []
    for ph in range(4):
        c = boot(img, ph)
        simulate(c, 30)
        c.host_in[0] += [0x00, 0xFF, 0x5A, 0x81]
        simulate(c, 63 * 8 * 4 + 300)
        c.host_in[0] += [0xC3, 0x18]
        simulate(c, 63 * 8 * 3)
        chips.append(c)
    out.append(("ws2812 back to back + pause", img, chips, [0]))
    return out


@pytest.mark.slow
def test_t6_assumptions_hold_in_contract():
    rows, bad = [], []
    for label, img, chips, live in contract_runs():
        sm = img.slots()
        waits = {}
        for c in chips:
            for e in live:
                for s in arm_steps(c, img, e, slot_gap(sm, e), slot_chain(sm, e)):
                    if s["g"] in img.assume:
                        waits.setdefault(s["g"], []).append(s["wait"])
                        if s["wait"] < img.assume[s["g"]]:
                            bad.append((label, "assume", hex(s["g"]), s["wait"], img.assume[s["g"]]))
                    if s["bound"] is None or s["measured"] > s["bound"] or s["target"] != s["next"]:
                        bad.append((label, "bound", s))
        for g, m in img.assume.items():
            if img.owner[g] in live:
                w = waits.get(g)
                rows.append("%s %03x: min %s >= %d" % (label, g, min(w) if w else None, m))
                if not w:
                    bad.append((label, "assume never exercised", hex(g)))
    record("T6 .assume armed holds in the in-contract worst cases", not bad, "; ".join(rows))
    assert not bad, bad[:6]
