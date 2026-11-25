# A5 and host-link checks (S6/H subset): .bin replayed through the host-link model vs a direct load.
import pytest
from harness import prog, assemble, PROG
from jspe_iss import Chip, HostLink, play_bin
import os


def idle_uio(chip):
    return (~chip.uio_oe | chip.uio_out) & 0xFF        # pull-ups where released


def link_step(chip, hl, ui=0x80):
    chip.step(ui | hl.level(chip) << 3, idle_uio(chip))
    hl.sample(chip)


def drain(c, hl, extra=0):
    n = 0
    while not hl.idle():
        link_step(c, hl)
        n += 1
        assert n < 2_000_000
    for _ in range(extra):
        link_step(c, hl)


def load_via_link(img, B=16, extra=2000):
    c = Chip(direct=False)
    hl = HostLink(16)
    if B != 16:
        hl.send(bytes([0x1B, 0x72, 0x01, {8: 1, 4: 2}[B], 0]))
        drain(c, hl, 40)
        hl.B = B
    for op in play_bin(img.bin_bytes()):
        hl.send(op)
    drain(c, hl, extra)
    return c, hl


def rdmem(c, hl, a, n):
    hl.got = []
    hl.send(bytes([0x1B, 0x71, a & 0xFF, a >> 8, n - 1]))
    k = 0
    while len(hl.got) < 2 + 2 * n:
        link_step(c, hl)
        k += 1
        assert k < 400_000, hl.got[:4]
    assert hl.got[:2] == [0x1B, 0xC1]
    b = hl.got[2:]
    return [b[2 * i] | b[2 * i + 1] << 8 for i in range(n)]


def load_direct(img, extra=2000):
    c = Chip()
    c.boot(img)
    for _ in range(extra):
        c.step(0x88, idle_uio(c))
    return c


def snapshot(c):
    out = {"mem": list(c.mem), "pad": list(c.pad), "slotmap": c.slotmap, "filt": c.filt, "pads_out": (c.uo & ~0x30, c.uio_out, c.uio_oe)}
    for k, e in enumerate(c.eng):
        out["e%d" % k] = (e.R[:], e.S, e.P, e.C, e.L, e.cfg[:], e.PC, e.run, e.waiting, e.ovr, e.trap, e.halted,
                          e.oe[:], e.val[:], e.fol[:], e.IR, e.irv)
    return out


IMAGES = ["uart_tx", "uart_rx", "spi_ctrl", "spi_tgt", "i2c_ctrl", "i2c_tgt", "ws2812", "manchester"]


@pytest.mark.parametrize("name", IMAGES)
def test_a5_bin_replay_equals_direct_load(name):
    img = prog(name)
    cl, hl = load_via_link(img)
    cd = load_direct(img)
    sl, sd = snapshot(cl), snapshot(cd)
    diff = [k for k in sd if sl[k] != sd[k]]
    assert not diff, (name, diff, [(sl[k], sd[k]) for k in diff[:2]])
    assert all(b not in (0x8C,) for b in hl.got), hl.got
    words = sorted(img.words)
    got = rdmem(cl, hl, words[0], words[-1] - words[0] + 1)
    assert all(got[a - words[0]] == img.words[a] for a in words)


@pytest.mark.parametrize("B", [16, 8, 4])
def test_a5_multi_engine_image_at_link_rates(B):
    p = [os.path.join(PROG, f + ".s") for f in ("uart_tx", "uart_rx", "spi_tgt", "i2c_tgt")]
    img = assemble(p, {"uart_tx.s:ENG": "1", "uart_rx.s:ENG": "2", "spi_tgt.s:ENG": "3", "i2c_tgt.s:ENG": "0",
                       "uart_tx.s:ORG": "200", "uart_rx.s:ORG": "220"})
    sd = snapshot(load_direct(img))
    cl, hl = load_via_link(img, B=B)
    sl = snapshot(cl)
    assert [k for k in sd if sl[k] != sd[k]] == []
    assert rdmem(cl, hl, 0, 256) == [cl.mem[a] if cl.mem[a] is not None else 0 for a in range(256)]


