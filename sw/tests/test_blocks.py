# S2-S5, S7-S9, T2: shifter, timer, events, watchers, streams, pins, shared blocks, micro-loop (ISS level).
import random
import pytest
from harness import assemble_text, boot, simulate, record
from jspe_asm import encode
from jspe_iss import Chip, HostLink
from test_hostlink import link_step

CFG = {"SHCFG": 0, "TAPCFG": 1, "TCFG": 2, "BIND01": 3, "BIND23": 4, "CC0": 5, "CC1": 6, "WV0": 7, "WV1": 8,
       "LFSR_LO": 16, "LFSR_HI": 17, "POLY_LO": 18, "POLY_HI": 19, "LFCFG": 20, "SIG": 21}


def w1(text):
    img = assemble_text(".engine 0\n.code\n" + text + "\n")
    return img.words[min(img.words)]


def chip():
    c = Chip()
    c.mem = [0] * 512
    return c


def ex(c, text, e=0, ui=0, uio=0):
    w = w1(text) if isinstance(text, str) else text
    c.pulse(e, "exec", w)
    c.step(ui, uio)
    for _ in range(2000):
        if not c.eng[e].xm:
            return
        c.step(ui, uio)
    raise AssertionError("exec did not complete: " + str(text))


def wcfg(c, name, v, e=0):
    c.eng[e].R[0] = v & 0xFFFF
    c.exec_word(e, encode("wcfg", creg=CFG[name], rs=0))


def rcfg(c, name, e=0):
    c.exec_word(e, encode("rcfg", creg=CFG[name], rd=1))
    return c.eng[e].R[1]


# ---------------------------------------------------------------- S2 shifter
def ref_shift(S, f, wl, lsb):
    mask = (1 << wl) - 1
    return (((S & mask) >> 1) | (f << (wl - 1))) if lsb else (((S << 1) | f) & mask)


def test_s2_out_shift_against_reference():
    rnd = random.Random(11)
    for _ in range(150):
        wl, lsb, inv, podd = rnd.randrange(1, 17), rnd.randrange(2), rnd.randrange(2), rnd.randrange(2)
        S = rnd.randrange(1 << wl)
        c = chip()
        wcfg(c, "SHCFG", (wl - 1) | lsb << 4 | inv << 7)
        wcfg(c, "TAPCFG", podd << 2)
        c.eng[0].S = S
        N, PAR, s = 0, podd, S
        for k in range(rnd.randrange(1, 2 * wl + 2)):
            D = ((s & 1) if lsb else (s >> (wl - 1) & 1)) ^ inv
            ex(c, "p0 <- out")
            wend = N == 1 or (N == 0 and wl == 1)
            PAR = (podd if N == 0 else PAR) ^ D
            N = wl - 1 if N == 0 else N - 1
            s = ref_shift(s, 1, wl, lsb)
            e = c.eng[0]
            assert (e.val[0], e.S, e.N, e.B, e.PAR) == (D, s, N, D, PAR), (wl, lsb, inv, k)
            del wend


def test_s2_shin_and_diff_against_reference():
    rnd = random.Random(12)
    for _ in range(60):
        wl, lsb, inv, diff = rnd.randrange(1, 17), rnd.randrange(2), rnd.randrange(2), rnd.randrange(2)
        c = chip()
        wcfg(c, "BIND01", 4)                          # p0 <- ui_in[4]
        wcfg(c, "SHCFG", (wl - 1) | lsb << 4 | inv << 7 | diff << 8)
        s, LV = 0, 0
        for k in range(2 * wl):
            lv = rnd.randrange(2)
            for _ in range(3):
                c.step(lv << 4)
            ex(c, "shin", ui=lv << 4)
            b = lv ^ (LV if diff else 0) ^ inv
            LV = lv
            s = ref_shift(s, b, wl, lsb)
            assert (c.eng[0].S, c.eng[0].B) == (s, b), (wl, lsb, inv, diff, k)


def test_s2_smp_leaves_s_and_n():
    c = chip()
    wcfg(c, "BIND01", 4)
    wcfg(c, "SHCFG", 7)
    c.eng[0].S = 0x5A
    for _ in range(3):
        c.step(1 << 4)
    ex(c, "smp", ui=1 << 4)
    e = c.eng[0]
    assert (e.S, e.N, e.B, e.PAR) == (0x5A, 0, 1, 1)


