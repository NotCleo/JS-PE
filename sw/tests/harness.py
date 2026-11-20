# Shared helpers for the sw tests: assemble, boot in a slot phase, drive pads, measure arming delays.
import bisect, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SW = os.path.dirname(HERE)
ROOT = os.path.dirname(SW)
sys.path.insert(0, SW)
sys.path.insert(0, HERE)
from jspe_asm import assemble, assemble_text, decode, is_guarded, AsmError, jump_target, walk_bound  # noqa: E402
from jspe_iss import Chip                                                     # noqa: E402

PROG = os.path.join(ROOT, "docs", "spec", "programs")
RESULTS = os.path.join(ROOT, "build", "test_first_results.txt")


def prog(name, **defs):
    return assemble([os.path.join(PROG, name + ".s")], {k: str(v) for k, v in defs.items()})


def boot(img, phase=0, trace=True, fifo_log=False):
    c = Chip()
    for e in c.eng:
        e.trace = trace
    if fifo_log:
        c.fifo_log = []
    c.boot(img, phase=phase)
    return c


def simulate(chip, n, inputs=None, until=None):
    # inputs(t, chip) -> (ui, uio): pad levels at the sampling edge t = cycle + 1 (just before it)
    for _ in range(n):
        if inputs:
            ui, uio = inputs(chip.cycle + 1 - 1e-6, chip)
        else:
            ui, uio = 0x08, 0
        chip.step(ui, uio)
        if until and until(chip):
            return True
    return False


def deadline_delays(chip, d):
    # arming delay of one .deadline path: G fires (its loop end for loops) -> path target armed, path as listed
    eng = chip.eng[d["engine"]]
    rng = range(d["g"][1], d["g"][-1] + 1)
    arms = [(cy, pc) for cy, pc, ir in eng.arms if is_guarded(ir)]
    cys = [a[0] for a in arms]
    fcy = [f[0] for f in eng.fires]
    want = [a for a in d["path"] if decode_name(chip, a) not in ("jmp", "jmpa")]
    out = []
    for cy, pc, late, name, lend in eng.fires:
        if pc not in rng or not lend:
            continue
        k = bisect.bisect_right(cys, cy)
        if k == len(arms) or arms[k][1] != d["target"]:
            continue
        lo, hi = bisect.bisect_right(fcy, cy), bisect.bisect_left(fcy, arms[k][0])
        got = [f[1] for f in eng.fires[lo:hi] if f[3] not in ("jmp", "jmpa")]
        if got == want:
            out.append(arms[k][0] - cy)
    return out


def decode_name(chip, a):
    i, _ = decode(chip.mem[a])
    return i["name"] if i else None


def changes(chip, getbit, init=0):
    out, last = [], init
    for t, uo, uout, uoe in chip.pad_trace:
        v = getbit(uo, uout, uoe)
        if v != last:
            out.append((t, v))
            last = v
    return out


def record(name, ok, measured):
    os.makedirs(os.path.dirname(RESULTS), exist_ok=True)
    with open(RESULTS, "a") as f:
        f.write("%-58s %-4s %s\n" % (name, "pass" if ok else "FAIL", measured))


def g_map(img):
    # address -> (G start, G end) for every guarded instruction and loop (ISA.md 10)
    out = {}
    for a in sorted(img.words):
        if a in out or ".word" in img.src[a][2].lower():
            continue
        i, f = decode(img.words[a])
        if i is None:
            continue
        if i["format"] == "E" and f["rr"] in (1, 2):
            end = a + (0 if f["rr"] == 1 else 1)
            for x in range(a, end + 1):
                out[x] = (a, end)
        elif is_guarded(img.words[a]):
            out[a] = (a, a)
    return out


def _folded(img, a):
    # address that executes next when control reaches a (unconditional jmps fold)
    for _ in range(4):
        w = img.words.get(a)
        if w is None or decode_name_w(w) not in ("jmp", "jmpa"):
            return a
        a = jump_target(a, w)
    return a


def decode_name_w(w):
    i, _ = decode(w)
    return i["name"] if i else None


def arm_steps(chip, img, e, gap, chain, t0=None):
    # every final firing of a G on engine e after cycle t0 -> dict(g, wait, measured, bound, target, next, taken):
    # wait = clk G was armed before it fired; measured = clk to the next armed G; bound = the assembler's walk_bound
    # for the path actually executed at that wait. Paths cut by a watcher redirect are skipped.
    t0 = getattr(chip, "t_run", 0) if t0 is None else t0
    eng = chip.eng[e]
    gm = g_map(img)
    starts = {v[0] for v in gm.values()}
    fires = [f for f in eng.fires if f[0] >= t0 and img.owner.get(f[1]) == e]
    garms = [(cy, pc) for cy, pc, ir in eng.arms if cy >= t0 and pc in starts and img.owner.get(pc) == e]
    acy = [a[0] for a in garms]
    redirs = sorted(c for c in eng.redirs if c >= t0)
    bounds = sorted([f[0] for f in fires if f[1] in gm and f[4]] + redirs)
    out = []
    for k, (cy, pc, late, name, lend) in enumerate(fires):
        if pc not in gm or not lend:
            continue
        gs, ge = gm[pc]
        b = bisect.bisect_left(bounds, cy)
        lo = bounds[b - 1] if b > 0 else t0 - 1
        mine = [a for a in garms[bisect.bisect_right(acy, lo):bisect.bisect_right(acy, cy)] if a[1] == gs]
        n = bisect.bisect_right(acy, cy)
        if not mine or n == len(garms):
            continue
        ncy, npc = garms[n]
        r = bisect.bisect_right(redirs, cy - 1)
        if r < len(redirs) and redirs[r] <= ncy:
            continue
        path = [f[1] for f in fires[k + 1:] if cy < f[0] < ncy and f[1] not in gm]
        seq = path + [npc]
        w = img.words[ge]
        tgt = jump_target(ge, w) if decode(w)[0]["format"] in ("JF", "JP") else None
        taken = tgt is not None and _folded(img, tgt) == seq[0] and _folded(img, (ge + 1) & 511) != seq[0]
        dec = {}
        for x, nx in zip(path, seq[1:]):
            wx = img.words[x]
            if decode(wx)[0]["format"] in ("JF", "JP"):
                dec[x] = _folded(img, jump_target(x, wx)) == nx and _folded(img, (x + 1) & 511) != nx
        miss = []

        def decide(a, t):
            if a not in dec:
                miss.append(a)
            return dec.get(a, False)
        wait = cy - mine[0][0]
        ret = None
        if any(decode_name_w(img.words[x]) == "ret" for x in path):
            depth = 0
            for f in reversed(eng.fires[:eng.fires.index(fires[k])]):
                if f[3] == "ret":
                    depth += 1
                elif f[3] == "call":
                    if depth == 0:
                        ret = (f[1] + 1) & 511
                        break
                    depth -= 1
            if ret is None:                    # call-mode watcher: L = the preempted instruction
                ix = [i for i, x in enumerate(path) if decode_name_w(img.words[x]) == "ret"][0]
                ret = seq[ix + 1]
        bd, target, _ = walk_bound(img, ge, decide, gap, chain, wait, taken, ret)
        out.append({"g": gs, "wait": wait, "measured": ncy - cy, "bound": bd, "target": target, "next": npc,
                    "taken": taken, "late": late, "cy": cy, "miss": miss})
    return out