# ---------------------------------------------------------------- S6 host control through the link
from harness import assemble_text      # noqa: E402

ESC = 0x1B
S6 = """
.engine 0
.init
        li    R0, #4
        wcfg  BIND01, R0
.entry go
.code
go:     p1 <- 1
        @rise p0 : p1 <- 0
        p2 <- 1
        p2 <- 0
        @never : nop
.engine 1
.entry g1
.code
.org 40
g1:     p0 <- 1
        @never : nop
buf:    .word 0x4321
"""


def s6_chip():
    img = assemble_text(S6)
    c = Chip(direct=False)
    c.load_image(img)
    for e in c.eng:
        e.trace = True
    for e in (0, 1):
        for w in img.exec_words(e):
            c.exec_word(e, w)
    hl = HostLink(16)
    for _ in range(400):                           # H_RX was low during the injected init: let that frame end
        link_step(c, hl)
    hl.got = []
    return img, c, hl


def send(c, hl, data, ui=0x80, extra=40, mon=None):
    hl.send(bytes(data))
    k = 0
    while not hl.idle() or k < extra:
        k += hl.idle()
        link_step(c, hl, ui)
        if mon:
            mon(c)


def test_s6_run_mask_same_clock_and_step():
    img, c, hl = s6_chip()
    rise = {}

    def mon(ch):
        for e in (0, 1):
            if ch.eng[e].run and e not in rise:
                rise[e] = ch.cycle
    send(c, hl, [ESC, 0x33], mon=mon)
    assert len(rise) == 2 and rise[0] == rise[1]
    send(c, hl, [ESC, 0x21])                       # STOP e0 at the armed guard
    e = c.eng[0]
    assert (e.PC, e.irv, e.run) == (img.label("go") + 1, 1, 0)
    send(c, hl, [ESC, 0x41])                       # STEP: the guard waits, an edge after STEP fires it
    for _ in range(20):
        link_step(c, hl, 0x90)
    assert (e.val[1], e.run, e.PC) == (0, 0, img.label("go") + 2)
    send(c, hl, [ESC, 0x41], ui=0x90)              # STEP: exactly one instruction
    assert (e.val[2], e.run, e.PC) == (1, 0, img.label("go") + 3)
    send(c, hl, [ESC, 0x31], ui=0x90)              # RUN resumes there
    assert e.val[2] == 0 and e.run == 1


@pytest.mark.nolint
def test_s6_exec_guard_wait_memory_jump_and_pending_rules():
    img, c, hl = s6_chip()
    # EXEC with a guard wait: stays pending; RUN, STEP, EXEC to it are link errors; STOP cancels it
    w = encode_text("@rise p0 : p3 <- 1")
    hl.got = []
    send(c, hl, [ESC, 0x60, w & 0xFF, w >> 8])
    assert c.eng[0].xm == 1
    send(c, hl, [ESC, 0x31, ESC, 0x41, ESC, 0x60, 0, 0], extra=400)
    assert hl.got.count(0x8C) >= 1 and c.eng[0].run == 0 and c.eng[0].xm == 1
    send(c, hl, [ESC, 0x21])
    assert c.eng[0].xm == 0 and c.eng[0].irv == 0
    send(c, hl, [ESC, 0x21], ui=0x90, extra=10)
    assert c.eng[0].val[3] == 0                    # cancelled: no effect when the edge comes later
    # EXEC with a memory access, then with a jump: PC set by the jump only
    c.eng[0].R[1] = img.label("buf")
    pc = c.eng[0].PC
    w = encode_text("ld R2, [R1]")
    send(c, hl, [ESC, 0x60, w & 0xFF, w >> 8])
    assert c.eng[0].R[2] == 0x4321 and c.eng[0].PC == pc
    w = encode_text("jmp 50", org=0)
    send(c, hl, [ESC, 0x60, w & 0xFF, w >> 8])
    assert c.eng[0].PC == 50