def test_s2_run_length_and_r1():
    c = chip()
    wcfg(c, "SHCFG", 15)
    wcfg(c, "TAPCFG", 5 << 4)                      # rl = 5
    c.eng[0].S = 0xF800                            # 5 ones then zeros, MSB first
    runs = []
    for _ in range(7):
        ex(c, "p0 <- out")
        runs.append(c.eng[0].RUN)
    assert runs == [1, 2, 3, 4, 5, 1, 2]
    wcfg(c, "TAPCFG", 3 << 4 | 1 << 7)             # r1: count ones only
    wcfg(c, "SHCFG", 15)                           # wrst: RUN <- 0
    c.eng[0].S = 0xE000
    runs = []
    for _ in range(5):
        ex(c, "p0 <- out")
        runs.append(c.eng[0].RUN)
    assert runs == [1, 2, 3, 0, 0]


# ---------------------------------------------------------------- S3 timer
def timer_events(c, n, e=0):
    out = []
    for _ in range(n):
        eng = c.eng[e]
        T0 = eng.T
        c.step()
        out.append(T0)
    return out


def test_s3_prescale():
    for pre, div in ((1, 16), (2, 256)):
        img = assemble_text("""
.engine 0
.init
        li    R0, #%d << 8
        wcfg  TCFG, R0
        li    R0, #2
        mov   P, R0
.entry go
.code
go:     tsync
l:      @tick : nop
        jmp   l
""" % pre)
        c = boot(img, 0)
        simulate(c, div * 3 * 6)
        ticks = [f[0] for f in c.eng[0].fires if f[1] == img.label("l")]
        assert len(ticks) >= 4
        assert {b - a for a, b in zip(ticks, ticks[1:])} == {3 * div}, (pre, ticks)


def test_s3_hard_and_windowed_sync():
    # hard sync: every rise of p0 restarts T <- P; windowed: only when stopped or past tock
    for swin in (0, 1):
        img = assemble_text("""
.engine 0
.init
        li    R0, #4
        wcfg  BIND01, R0
        li    R0, #(1 << 10) | (%d << 14)    ; sedge rise, spin p0
        wcfg  TCFG, R0
        li    R0, #20
        mov   P, R0
        li    R0, #10
        mov   C, R0
.entry go
.code
go:     @never : nop
""" % swin)
        c = boot(img, 0)
        starts = []
        edges = [40, 45, 60, 75, 110]

        def inp(t, ch):
            return (1 if any(e <= t < e + 2 for e in edges) else 0) << 4 | 8, 0
        prevT = None
        for _ in range(160):
            ui, uio = inp(c.cycle + 1 - 1e-6, c)
            T0, run0 = c.eng[0].T, c.eng[0].trun
            c.step(ui, uio)
            if c.eng[0].T == 20 and (T0 != 0 or not run0):
                starts.append(c.cycle - 1)
            prevT = T0
        del prevT
        # edge at e (pad) -> synced level in e+2 -> rise live in clock e+2 -> start at the end of it
        if swin == 0:
            assert starts[:5] == [e + 2 for e in edges], starts
        else:
            # 40: stopped -> start (one-shot); 45: T=15 >= C -> ignored; 60: T=0..: past tock -> start; 75: past tock
            assert 42 in starts and 47 not in starts and 62 in starts, starts


def test_s3_frac_average_period():
    img = assemble_text("""
.engine 0
.init
        li    R0, #170
        wcfg  TCFG, R0
        li    R0, #15
        mov   P, R0
.entry go
.code
go:     tsync
l:      @tick : nop
        jmp   l
""")
    c = boot(img, 0)
    simulate(c, 17 * 270)
    t = [f[0] for f in c.eng[0].fires if f[1] == img.label("l")]
    assert t[256] - t[0] == 256 * 16 + 170                # P + 1 + frac/256 on average
    assert {b - a for a, b in zip(t, t[1:])} == {16, 17}


