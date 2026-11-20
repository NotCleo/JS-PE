# T1, T3, T4, T6: fetch timing of ARCH.md 4.2 in every slot phase and slot map, against a slot model.
import pytest
from harness import assemble_text, boot, simulate, record

MAPS = {0xE4: (0, 1, 2, 3), 0x00: (0, 0, 0, 0), 0x44: (0, 1, 0, 1), 0x24: (0, 1, 2, 0)}
RANGE = {0xE4: (2, 5), 0x00: (2, 2), 0x44: (2, 3), 0x24: (2, 4)}     # 2 + (largest gap - 1)

PROG = """
.engine 0
.slots {s}
.init
        li    R0, #0x04             ; p0 <- ui_in[4]
        wcfg  BIND01, R0
        li    R0, #buf
        li    R1, #0x1234
        li    R2, #0
.entry go
.code
go:     @rise p0 : nop              ; X1: successor from PF
x1s:    nop
        @rise p0 : jhi p0, tj       ; X2: taken jump
        @never : nop
tj:     @rise p0 : nop
        ld    R2, [R0]              ; X3: ld fires one clk after the rise
        @rise p0 : nop
        st    R1, [R0+1]!           ; X4: st
        @rise p0 : nop
        call  sub                   ; X5: call
cr:     @rise p0 : nop
        jmp   fj                    ; X6: jmp in PF, folded
        @never : nop
fj:     nop
        @rise p0 : jhi p0, ch       ; X7: redirect, then words that cannot be prefetched
        @never : nop
ch:     nop
        nop
        nop
        nop
        jmp   j2                    ; two jumps in a row, arriving with IR empty
j2:     jmp   j3
        @never : nop
j3:     @never : nop
sub:    @rise p0 : ret              ; X8: ret
buf:    .word 0x5678, 0
"""


def owner(mapv, c):
    return MAPS[mapv][c % 4]


def first_slot(mapv, c):
    while owner(mapv, c) != 0:
        c += 1
    return c


def run(mapv, ph, off, n_edges=9, gap=24):
    img = assemble_text(PROG.format(s=", ".join(str(x) for x in MAPS[mapv])))
    c = boot(img, ph)
    start = c.cycle
    t0 = c.cycle + 30 + off
    edges = [t0 + k * gap for k in range(n_edges)]

    log = c.log = []

    def inputs(t, ch):
        log.append((ch.cycle, ch.eng[0].R[2], ch.mem[img.label("buf") + 1]))
        lvl = 0
        for e in edges:
            if e <= t < e + gap // 2:
                lvl = 1
        return lvl << 4 | 8, 0
    simulate(c, edges[-1] + gap + 20 - c.cycle, inputs)
    e = c.eng[0]
    e.fires = [f for f in e.fires if f[0] >= start]
    e.arms = [a for a in e.arms if a[0] > start]
    return img, c


def fire_of(eng, pc, name=None):
    return [f[0] for f in eng.fires if f[1] == pc and (name is None or f[3] == name)]


def arm_after(eng, cy, pc):
    for a in eng.arms:
        if a[0] > cy and a[1] == pc:
            return a[0]
    return None


CASES = []
for _m in MAPS:
    for _ph in range(4):
        for _off in range(4):
            CASES.append((_m, _ph, _off))


@pytest.fixture(scope="module")
def runs():
    return {k: run(*k) for k in CASES}


def test_t1_successor_in_pf(runs):
    for k, (img, c) in runs.items():
        e = c.eng[0]
        f = fire_of(e, img.label("go"))[0]
        assert arm_after(e, f, img.label("x1s")) == f + 1, k


def test_t1_redirects_against_slot_model(runs):
    seen = {m: set() for m in MAPS}
    rows = []
    for (m, ph, off), (img, c) in runs.items():
        e = c.eng[0]
        for src, tgt, name in ((img.label("tj") - 2, img.label("tj"), "jhi"),
                               (img.label("cr") - 1, img.label("sub"), "call"),
                               (img.label("sub"), img.label("cr"), "ret")):
            fs = fire_of(e, src, name)
            assert fs, (m, ph, off, name)
            f = fs[0]
            a = arm_after(e, f, tgt)
            exp = first_slot(m, f) + 2
            rows.append((m, name, a - f))
            assert a == exp, (hex(m), ph, off, name, f, a, exp)
            lo, hi = RANGE[m]
            assert lo <= a - f <= hi
            seen[m].add(a - f)
    ok = all(seen[m] == set(range(RANGE[m][0], RANGE[m][1] + 1)) for m in MAPS)
    record("T1 redirect (jhi, call, ret) -> armed g+2, 4 maps x 16 phases", ok,
           " ".join("%02X:%s" % (m, sorted(seen[m])) for m in MAPS))
    assert ok