def test_s6_rst_and_exec_to_running_engine():
    img, c, hl = s6_chip()
    send(c, hl, [ESC, 0x33])
    hl.got = []
    send(c, hl, [ESC, 0x61, 0, 0], extra=400)      # EXEC to a running engine: error, ignored
    assert 0x8C in hl.got
    send(c, hl, [ESC, 0x23, ESC, 0x53])
    e = c.eng[0]
    assert (e.run, e.PC, e.irv, e.oe, e.val) == (0, 0, 0, [0] * 4, [0] * 4) and c.eng[1].run == 0


def encode_text(text, org=None):
    src = ".engine 0\n.code\n" + ("" if org is None else ".org %d\n" % org) + text + "\n"
    img = assemble_text(src)
    return img.words[min(img.words)]


# ---------------------------------------------------------------- H4: each row of HOSTIF.md 9
def raw_frame(c, hl, byte, stop=1, B=16):
    bits = [0] + [(byte >> k) & 1 for k in range(8)] + [stop]
    for b in bits:
        for _ in range(B):
            c.step(0x80 | b << 3, idle_uio(c))
            hl.sample(c)
    for _ in range(4 * B):
        c.step(0x88, idle_uio(c))
        hl.sample(c)


def has_sync(got):
    return any(got[k] == ESC and got[k + 1] == 0xC4 for k in range(len(got) - 1))


def sync_ok(c, hl):
    hl.got = []
    send(c, hl, [ESC, 0x74], extra=400)
    return has_sync(hl.got)


def notices(hl):
    return [hl.got[k + 1] for k in range(len(hl.got) - 1) if hl.got[k] == ESC and 0x80 <= hl.got[k + 1] <= 0x8C]


H4 = """
.engine 0
.entry go
.code
go:     @never : nop
.engine 1
.entry g1
.code
.org 40
g1:     halt
        @never : nop
.engine 2
.entry g2
.code
.org 60
g2:     .word 0xFC00
"""


def h4_chip():
    img = assemble_text(H4)
    c = Chip(direct=False)
    c.load_image(img)
    for e in (0, 1, 2):
        for w in img.exec_words(e):
            c.exec_word(e, w)
    hl = HostLink(16)
    for _ in range(400):
        link_step(c, hl)
    hl.got = []
    return img, c, hl


def test_d085_reply_op_replaces_reply_in_progress():
    # RDREG while an RDMEM reply is still running: the rest of the old reply is dropped (HOSTIF 3)
    c = Chip(direct=False)
    hl = HostLink(16)
    for a in range(16):
        c.mem[a] = 0x1100 + a
    old = [0x1B, 0xC1] + [x for a in range(16) for x in (a, 0x11)]
    hl.send(bytes([0x1B, 0x71, 0x00, 0x00, 15]))
    k = 0
    while len(hl.got) < 4:
        link_step(c, hl)
        k += 1
        assert k < 20_000
    hl.send(bytes([0x1B, 0x73, 0x00]))
    drain(c, hl, 3000)
    lk = c.link
    assert not (lk.reply or lk.reply_pending or lk.rd_queue or lk.rd_flight is not None)
    got = hl.got
    assert got[-4:] == [0x1B, 0xC3, 0x10, 0x4A], got
    assert 4 <= len(got) - 4 < len(old) and got[:-4] == old[:len(got) - 4], got


def test_h4_error_rows():
    img, c, hl = h4_chip()
    raw_frame(c, hl, 0x55, stop=0)                       # stop bit low
    send(c, hl, [ESC, 0x7F], extra=400)                  # unknown op
    send(c, hl, [0x42], extra=400)                       # data, no engine selected
    n = notices(hl)
    assert n.count(0x8C) == 3, n
    hl.got = []
    raw_frame(c, hl, 0x00, stop=0)                       # break inside a command: abandoned, one error
    send(c, hl, [ESC, 0x74], extra=400)
    assert notices(hl).count(0x8C) == 1 and has_sync(hl.got)
    hl.got = []
    send(c, hl, [ESC, 0x00, 1, 2, 3, 4, 5], extra=400)   # RX FIFO of stopped e0 overflows
    assert 0x80 in notices(hl) and c.eng[0].ovr == 1
    hl.got = []
    send(c, hl, [ESC, 0x37], extra=1500)                 # RUN e0-e2: e1 halts, e2 traps
    n = notices(hl)
    assert 0x81 not in n and 0x85 in n and 0x8A in n, [hex(x) for x in n]
    hl.got = []
    send(c, hl, [ESC, 0x72, 0x02, 0x00, 0x00], extra=400)  # SLOTMAP with e0 running
    assert notices(hl) == [0x8C] and c.slotmap == 0xE4
    assert sync_ok(c, hl)