# ---------------------------------------------------------------- S4 events
def test_s4_doubled_event_sets_ovr_only_when_consumed():
    img = assemble_text("""
.engine 0
.init
        li    R0, #4
        wcfg  BIND01, R0
        li    R0, #buf
.entry go
.code
go:     ld    R1, [R0]
        ld    R1, [R0]
        ld    R1, [R0]
        ld    R1, [R0]
        @rise p0 : nop
        @never : nop
buf:    .word 0
""")
    for consume in (True, False):
        c = boot(img, 0)
        s0 = c.cycle
        edges = [s0 + 2, s0 + 6] if consume else [s0 + 4]

        def inp(t, ch):
            return (1 if any(e <= t < e + 2 for e in edges) else 0) << 4 | 8, 0
        simulate(c, 60, inp)
        assert c.eng[0].ovr == int(consume and len(edges) == 2), consume


def test_s4_level_guard_not_latched_and_timer_start_clears():
    img = assemble_text("""
.engine 0
.init
        li    R0, #4
        wcfg  BIND01, R0
        li    R0, #0x0A00                  ; CC0 = level p0, off
        wcfg  CC0, R0
        li    R0, #3
        mov   P, R0
        li    R0, #buf
.entry go
.code
go:     tsync
        ld    R1, [R0]
        ld    R1, [R0]
        ld    R1, [R0]
        tsync                              ; clears the ticks latched meanwhile
        @tick : p1 <- 1
        @c0 : p1 <- 0
        @never : nop
buf:    .word 0
""")
    c = boot(img, 0)
    s0 = c.cycle

    def inp(t, ch):
        return (1 if s0 + 5 <= t < s0 + 7 else 0) << 4 | 8, 0
    simulate(c, 80, inp)
    e = c.eng[0]
    t2 = [f[0] for f in e.fires if f[3] == "tsync"][1]
    tk = [f[0] for f in e.fires if f[1] == img.label("go") + 5][0]
    assert tk == t2 + 4 + 0 or tk >= t2 + 4                 # first tick after the restart, not a latched one
    assert not [f for f in e.fires if f[1] == img.label("go") + 6]   # c0 pulse during busy clocks not latched


# ---------------------------------------------------------------- S5 watchers
WATCH2 = """
.engine 0
.init
        li    R0, #0x04 | (0x05 << 8)       ; p0 <- ui_in[4], p1 <- ui_in[5]
        wcfg  BIND01, R0
        li    R0, #0x0A04 | (%d << 12)      ; CC0: rise p0
        wcfg  CC0, R0
        li    R0, #0x0A05 | (%d << 12)      ; CC1: rise p1
        wcfg  CC1, R0
        li    R0, #h0
        wcfg  WV0, R0
        li    R0, #h1
        wcfg  WV1, R0
        watch #3
.entry go
.code
go:     @never : nop
h0:     p2 <- 1
        @never : nop
h1:     p3 <- 1
        ret
"""


def test_s5_both_watchers_one_clock_watcher0_wins():
    img = assemble_text(WATCH2 % (1, 1))
    c = boot(img, 0)
    s0 = c.cycle
    simulate(c, 60, lambda t, ch: ((3 if t > s0 + 10 else 0) << 4 | 8, 0))
    e = c.eng[0]
    assert e.val[2] == 1 and e.val[3] == 0


def test_s5_call_ret_reexecutes_and_none_while_stopped():
    img = assemble_text(WATCH2 % (1, 2))
    c = boot(img, 0)
    s0 = c.cycle
    simulate(c, 40, lambda t, ch: ((2 if t > s0 + 10 else 0) << 4 | 8, 0))
    e = c.eng[0]
    assert e.val[3] == 1 and e.PC == img.label("go") and e.irv == 1   # back at the preempted guard
    assert e.L == img.label("go")
    c.pulse(0, "stop")
    simulate(c, 5, lambda t, ch: (2 << 4 | 8, 0))
    simulate(c, 20, lambda t, ch: (1 << 4 | 8, 0))                     # rise p0 while stopped
    assert e.val[2] == 0
    assert [f for f in e.fires if f[1] == img.label("h0")] == []


# ---------------------------------------------------------------- S7 streams
def decode_stream(got):
    out, cur, k = [], None, 0
    while k < len(got):
        b = got[k]
        if b == 0x1B:
            x = got[k + 1]
            k += 2
            if x <= 3:
                cur = x
                out.append(("sel", x))
            elif x == 0x1B:
                out.append((cur, 0x1B))
            elif 0x40 <= x < 0x80:
                out.append((cur, "evt", x & 0x3F))
            else:
                out.append(("note", x))
        else:
            out.append((cur, b))
            k += 1
    return out