def test_t1_t4_ld_st_against_slot_model(runs):
    seen_ld, seen_st = {m: set() for m in MAPS}, {m: set() for m in MAPS}
    for (m, ph, off), (img, c) in runs.items():
        e = c.eng[0]
        fl = fire_of(e, img.label("tj") + 1, "ld")[0]
        fs = fire_of(e, img.label("tj") + 3, "st")[0]
        comp_ld = next(cy for cy, r2, _ in c.log if r2 == 0x5678) - 1      # completion clock = grant + 1
        comp_st = next(cy for cy, _, mv in c.log if mv == 0x1234) - 1      # completion clock = grant
        assert comp_ld == first_slot(m, fl) + 1, (hex(m), ph, off, "ld", fl, comp_ld)
        assert comp_st == first_slot(m, fs), (hex(m), ph, off, "st", fs, comp_st)
        # the successor is never prefetched: requested in g + 2 (ld) / g + 1 (st), armed 2 clk after its grant (D070)
        assert arm_after(e, fl, img.label("tj") + 2) == first_slot(m, comp_ld + 1) + 2, (hex(m), ph, off, "ld next")
        assert arm_after(e, fs, img.label("tj") + 4) == first_slot(m, comp_st + 1) + 2, (hex(m), ph, off, "st next")
        seen_ld[m].add(comp_ld - fl + 1)
        seen_st[m].add(comp_st - fs + 1)
    ok = (all(min(seen_ld[m]) >= 2 and max(seen_ld[m]) <= 5 and min(seen_st[m]) >= 1 and max(seen_st[m]) <= 4
              for m in MAPS) and seen_ld[0xE4] == {2, 3, 4, 5} and seen_st[0xE4] == {1, 2, 3, 4})
    record("T4 ld 2-5 clk, st 1-4 clk (fire -> completion), 4 maps x 16 phases", ok,
           "ld " + " ".join("%02X:%s" % (m, sorted(seen_ld[m])) for m in MAPS) +
           "; st " + " ".join("%02X:%s" % (m, sorted(seen_st[m])) for m in MAPS))
    assert ok


def chain_model(m, f, n):
    # redirect fired in f: word k armed in a(k); each later word requested in the clock its predecessor is armed
    a = [first_slot(m, f) + 2]
    for _ in range(n - 1):
        a.append(first_slot(m, a[-1]) + 2)
    return a


def chain_meas(img, c):
    e = c.eng[0]
    f = fire_of(e, img.label("ch") - 2, "jhi")[0]
    return f, [arm_after(e, f, img.label("ch") + k) for k in range(4)]


def test_t1_words_not_prefetched(runs):
    worst = {m: 0 for m in MAPS}
    for (m, ph, off), (img, c) in runs.items():
        f, got = chain_meas(img, c)
        assert got == chain_model(m, f, 4), (hex(m), ph, off, f, got)
        worst[m] = max(worst[m], max(a - f - 4 * k for k, a in enumerate(got)))
    ok = all(worst[m] <= 1 + 4 for m in MAPS)
    record("T1 j words not prefetched: armed <= f + 4j + 1 (j = 1..4)", ok,
           "max (armed - f - 4(j-1)) " + " ".join("%02X:%d" % (m, worst[m]) for m in MAPS))
    assert ok


def test_t1_chain_per_access_equals_k(runs):
    # D054: chained accesses cost K = max over own slots s of (first own slot >= s + 2) - s
    from jspe_asm import slot_chain
    for (m, ph, off), (img, c) in runs.items():
        f, got = chain_meas(img, c)
        assert max(b - a for a, b in zip(got, got[1:])) <= slot_chain(m, 0), (hex(m), got)


def test_t3_jump_folding(runs):
    for (m, ph, off), (img, c) in runs.items():
        e = c.eng[0]
        assert not fire_of(e, img.label("fj") - 2), hex(m)                    # jmp in PF never executes
        f = fire_of(e, img.label("cr"))[0]
        assert arm_after(e, f, img.label("fj")) == f + 1, (hex(m), ph, off)
        _, got = chain_meas(img, c)
        # two jumps in a row arriving with IR empty: one fetch each, no execute clock
        assert arm_after(e, got[3], img.label("j3")) == chain_model(m, got[3], 3)[-1], (hex(m), ph, off)


def iso_trace(others):
    src = PROG.format(s="0, 1, 2, 3")
    if others:
        for k in (1, 2, 3):
            src += """
.engine %d
.init
        li    R0, #d%d
.entry l%d
.code
.org %d
l%d:    ld    R1, [R0+1]!
        st    R1, [R0]
        ld    R2, [R0]
        jmp   l%d
d%d:    .space 48
""" % (k, k, k, 240 + 60 * k, k, k, k)
    img = assemble_text(src)
    out = []
    for ph in range(4):
        c = boot(img, ph)
        start = c.cycle
        edges = [start + 30 + 24 * k + (k % 4) for k in range(9)]

        def inputs(t, ch):
            return (1 if any(e <= t < e + 12 for e in edges) else 0) << 4 | 8, 0
        simulate(c, 260, inputs)
        e0 = c.eng[0]
        if others:
            assert all(sum(1 for f in c.eng[k].fires if f[3] in ("ld", "st")) > 20 for k in (1, 2, 3))
            assert c.undef_reads == 0
        out.append([(f[0] - start, f[1]) for f in e0.fires if f[0] >= start])
    return out


def test_t6_isolation():
    alone, loaded = iso_trace(False), iso_trace(True)
    ok = alone == loaded
    record("T6 engine 0 fire trace alone == with 3 engines in ld/st loops", ok,
           "%d fires x 4 phases" % sum(len(x) for x in alone))
    assert ok