# ---------------------------------------------------------------- H3: full rate into a stopped stream, then ESC STOP
def test_h3_full_rate_stopped_stream_then_stop():
    img = assemble_text("""
.engine 0
.entry l
.code
l:      @hrx  : pull R0
        @htx  : push R0
        jmp   l
""")
    c = Chip(direct=False)
    c.load_image(img)
    for w in img.exec_words(0):
        c.exec_word(0, w)
    hl = HostLink(16)
    for _ in range(400):
        link_step(c, hl)
    hl.got = []
    data = [0x10 + k for k in range(10)] + [0x1B]
    hl.send(bytes([ESC, 0x00]))
    for b in data:
        hl.send(bytes([ESC, 0x1B]) if b == 0x1B else bytes([b]), is_data=True)
    for _ in range(160 * 12):
        link_step(c, hl)
    # stopped at 3 entries: H_RDY high (fewer than 2 free); the rest waits in the host
    assert len(c.rx[0].q) == 3 and (c.uo >> 5) & 1 == 1 and c.eng[0].ovr == 0
    q, hl.q = hl.q, []
    send(c, hl, [q[0][0]])                              # a host that stops one byte late: still no loss
    assert len(c.rx[0].q) == 4 and c.eng[0].ovr == 0
    send(c, hl, [ESC, 0x21, ESC, 0x74], extra=600)     # command bytes ignore H_RDY: accepted while not ready
    assert any(hl.got[k] == ESC and hl.got[k + 1] == 0xC4 for k in range(len(hl.got) - 1))
    hl.got = []
    send(c, hl, [ESC, 0x31], extra=10)                 # RUN: the echo drains the stream
    hl.q = q[1:]                                       # the held data bytes follow H_RDY again; nothing was lost
    for _ in range(160 * 30):
        link_step(c, hl)
    echoed = [x[1] for x in decode_stream(hl.got) if x[0] == 0]
    assert echoed == data and c.eng[0].ovr == 0, echoed


def decode_stream(got):
    from test_blocks import decode_stream as d
    return d(got)


# ---------------------------------------------------------------- D080: escaped 0x1B into an RX FIFO with one free entry
@pytest.mark.parametrize("B", [4, 8, 16])
def test_d080_escaped_byte_into_one_free_entry(B):
    # uart_tx.s (E0) looped to uart_rx.s (E1); at B = 4 the 5th byte (ESC ESC) arrives with 1 free entry
    img = assemble([os.path.join(PROG, f + ".s") for f in ("uart_tx", "uart_rx")])
    c, hl = load_via_link(img, B=B, extra=200)
    hl.got = []
    data = [0x55, 0xA3, 0x00, 0xFF, ESC, 0x0F, 0x0E]
    hl.send(bytes([ESC, 0x00]))
    for b in data:
        hl.send(bytes([ESC, ESC]) if b == ESC else bytes([b]), is_data=True)
    held = False
    for _ in range(30000):
        c.step(hl.level(c) << 3 | (c.uo & 1) << 7, idle_uio(c))
        hl.sample(c)
        held |= c.link.pstate == "op" and c.link.sel == 0 and c.rx[0].free() == 1 and c.link.h_rdy_next == 1
    out = decode_stream(hl.got)
    assert [x[1] for x in out if x[0] == 1] == data, out
    assert not [x for x in out if x[0] == "note"], out
    assert c.eng[0].ovr == 0 and c.eng[1].ovr == 0
    assert held or B != 4, "case not reached"