def test_s7_stream_scheduling_escape_and_events():
    src = ""
    for e in (0, 1):
        src += """
.engine %d
.init
        li    R0, #0x1B
        li    R1, #%d
.entry s%d
.code
.org %d
s%d:    @htx : push R0
        @htx : push R1
        @htx : push R1
        @htx : evt #5
        @htx : push R1
        @htx : push R1
        @htx : push R0
        @never : nop
""" % (e, 0x10 * (e + 1), e, 100 * (e + 1), e)
    img = assemble_text(src)
    c = Chip(direct=False)
    c.load_image(img)
    for e in (0, 1):
        for w in img.exec_words(e):
            c.exec_word(e, w)
    hl = HostLink(16)
    c.pulse(0, "run")
    c.pulse(1, "run")
    for _ in range(20000):
        link_step(c, hl)
    s = decode_stream(hl.got)
    data = [x for x in s if x[0] in (0, 1)]
    for e in (0, 1):
        v = 0x10 * (e + 1)
        assert [x[1:] for x in data if x[0] == e] == [(0x1B,), (v,), (v,), ("evt", 5), (v,), (v,), (0x1B,)]
    # a stream is served for at most 4 entries while the other has data waiting
    runs, last, n = [], None, 0
    for x in data:
        if x[0] != last:
            if last is not None:
                runs.append(n)
            last, n = x[0], 0
        n += 1
    runs.append(n)
    assert max(runs[:-1]) <= 4 and len(runs) >= 3, runs
    assert c.eng[0].ovr == 0 and c.eng[1].ovr == 0


def test_s7_fifo_limits_follow_registered_levels():
    from jspe_iss import Fifo
    f = Fifo()
    for k in range(4):
        assert f.commit(k, False, False)
    assert not f.commit(9, True, False) and f.q == [1, 2, 3]       # write to full ignored, pop honoured
    g = Fifo()
    assert g.commit(5, True, False) and g.q == [5]                  # pop of empty ignored, write honoured


def test_s7_h_rdy_threshold():
    img = assemble_text(".engine 0\n.entry go\n.code\ngo: @never : nop\n")
    c = Chip(direct=False)
    c.load_image(img)
    hl = HostLink(16)
    hl.send(bytes([0x1B, 0x00]))
    hl.send(bytes([1, 2, 3]))
    rdy = []
    for _ in range(160 * 8):
        link_step(c, hl)
        rdy.append((len(c.rx[0].q), (c.uo >> 5) & 1))
    lv = {q: r for q, r in rdy[-50:]}
    assert len(c.rx[0].q) == 3 and lv.get(3) == 1
    assert any(q == 2 and r == 0 for q, r in rdy) and any(q == 3 and r == 1 for q, r in rdy)


# ---------------------------------------------------------------- S8 pins
def test_s8_pad_modes():
    img = assemble_text("""
.engine 0
.pad uio1, p0
.pad uio2, p1, od
.pad uo0, p2, inv
.init
        nop
.entry go
.code
go:     p0 <- 1, p1 <- 0
        p2 <- 1
        @never : nop
""")
    c = boot(img, 0)
    simulate(c, 30)
    assert (c.uio_out >> 1) & 1 == 1 and (c.uio_oe >> 1) & 1 == 1
    assert (c.uio_oe >> 2) & 1 == 1 and (c.uio_out >> 2) & 1 == 0        # od: drive low = oe
    assert c.uo & 1 == 0                                                 # inverted


def test_s8_follow_one_clock_behind_s():
    c = chip()
    wcfg(c, "SHCFG", 7)
    c.eng[0].S = 0x80
    ex(c, "p0 <- f")
    assert c.eng[0].val[0] == 1
    c.eng[0].S = 0x00
    c.step()
    assert c.eng[0].val[0] == 0


def test_s8_no_edge_after_bind_write():
    img = assemble_text("""
.engine 0
.init
        li    R0, #25                       ; p0 <- constant 1
        li    R1, #24
        wcfg  BIND01, R1
.entry go
.code
go:     wcfg  BIND01, R0                    ; 0 -> 1 by rebinding: no edge
        @rise p0 : p1 <- 1
        @never : nop
""")
    c = boot(img, 0)
    simulate(c, 40)
    assert c.eng[0].val[1] == 0


