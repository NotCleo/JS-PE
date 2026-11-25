# S1, S9, S6/S7 subset: instruction semantics by host EXEC against plain Python arithmetic.
import random
import pytest
from harness import assemble_text, boot
from jspe_asm import encode
from jspe_iss import Chip

M = 0xFFFF


def chip():
    c = Chip()
    c.mem = [0] * 512
    return c


def setregs(c, e, **v):
    eng = c.eng[e]
    for k, x in v.items():
        if k in ("R0", "R1", "R2"):
            eng.R[int(k[1])] = x
        else:
            setattr(eng, k, x)


def regs(eng):
    return [eng.R[0], eng.R[1], eng.R[2], eng.S, eng.P, eng.C, eng.T, eng.L]


def ref_alu(op, a, b, cy):
    if op == "add":
        r = a + b
        return r & M, r >> 16 & 1
    if op == "adc":
        r = a + b + cy
        return r & M, r >> 16 & 1
    if op in ("sub", "cmp"):
        return (a - b) & M, int(a >= b)
    if op in ("and", "tst"):
        return a & b, cy
    if op == "or":
        return a | b, cy
    if op == "xor":
        return a ^ b, cy
    if op == "shl":
        return (b << 1) & M, b >> 15
    if op == "shr":
        return b >> 1, b & 1
    if op == "rlc":
        return ((b << 1) | cy) & M, b >> 15
    if op == "rrc":
        return (cy << 15) | (b >> 1), b & 1