# ---------------------------------------------------------------- S9 shared blocks
def crc_via_lfsr(data, poly, init, width, right, xorout):
    c = chip()
    if right:
        P, I = poly, init
    else:
        P, I = poly << (32 - width), init << (32 - width)
    wcfg(c, "POLY_LO", P & 0xFFFF)
    wcfg(c, "POLY_HI", P >> 16)
    wcfg(c, "LFSR_LO", I & 0xFFFF)
    wcfg(c, "LFSR_HI", I >> 16)
    wcfg(c, "LFCFG", right)
    wcfg(c, "SHCFG", 7 | right << 4)
    wcfg(c, "TAPCFG", 1 << 3)
    for b in data:
        c.eng[0].S = b
        for _ in range(8):
            ex(c, "p0 <- out")
    v = rcfg(c, "LFSR_LO") | rcfg(c, "LFSR_HI") << 16
    if not right:
        v >>= 32 - width
    return (v ^ xorout) & ((1 << width) - 1)


def test_s9_lfsr_crc_check_values():
    m = b"123456789"
    assert crc_via_lfsr(m, 0x07, 0, 8, 0, 0) == 0xF4
    assert crc_via_lfsr(m, 0x4599, 0, 15, 0, 0) == 0x059E
    assert crc_via_lfsr(m, 0x1021, 0xFFFF, 16, 0, 0) == 0x29B1
    assert crc_via_lfsr(m, 0xEDB88320, 0xFFFFFFFF, 32, 1, 0xFFFFFFFF) == 0xCBF43926


def test_s9_signal_flags_clear_wins_and_rcfg():
    c = chip()
    c.pulse(0, "exec", w1("sset #3"))
    c.pulse(1, "exec", w1("sset #3"))
    c.pulse(2, "exec", w1("sset #5"))
    c.step()
    c.step()
    assert c.sig == 0x28
    c.pulse(0, "exec", w1("sset #5"))
    c.pulse(3, "exec", w1("sclr #5"))
    c.step()
    c.step()
    assert c.sig == 0x08
    assert rcfg(c, "SIG", e=2) == 0x08


def test_s9_lfsr_lowest_engine_wins_and_write_beats_step():
    c = chip()
    c.eng[1].R[0], c.eng[2].R[0] = 0x1111, 0x2222
    c.pulse(2, "exec", encode("wcfg", creg=16, rs=0))
    c.pulse(1, "exec", encode("wcfg", creg=16, rs=0))
    c.step()
    c.step()
    assert c.lfsr & 0xFFFF == 0x1111
    wcfg(c, "TAPCFG", 1 << 3, e=3)
    c.eng[3].S = 0xFF
    c.eng[0].R[0] = 0x4444
    c.pulse(0, "exec", encode("wcfg", creg=16, rs=0))
    c.pulse(3, "exec", w1("p0 <- out"))
    c.step()
    c.step()
    assert c.lfsr & 0xFFFF == 0x4444


# ---------------------------------------------------------------- T2 micro-loop
def loop_arms(src, n=200, edges=()):
    img = assemble_text(src)
    c = boot(img, 0)
    s0 = c.cycle

    def inp(t, ch):
        return (1 if any(s0 + e <= t < s0 + e + 3 for e in edges) else 0) << 4 | 8, 0
    simulate(c, n, inp)
    e = c.eng[0]
    return img, c, [(f[0], f[1]) for f in e.fires if f[0] >= s0]


LOOPHDR = """
.engine 0
.init
        li    R0, #4
        wcfg  BIND01, R0
        li    R0, #%d
        wcfg  SHCFG, R0
        li    S, #0xA5
.entry go
.code
"""


def test_t2_rep_head_tail_each_clock():
    img, c, fires = loop_arms(LOOPHDR % 7 + """
go:     loop { p1 <- out }
        loop {
          p2 <- 1
          p2 <- out
        }
        @never : nop
""", 120)
    a = img.label("go")
    rep = [t for t, pc in fires if pc == a]
    pair = [t for t, pc in fires if pc in (a + 1, a + 2)]
    assert len(rep) == 8 and all(y - x == 1 for x, y in zip(rep, rep[1:]))
    assert len(pair) == 16 and all(y - x == 1 for x, y in zip(pair[1:], pair[2:])), pair   # first pass: tail fetched