@pytest.mark.nolint
def test_s1_alu_random():
    rnd = random.Random(7)
    ops = ("add", "adc", "sub", "cmp", "and", "or", "xor", "tst", "shl", "shr", "rlc", "rrc")
    corner = (0, 1, 0x7FFF, 0x8000, 0xFFFF)
    for op in ops:
        for k in range(120):
            a = corner[k % 5] if k < 25 else rnd.randrange(1 << 16)
            b = corner[k // 5 % 5] if k < 25 else rnd.randrange(1 << 16)
            cy = rnd.randrange(2)
            rd, rs = rnd.choice([(0, 1), (1, 2), (2, 0), (3, 1), (0, 4), (5, 2)])
            c = chip()
            eng = c.eng[0]
            init = [rnd.randrange(1 << 16) for _ in range(6)]
            setregs(c, 0, R0=init[0], R1=init[1], R2=init[2], S=init[3], P=init[4], C=init[5])
            vals = regs(eng)
            vals[rd], vals[rs] = a, b
            setregs(c, 0, **{("R0", "R1", "R2", "S", "P", "C")[rd]: a, ("R0", "R1", "R2", "S", "P", "C")[rs]: b})
            eng.Cy = cy
            c.exec_word(0, encode(op, rd=rd, rs=rs))
            r, ncy = ref_alu(op, a, b, cy)
            exp = list(vals)
            if op not in ("cmp", "tst"):
                exp[rd] = r
            assert regs(eng)[:6] == exp[:6], (op, hex(a), hex(b), rd, rs)
            assert (eng.Z, eng.Cy) == (int(r == 0), ncy), (op, hex(a), hex(b), cy)


@pytest.mark.nolint
def test_s1_alu_rd_t_and_l():
    c = chip()
    eng = c.eng[0]
    eng.T = 5
    setregs(c, 0, R0=5)
    c.exec_word(0, encode("sub", rd=6, rs=0))          # rd = T: result dropped, flags set
    assert eng.Z == 1 and eng.Cy == 1
    setregs(c, 0, R1=0x1FF, R2=3)
    c.exec_word(0, encode("add", rd=7, rs=1))          # L takes the low 9 bits
    c.exec_word(0, encode("add", rd=7, rs=2))
    assert eng.L == 2 and eng.Cy == 0
    c.exec_word(0, encode("mov", rd=0, rs=7, g=0))     # L reads zero-extended
    assert eng.R[0] == 2


@pytest.mark.nolint
def test_s1_immediates():
    rnd = random.Random(3)
    for _ in range(300):
        rd = rnd.randrange(4)
        v, imm = rnd.randrange(1 << 16), rnd.randrange(256)
        c = chip()
        eng = c.eng[0]
        setregs(c, 0, **{("R0", "R1", "R2", "S")[rd]: v})
        eng.Z, eng.Cy = 1, 1
        op = rnd.choice(("li", "lih", "addi", "cmpi"))
        c.exec_word(0, encode(op, rd=rd, imm=imm))
        got = regs(eng)[rd]
        if op == "li":
            assert got == imm and (eng.Z, eng.Cy) == (1, 1)
        elif op == "lih":
            assert got == (v & 0xFF) | imm << 8 and (eng.Z, eng.Cy) == (1, 1)
        elif op == "addi":
            s = imm - 256 if imm & 0x80 else imm
            r = v + (s & M)
            assert got == r & M and eng.Z == int(r & M == 0) and eng.Cy == r >> 16 & 1, (v, imm)
        else:
            assert got == v and eng.Z == int(v == imm) and eng.Cy == int(v >= imm), (v, imm)


@pytest.mark.nolint
def test_s1_mov_and_timer_capture():
    c = chip()
    eng = c.eng[0]
    setregs(c, 0, R2=0xBEEF, T=0x1234)
    c.exec_word(0, encode("mov", rd=4, rs=2, g=0))
    c.exec_word(0, encode("mov", rd=1, rs=6, g=0))
    c.exec_word(0, encode("mov", rd=6, rs=2, g=0))     # write to T ignored
    assert eng.P == 0xBEEF and eng.R[1] == eng.T and eng.T != 0xBEEF


@pytest.mark.nolint
def test_s1_ld_st_modes():
    for mode in range(4):
        for rb in range(4):
            for rd in range(4):
                c = chip()
                eng = c.eng[0]
                for a in range(512):
                    c.mem[a] = (a * 37 + 11) & M
                base, s = 100, 7
                setregs(c, 0, R0=0x1111, R1=0x2222, R2=0x3333, S=s)
                setregs(c, 0, **{("R0", "R1", "R2", "S")[rb]: base if rb != 3 else s})
                b = regs(eng)[rb]
                sv = eng.S
                addr = {0: b, 1: b + 1, 2: b + sv, 3: b + sv}[mode] & 511
                wb = {0: None, 1: b + 1, 2: None, 3: b + sv}[mode]
                before = regs(eng)
                c.exec_word(0, encode("ld", rd=rd, rb=rb, mode=mode << 0))
                exp = list(before)
                if wb is not None:
                    exp[rb] = wb & M
                exp[rd] = c.mem[addr]                    # rd == rb: loaded value wins
                assert regs(eng)[:4] == exp[:4], ("ld", mode, rb, rd)
                # store of rs = rd register
                c2 = chip()
                e2 = c2.eng[0]
                setregs(c2, 0, R0=0x1111, R1=0x2222, R2=0x3333, S=s)
                setregs(c2, 0, **{("R0", "R1", "R2", "S")[rb]: base if rb != 3 else s})
                before = regs(e2)
                c2.exec_word(0, encode("st", rs=rd, rb=rb, mode=mode))
                assert c2.mem[addr] == before[rd], ("st", mode, rb, rd)
                exp = list(before)
                if wb is not None:
                    exp[rb] = wb & M
                assert regs(e2)[:4] == exp[:4]


@pytest.mark.nolint
def test_s1_ld_rd_t_dropped_and_address_wraps():
    c = chip()
    eng = c.eng[0]
    c.mem[5] = 0xAAAA
    setregs(c, 0, R0=0x0204, T=9)                       # 0x205 & 511 = 5
    c.exec_word(0, encode("ld", rd=1, rb=0, mode=1))
    assert eng.R[1] == 0xAAAA and eng.R[0] == 0x205
    c.exec_word(0, encode("ld", rd=6, rb=0, mode=0))
    assert eng.T == 9


@pytest.mark.nolint
def test_s1_rx_fifo_ops_and_overrun():
    c = chip()
    eng = c.eng[0]
    setregs(c, 0, R0=0x1234)
    c.exec_word(0, encode("push", rs=0, g=0))
    c.exec_word(0, encode("pushh", rs=0, g=0))
    c.exec_word(0, encode("evt", c=0x2A, g=0))
    c.step()
    assert [v for _, v in c.host_out[0]] == [0x34, 0x12, 0x100 | 0x2A]
    setregs(c, 0, R1=0xFFFF)
    c.exec_word(0, encode("pull", rd=1, g=0))           # empty: unchanged, OVR
    assert eng.R[1] == 0xFFFF and eng.ovr == 1
    c.exec_word(0, encode("ovclr", g=0))
    assert eng.ovr == 0
    c.host_in[0] += [0x5A, 0xC3]
    c.step()
    c.step()
    c.exec_word(0, encode("pull", rd=1, g=0))
    c.exec_word(0, encode("pullh", rd=1, g=0))
    assert eng.R[1] == 0xC35A and eng.ovr == 0


@pytest.mark.nolint
def test_s1_push_full_fifo_overrun():
    c = chip()
    c.tx_block[0] = True
    eng = c.eng[0]
    for k in range(4):
        c.exec_word(0, encode("push", rs=0, g=0))
    assert eng.ovr == 0
    c.exec_word(0, encode("push", rs=0, g=0))
    assert eng.ovr == 1 and len(c.tx[0].q) == 4


def test_s1_undefined_traps():
    for w in (0xD870, 0xF0C0, 0xFC00):
        c = chip()
        c.exec_word(0, w)
        assert c.eng[0].trap == 1, hex(w)


@pytest.mark.nolint
def test_s1_st_reads_rs_at_completion():
    # D066: `st T` stores the count of the grant clock, in every slot phase
    seen = set()
    for ph in range(4):
        c = chip()
        eng = c.eng[0]
        setregs(c, 0, R0=40, R1=1000)
        c.exec_word(0, encode("tset", rs=1, g=0))
        while c.slot != ph:
            c.step()
        c.pulse(0, "exec", encode("st", rs=6, rb=0, mode=0))
        c.step()
        t_fire = eng.T
        for _ in range(8):
            t_now = eng.T
            c.step()
            if not eng.xm:
                break
        assert c.mem[40] == t_now, (ph, c.mem[40], t_now)
        seen.add(t_fire - t_now)
    assert len(seen) > 1                                # the slot wait is visible in the stored value


def test_s6_exec_drops_pending_memory_access():
    # D067: STOP while ld waits for its slot, EXEC: the word executes, the ld runs once after RUN
    img = assemble_text("""
.engine 0
.init
        li    R0, #buf
        li    R2, #0
.entry go
.code
go:     ld    R1, [R0+1]!
        @never : nop
buf:    .word 0x1111, 0x2222, 0x3333
""")
    buf = img.label("buf")
    hit = 0
    for ph in range(4):
        for s in range(1, 12):
            c = boot(img, ph)
            eng = c.eng[0]
            for _ in range(s):
                c.step()
            c.pulse(0, "stop")
            c.step()
            pending = eng.memop is not None and not eng.memop["granted"]
            c.step()
            c.exec_word(0, encode("li", rd=2, imm=0x55))
            assert eng.R[2] == 0x55, (ph, s)
            if pending:
                hit += 1
                assert eng.R[0] == buf and eng.memop is None
            c.pulse(0, "run")
            for _ in range(40):
                c.step()
            assert (eng.R[0], eng.R[1]) == (buf + 1, 0x2222), (ph, s)
    assert hit > 0


def test_s6_exec_keeps_loop_pair_order():
    # D069 (1): STOP with a pair loaded, EXEC, RUN: the head is refetched with its own tail
    img = assemble_text("""
.engine 0
.init
        li    R0, #4
        wcfg  BIND01, R0
        li    R0, #7
        wcfg  SHCFG, R0
        li    S, #0
.entry go
.code
go:     loop {
          @rise p0 : shin
          @fall p0 : p1 <- d
        }
        p2 <- 1
        @never : nop
""")
    go = img.label("go")
    for stop_at in (40, 43, 46, 52, 55):
        c = boot(img, 0)
        eng = c.eng[0]
        eng.trace = True
        t0 = c.cycle

        def ui():
            return (1 if ((c.cycle - t0) // 6) % 2 else 0) << 4 | 8
        for _ in range(stop_at):
            c.step(ui(), 0)
        c.pulse(0, "stop")
        c.step(ui(), 0)
        c.step(ui(), 0)
        c.pulse(0, "exec", encode("li", rd=2, imm=1))
        for _ in range(4):
            c.step(ui(), 0)
        c.pulse(0, "run")
        for _ in range(200):
            c.step(ui(), 0)
        pcs = [f[1] for f in eng.fires if f[0] > t0 and f[3] == "pin"]
        assert set(pcs[:-1]) == {go, go + 1} and pcs[-1] == go + 2, (stop_at, pcs)
        assert sum(1 for p in pcs if p == go) == 8, (stop_at, pcs)


def test_s6_run_after_reset_runs_word0_once():
    # D109: FP = PC + 1 from reset, so RUN with no EXEC runs word 0 once (after chip reset and after RST)
    c = chip()
    eng = c.eng[0]
    c.mem[0] = encode("evt", c=5)
    c.mem[1] = encode("halt")
    for via_rst in (False, True):
        if via_rst:
            c.pulse(0, "rst")
            c.step()
            assert eng.PC == 0 and eng.FP == 1
        c.pulse(0, "run")
        for _ in range(30):
            c.step()
        assert eng.halted == 1 and eng.run == 0 and eng.PC == 2
        got = [x for _, x in c.host_out[0]] + c.tx[0].q
        assert got == [0x105], got
        c.host_out[0].clear()


def test_s6_trap_and_run_end_step_mode():
    # D069 (2)
    c = chip()
    eng = c.eng[0]
    c.mem[0] = 0xFC00
    c.exec_word(0, encode("jmpa", addr=0))
    c.pulse(0, "step")
    for _ in range(12):
        c.step()
    assert eng.trap == 1 and eng.run == 0 and eng.stepm == 0
    c.mem[0] = 0
    eng.trap = 0
    c.exec_word(0, encode("jmpa", addr=0))
    c.pulse(0, "step")
    c.step()
    c.pulse(0, "run")
    for _ in range(40):
        c.step()
    assert eng.run == 1 and eng.PC > 2


@pytest.mark.nolint
def test_s6_stop_cancels_injected_memory_access():
    # D069 (3): the injected st never writes, also when STOP falls in a later slot clock
    for delay in range(1, 7):
        c = chip()
        eng = c.eng[0]
        c.slotmap = 0x55                                # engine 0 owns no slot
        setregs(c, 0, R0=100, R1=0xBEEF)
        c.pulse(0, "exec", encode("st", rs=1, rb=0, mode=1))
        for _ in range(delay):
            c.step()
        c.pulse(0, "stop")
        c.step()
        c.slotmap = 0xE4
        assert eng.xm == 0 and eng.memop is None and eng.flight is None
        c.pulse(0, "run")
        for _ in range(20):
            c.step()
        assert c.mem[101] == 0 and eng.R[0] == 100, delay
    hit = 0
    for ph in range(4):                                 # STOP in every clock around the grant of an injected ld
        for delay in range(1, 6):
            c = chip()
            eng = c.eng[0]
            c.mem[101] = 0x1234
            setregs(c, 0, R0=100, R1=0x5555)
            while c.slot != ph:
                c.step()
            c.pulse(0, "exec", encode("ld", rd=1, rb=0, mode=1))
            for _ in range(delay):
                c.step()
            grant = eng.memop is not None and not eng.memop["granted"] and c.slot == 0
            arrive = eng.flight is not None
            done = not eng.xm
            c.pulse(0, "stop")
            c.step()
            assert eng.xm == 0 and eng.memop is None and eng.flight is None and eng.PC == 0
            c.step()
            c.step()
            assert (eng.R[1], eng.R[0]) == ((0x1234, 101) if done or arrive else (0x5555, 100)), (ph, delay)
            hit += grant
    assert hit > 0                                      # STOP in the grant clock: the read is dropped