def test_t2_entry_at_tail_and_head_end_first_pass():
    img, c, fires = loop_arms(LOOPHDR % 0 + """
go:     jmp   hd + 1
hd:     loop {
          p2 <- 1
          p2 <- out
        }
        p1 <- 1
        @never : nop
""", 120)
    a = img.label("hd") + 1
    tail = [t for t, pc in fires if pc == a]
    head = [t for t, pc in fires if pc == a - 1]
    # word length 1: the tail ends the loop at its first shift; never re-enters the head
    assert len(tail) == 1 and head == []
    img, c, fires = loop_arms(LOOPHDR % 1 + """
go:     jmp   hd + 1
hd:     loop {
          p2 <- 1
          p2 <- out
        }
        p1 <- 1
        @never : nop
""", 120)
    a = img.label("hd") + 1
    tail = [t for t, pc in fires if pc == a]
    head = [t for t, pc in fires if pc == a - 1]
    assert len(tail) == 2 and len(head) == 1 and 2 <= head[0] - tail[0] <= 5     # tail first pass: redirect
    img, c, fires = loop_arms(LOOPHDR % 0 + """
go:     loop {
          p2 <- out
          p2 <- 1
        }
        p1 <- 1
        @never : nop
""", 120)
    a = img.label("go")
    head = [t for t, pc in fires if pc == a]
    tl = [t for t, pc in fires if pc == a + 1]
    nx = [t for t, pc in fires if pc == a + 2]
    assert len(head) == 1 and tl == [] and 2 <= nx[0] - head[0] <= 5            # head ends on first pass: skips the tail


# ---------------------------------------------------------------- S2 q sources, MIS; S8 input filter
def test_s2_q_sources():
    for alt in range(4):
        c = chip()
        wcfg(c, "SHCFG", 7)
        wcfg(c, "TAPCFG", alt)
        c.eng[0].S = 0b10110000
        for _ in range(3):
            ex(c, "p0 <- out")                  # bits 1, 0, 1: PAR 0, B 1
        e = c.eng[0]
        D = (e.S >> 7) & 1                      # next data bit: 1
        B0, PAR0 = e.B, e.PAR
        ex(c, "p1 <- q")
        exp = {0: PAR0, 1: 1 - D, 2: 1 - B0, 3: B0}[alt]
        assert e.val[1] == exp, (alt, e.val[1], exp)
        if alt == 2:
            assert e.B == 1 - B0 and e.RUN == 1          # stuff bit enters the tap update


def test_s2_mis():
    # MIS = level(inpin) ^ LD, read through jmis under EXEC (a taken jump sets PC)
    c = chip()
    wcfg(c, "BIND01", 4)
    wcfg(c, "SHCFG", 7)                          # inpin p0, MSB first
    def mis(lvl):
        c.eng[0].PC = 100
        for _ in range(3):
            c.step(lvl << 4)
        ex(c, encode("jmis", rel=5), ui=lvl << 4)
        return int(c.eng[0].PC == 106)
    ex(c, "p1 <- 1")                             # constant: not a data action, LD stays 0
    assert (mis(0), mis(1)) == (0, 1)
    c.eng[0].S = 0x80
    ex(c, "p1 <- d")                             # LD <- 1
    assert (mis(0), mis(1)) == (1, 0)


def test_s8_input_filter():
    # a 2-clk pulse never reaches lvl through the filter; a 3-clk pulse does, 2 clk later than unfiltered
    def run(seq, filt):
        img = assemble_text("""
.engine 0
.init
        li    R0, #4
        wcfg  BIND01, R0
.entry go
.code
go:     @rise p0 : p1 <- 1
        @never : nop
""")
        c = boot(img, 0)
        c.filt = filt << 4
        s0 = c.cycle
        for k in range(60):
            v = seq[k] if k < len(seq) else 0
            c.step(v << 4 | 8)
        f = [x[0] - s0 for x in c.eng[0].fires if x[1] == img.label("go") and x[0] >= s0]
        return f
    assert run([0] * 10 + [1] * 2, 1) == []
    assert run([0] * 10 + [1] * 2, 0) != []
    a, b = run([0] * 10 + [1] * 3, 0), run([0] * 10 + [1] * 3, 1)
    assert a and b and b[0] - a[0] == 2
