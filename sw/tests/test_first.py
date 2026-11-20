# docs/history/REVIEW.md 6 "test first": deadline paths in all slot phases, auto-transfer with apull, watcher during ld/st,
# pad-referred timing numbers. Each case appends "name pass/FAIL measured" to build/test_first_results.txt.
import pytest
import os
from harness import prog, boot, simulate, deadline_delays, changes, record, assemble_text, PROG, AsmError
from jspe_asm import Assembler
from models import UartSender, SpiController, ManchesterSender, uart_decode, level_at

PHASES = (0, 1, 2, 3)


def uio_spi(ctrl, miso_bit=2, sck=3, cs=0, mosi=1):
    def inputs(t, chip):
        miso = (chip.uio_out >> miso_bit) & 1 if (chip.uio_oe >> miso_bit) & 1 else 0
        return 0x08, ctrl.sck(t) << sck | ctrl.cs(t) << cs | ctrl.mosi(t) << mosi | miso << miso_bit
    return inputs


class Seq:
    # several SPI transactions one after another
    def __init__(self, ctrls):
        self.c = ctrls

    def pick(self, t):
        for x in self.c:
            if t < x.cs_hi + 1:
                return x
        return self.c[-1]

    def sck(self, t):
        return self.pick(t).sck(t)

    def cs(self, t):
        return self.pick(t).cs(t) if t >= self.c[0].cs_lo else 1

    def mosi(self, t):
        return self.pick(t).mosi(t)


def spi_transactions(T, cmds, nresp, phase=0.0, t0=40.0, gap_cs=40.0):
    ctrls, t = [], t0
    for c in cmds:
        x = SpiController(T, 0, [c] + [0] * nresp, phase=phase, t0=t)
        ctrls.append(x)
        t = x.cs_hi + gap_cs - phase
    return ctrls


# ---------------------------------------------------------------- 1. the nine .deadline paths
def check_deadlines(img, chips, label, engines=None):
    rows = []
    for d in img.deadlines:
        if engines is not None and d["engine"] not in engines:
            continue
        ms = []
        for c in chips:
            ms += deadline_delays(c, d)
        late = sum(1 for c in chips for f in c.eng[d["engine"]].fires if f[1] == d["target"] and f[2])
        ovr = sum(c.eng[d["engine"]].ovr for c in chips)
        mx = max(ms) if ms else None
        ok = bool(ms) and mx <= d["bound"] and d["bound"] <= d["n"]
        name = "deadline %s:%d (%s) n=%d" % (d["file"].split("/")[-1], d["line"], d["label"] or "straight", d["n"])
        record(name + " " + label, ok, "max %s clk over %d events, 4 slot phases, %s, late %d, OVR %d" %
               (mx, len(ms), "zero slack" if mx == d["n"] else "slack %s" % (None if mx is None else d["n"] - mx),
                late, ovr))
        rows.append((d, mx, ok, late, ovr))
    return rows


def test_deadline_uart_rx():
    img = prog("uart_rx", BIT=15)
    chips = []
    for ph in PHASES:
        c = boot(img, ph)
        s = UartSender(15, [0x55, 0x00, 0xFF, 0xA3, 0x3C] * 3, t0=c.cycle + 20 + ph * 0.25)
        simulate(c, int(s.end) + 60, lambda t, ch: (s.level(t) << 7 | 8, 0))
        assert c.host_bytes(1) == [0x55, 0x00, 0xFF, 0xA3, 0x3C] * 3
        chips.append(c)
    rows = check_deadlines(img, chips, "BIT 15")
    assert all(r[2] and r[3] == 0 and r[4] == 0 for r in rows)
    # BIT 14: the stop guard may wait < 9 clk, the jmp target is not prefetched: rejected (D100); BIT 12: exceeded
    with pytest.raises(AsmError):
        prog("uart_rx", BIT=14)
    a = Assembler([("uart_rx.s", open(os.path.join(PROG, "uart_rx.s")).read())], {"BIT": "12"})
    a.check = False
    img = a.run()
    ms = []
    for ph in PHASES:
        c = boot(img, ph)
        s = UartSender(12, [0x55, 0xA3, 0x00] * 2, t0=c.cycle + 20 + ph * 0.25)
        simulate(c, int(s.end) + 60, lambda t, ch: (s.level(t) << 7 | 8, 0))
        ms += deadline_delays(c, img.deadlines[0])
    record("deadline uart_rx BIT 12 (below the limit)", max(ms) > 1, "max %d clk > bound 1" % max(ms))
    assert max(ms) > 1


def test_deadline_uart_tx():
    data = [0x55, 0xA3, 0x00, 0xFF, 0x0F, 0xF0, 0x81]
    for bit, expect_late in ((11, False), (10, False), (9, True)):
        img = prog("uart_tx", BIT=bit)
        chips = []
        for ph in PHASES:
            c = boot(img, ph)
            simulate(c, 30)
            c.host_in[0] += data
            simulate(c, bit * 10 * (len(data) + 2))
            ch = changes(c, lambda uo, a, b: uo & 1, 1)
            dec = uart_decode([x for x in ch if x[0] > 30], bit)
            starts = [x[0] for x in dec]
            chips.append(c)
            if not expect_late:
                assert [x[1] for x in dec] == data
                assert all(b - a == 10 * bit for a, b in zip(starts, starts[1:])), starts
        if expect_late:
            late = sum(c.eng[0].late for c in chips)
            record("deadline uart_tx BIT 9 (two clk below the header limit)", late > 0, "late fires %d" % late)
            assert late > 0
        else:
            rows = check_deadlines(img, chips, "BIT %d, back to back" % bit)
            assert all(r[2] and r[3] == 0 and r[4] == 0 for r in rows)


def run_spi_tgt(img, T, cmds, nresp, slot, phase=0.0, table=None):
    c = boot(img, slot)
    tb = img.label("table")
    if table:
        for k, v in enumerate(table):
            c.mem[tb + k] = v
    ctrls = spi_transactions(T, cmds, nresp, phase=phase, t0=c.cycle + 40)
    seq = Seq(ctrls)
    simulate(c, int(ctrls[-1].cs_hi) + 40, uio_spi(seq))
    miso = changes(c, lambda uo, out, oe: (out >> 2) & (oe >> 2) & 1)
    got = [x.sample(miso) for x in ctrls]
    return c, ctrls, got, miso


def test_deadline_spi_tgt():
    img = prog("spi_tgt")
    tab = [0x00A5, 0x005A, 1, 2, 3, 4, 5, 6]
    chips = []
    for ph in PHASES:
        c, ctrls, got, _ = run_spi_tgt(img, 25, [0, 1, 3], 3, ph)
        for cmd, g in zip([0, 1, 3], got):
            assert g == [0] + tab[cmd:cmd + 3], (ph, cmd, g)
        chips.append(c)
    rows = check_deadlines(img, chips, "2 MHz, 3 response bytes")
    assert all(r[2] and r[4] == 0 for r in rows)


def test_spi_tgt_s_load_t6():
    # last command bit sampled in clock t -> S holds the table word by t + 5, MISO pad changes at edge t + 8 (D081)
    img = prog("spi_tgt")
    lp = img.label("cmd")          # first loop { @rise p0 : shin }
    worst_s, worst_pad = 0, 0
    for ph in PHASES:
        for sub in (0.0, 0.25, 0.5, 0.75):
            c, ctrls, got, miso = run_spi_tgt(img, 25, [0], 1, ph, phase=sub, table=[0xAA])
            assert got[0] == [0, 0xAA]
            fires = [f[0] for f in c.eng[0].fires if f[1] == lp]
            t = fires[7]
            rise = [tc for tc, v in miso if v == 1 and tc > t][0]
            worst_pad = max(worst_pad, rise - t)
    ok = worst_pad <= 8
    record("spi_tgt 8th edge (clock t) -> MISO pad change", ok, "edge t + %d (claim t + 8), all phases" % worst_pad)
    assert ok


def test_deadline_ws2812():
    img = prog("ws2812")
    data = [0x00, 0xFF, 0x5A, 0xA5, 0x81, 0x7E]
    chips = []
    for ph in PHASES:
        c = boot(img, ph)
        simulate(c, 30)
        c.host_in[0] += data
        simulate(c, 63 * 8 * (len(data) + 1))
        chips.append(c)
    rows = check_deadlines(img, chips, "6 bytes streamed")
    assert all(r[2] and r[3] == 0 and r[4] == 0 for r in rows)


def manchester_rx_inputs(sender):
    return lambda t, ch: (sender.level(t) << 4 | 8, 0)


def test_deadline_manchester():
    for bit_tx, bit_rx in ((50, 50), (22, 18)):
        img = prog("manchester", BIT=bit_tx)
        img_rx = prog("manchester", BIT=bit_rx)
        payload = [0x55, 0x55, 0xD5, 0x12, 0xA5, 0xFF, 0x00]
        chips = []
        for ph in PHASES:
            # TX on E0 and RX on E1 in one image; RX constants for bit_rx
            img2 = img if bit_tx == bit_rx else img_rx
            c = boot(img2, ph)
            simulate(c, 30)
            s = ManchesterSender(bit_rx, payload, t0=c.cycle + 30 + ph * 0.25)
            c.host_in[0] += [0x55, 0xD5, 0x3C]
            simulate(c, int(s.end) + 6 * bit_rx, manchester_rx_inputs(s))
            assert c.host_bytes(1) == payload[3:], c.host_bytes(1)
            chips.append(c)
        rows = check_deadlines(img2, chips, "BIT %d" % bit_rx, None if bit_tx == bit_rx else (1,))
        rx_rows = [r for r in rows if r[0]["engine"] == 1]
        assert all(r[2] and r[3] == 0 and r[4] == 0 for r in rx_rows)
        if bit_tx == bit_rx:
            assert all(r[2] and r[3] == 0 and r[4] == 0 for r in rows)
    # TX at its limit BIT 22
    img = prog("manchester", BIT=22)
    chips = []
    for ph in PHASES:
        c = boot(img, ph)
        simulate(c, 30)
        c.host_in[0] += [0x55, 0xD5, 0x3C, 0xA5]
        simulate(c, 22 * 8 * 6)
        chips.append(c)
    rows = check_deadlines(img, chips, "TX BIT 22", (0,))
    tx = [r for r in rows if r[0]["engine"] == 0]
    assert all(r[2] and r[3] == 0 and r[4] == 0 for r in tx)


# ---------------------------------------------------------------- 2. auto-transfer at wend, apull + apush + follow
STREAM = """
.engine 0
.pad uio2, p2
.init
        li    R0, #11 | (8 << 8)        ; p0 = SCK uio3, p1 = CS uio0
        wcfg  BIND01, R0
        li    R0, #9 << 8               ; p3 = MOSI uio1
        wcfg  BIND23, R0
        li    R0, #0x667                ; 8 bits, MSB first, input p3, apush, apull
        wcfg  SHCFG, R0
.entry go
.code
go:     @hrx  : pull S
        @fall p1 : p2 <- f
        loop { @rise p0 : shin }
        @htx  : push S
        @never : nop
"""


def test_autotransfer_apull_apush_follow():
    img = assemble_text(STREAM)
    host_tx = [0xA5, 0x3C, 0x81, 0x7E, 0x55, 0xAA, 0x0F, 0xF0]
    mosi = [0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88]
    worst = []
    for ph in PHASES:
        for T in (10, 16):
            c = boot(img, ph, fifo_log=True)
            c.host_in[0] += host_tx
            ctrl = SpiController(T, 0, mosi, t0=c.cycle + 60)
            simulate(c, int(ctrl.cs_hi) + 30, uio_spi(ctrl))
            miso = changes(c, lambda uo, out, oe: (out >> 2) & (oe >> 2) & 1)
            got_miso = ctrl.sample(miso)
            got_host = c.host_bytes(0)
            assert got_miso == host_tx, (ph, T, got_miso)
            assert got_host == mosi, (ph, T, got_host)
            lp = img.label("go") + 2
            fires = [f[0] for f in c.eng[0].fires if f[1] == lp]
            arms_out = [a for a in c.eng[0].arms if a[1] != lp and fires[0] < a[0] < fires[-1]]
            assert not arms_out, "loop left inside the stream"
            log = c.fifo_log
            wends = fires[7::8]
            for k, t in enumerate(wends[:-1]):
                assert (t, 0, "push") in log and (t, 0, "pop") in log, (ph, T, t)
                new_msb = (host_tx[k + 1] >> 7) & 1
                old_tap = (mosi[k] >> 7) & 1 if False else None
                pads = [tc for tc, v in miso if tc > t]
                if pads and level_at(miso, t, 0) != new_msb:
                    worst.append(pads[0] - t)
    ok = bool(worst) and max(worst) == 3 and min(worst) == 3     # pin state t + 2, pad t + 3 (D081)
    record("auto-transfer at wend (apush+apull, follow)", ok,
           "push+pull in the wend clock t for all bytes; loop never left; follow pad at edge t + %s" %
           sorted(set(worst)))
    assert ok


def test_autotransfer_blocked():
    img = assemble_text(STREAM)
    # apull blocked: host supplies 2 bytes for a 4-byte stream -> the loop ends at the 2nd wend, no transfer there
    c = boot(img, 0, fifo_log=True)
    c.host_in[0] += [0xA5, 0x3C]
    ctrl = SpiController(10, 0, [1, 2, 3, 4], t0=c.cycle + 60)
    simulate(c, int(ctrl.cs_hi) + 30, uio_spi(ctrl))
    lp = img.label("go") + 2
    fires = [f[0] for f in c.eng[0].fires if f[1] == lp]
    pushes = [x for x in c.fifo_log if x[2] == "push"]
    ok1 = len(fires) == 16 and len(pushes) == 2 and c.host_bytes(0) == [1, 2]
    # apush blocked: TX FIFO full (host not reading) -> loop ends when the 5th word cannot be pushed
    c = boot(img, 0, fifo_log=True)
    c.tx_block[0] = True
    c.host_in[0] += list(range(10, 20))
    ctrl = SpiController(10, 0, list(range(8)), t0=c.cycle + 60)
    simulate(c, int(ctrl.cs_hi) + 30, uio_spi(ctrl))
    fires = [f[0] for f in c.eng[0].fires if f[1] == lp]
    pulls = [x for x in c.fifo_log if x[2] == "pop"]
    ok2 = len(fires) == 40 and len(pulls) == 1 + 4
    record("auto-transfer blocked (apull empty / apush full)", ok1 and ok2,
           "apull: loop ended after %d shifts, %d pushes; apush: loop ended after %d shifts" %
           (16, len(pushes), len(fires)))
    assert ok1 and ok2


# ---------------------------------------------------------------- 3. watcher during ld / st
WATCH = """
.engine 0
.init
        li    R0, #4                    ; p0 = ui_in[4]
        wcfg  BIND01, R0
        li    R0, #0x0A04 | (MODE << 12) ; CC0: rise p0, abort or call
        wcfg  CC0, R0
        li    R0, #h
        wcfg  WV0, R0
        li    R0, #buf
        li    R1, #0x1234
        li    R2, #0
        watch #1
.entry go
.code
go:     OP
        @never : nop
h:      HANDLER
buf:    .word 0, 0x5678
"""


def watch_prog(op, mode, handler="@never : nop"):
    return assemble_text(WATCH.replace("OP", op).replace("HANDLER", handler).replace("MODE", str(mode)))


def run_watch(img, ph, fire_at):
    c = boot(img, ph)
    def inputs(t, ch):
        return (1 << 4 if fire_at is not None and t > fire_at - 2 else 0) | 8, 0
    simulate(c, 80, inputs)
    return c


def test_watcher_cancels_store():
    img = watch_prog("st R1, [R0]", 1)
    buf = img.label("buf")
    rows = []
    for ph in PHASES:
        c = run_watch(img, ph, None)
        f = [x[0] for x in c.eng[0].fires if x[3] == "st"][0]
        # completion clock = grant clock: find it by the write
        c2 = boot(img, ph)
        g = None
        for _ in range(80):
            c2.step(8, 0)
            if c2.mem[buf] == 0x1234:
                g = c2.cycle - 1
                break
        for k in range(0, g - f + 3):
            cw = run_watch(img, ph, f + k)
            written = cw.mem[buf] == 0x1234
            fired_at = [x for x in cw.eng[0].fires if x[3] == "st"]
            rows.append((ph, k, g - f, written, written == (f + k > g)))
    ok = all(r[4] for r in rows)
    cases = ["ph%d:g=f+%d" % (ph, gf) for ph, gf in sorted({(r[0], r[2]) for r in rows})]
    record("watcher (abort) during st: written iff watcher after the grant clock", ok,
           "%d offsets f..g+2 in 4 slot phases (%s)" % (len(rows), ", ".join(cases)))
    assert ok


def test_watcher_cancels_load():
    img = watch_prog("ld R2, [R0+1]!", 1)
    buf = img.label("buf")
    rows = []
    for ph in PHASES:
        c2 = boot(img, ph)
        comp = None
        for _ in range(80):
            c2.step(8, 0)
            if c2.eng[0].R[2] == 0x5678 and comp is None:
                comp = c2.cycle - 1
        f = [x[0] for x in c2.eng[0].fires if x[3] == "ld"][0]
        for k in range(0, comp - f + 3):
            cw = run_watch(img, ph, f + k)
            done = cw.eng[0].R[2] == 0x5678
            wb = cw.eng[0].R[0] == buf + 1
            rows.append((ph, k, comp - f, done, wb, done == wb == (f + k > comp)))
    ok = all(r[5] for r in rows)
    record("watcher (abort) during ld: no dest write, no write-back up to completion", ok,
           "%d offsets in 4 slot phases, completion f+%s" % (len(rows), sorted({r[2] for r in rows})))
    assert ok


def test_watcher_call_repeats_load():
    img = watch_prog("ld R2, [R0+1]!", 2, "ret")
    buf = img.label("buf")
    rows = []
    for ph in PHASES:
        c2 = boot(img, ph)
        comp = None
        for _ in range(80):
            c2.step(8, 0)
            if c2.eng[0].R[2] == 0x5678 and comp is None:
                comp = c2.cycle - 1
        f = [x[0] for x in c2.eng[0].fires if x[3] == "ld"][0]
        for k in range(0, comp - f + 1):
            cw = run_watch(img, ph, f + k)
            lds = [x for x in cw.eng[0].fires if x[3] == "ld"]
            e = cw.eng[0]
            rows.append((ph, k, len(lds), e.R[2] == 0x5678 and e.R[0] == buf + 1, e.L == img.label("go")))
    ok = all(r[3] and r[4] and (r[2] == 2 or r[1] == 0) for r in rows)
    record("watcher (call) during ld: ret repeats the access", ok,
           "ld fired %s times, R2 loaded, write-back once, L = ld address" % sorted({r[2] for r in rows}))
    assert ok


def test_st3_cs_rise_after_eighth_edge():
    img = prog("spi_tgt")
    tab = [0x00A5, 0x005A, 1, 2, 3, 4, 5, 6]
    lp = img.label("cmd")
    bad = []
    for ph in PHASES:
        # dry run: clock t of the 8th sampling fire of the command
        c, ctrls, got, _ = run_spi_tgt(img, 25, [2], 0, ph)
        t = [f[0] for f in c.eng[0].fires if f[1] == lp][7]
        for k in range(1, 7):
            c = boot(img, ph)
            x1 = SpiController(25, 0, [2], t0=c.cycle + 40)
            x1.cs_hi = t + k - 1.5          # engine sees CS high in clock t + k
            x1.lead = [a for a in x1.lead if a < x1.cs_hi]
            x1.trail = [a for a in x1.trail if a < x1.cs_hi]
            x2 = SpiController(25, 0, [1, 0, 0], t0=x1.cs_hi + 30)
            simulate(c, int(x2.cs_hi) + 40, uio_spi(Seq([x1, x2])))
            miso = changes(c, lambda uo, out, oe: (out >> 2) & (oe >> 2) & 1)
            g = x2.sample(miso)
            if g != [0, tab[1], tab[2]] or c.eng[0].ovr:
                bad.append((ph, k, g))
    ok = not bad
    record("ST3: CS rises in each of the 6 clocks after the 8th edge", ok,
           "next transfer correct in %d/24 cases %s" % (24 - len(bad), bad[:2]))
    assert ok


# ---------------------------------------------------------------- 4. pad-referred numbers
def spi_tgt_margins(T, phases=(0.0, 0.25, 0.5, 0.75)):
    img = prog("spi_tgt")
    setup_first, setup_all, hold = 1e9, 1e9, 1e9
    for ph in PHASES:
        for sub in phases:
            c, ctrls, got, miso = run_spi_tgt(img, T, [0, 3], 2, ph, phase=sub, table=[0xAA] * 8)
            for x, g in zip(ctrls, got):
                assert g == [0, 0xAA, 0xAA], (T, ph, sub, g)
                for i, ts in enumerate(x.samp[8:], 8):
                    prev = [tc for tc, v in miso if tc <= ts]
                    nxt = [tc for tc, v in miso if tc > ts]
                    m = ts - prev[-1]
                    setup_all = min(setup_all, m)
                    if i == 8:
                        setup_first = min(setup_first, m)
                    if nxt and i < len(x.samp) - 1:
                        hold = min(hold, nxt[0] - ts)
    return setup_first * 20, setup_all * 20, hold * 20


@pytest.mark.parametrize("T,claim", [(11.25, 25), (12.5, 50), (25, 300)])
def test_spi_tgt_setup(T, claim):
    first, allm, hold = spi_tgt_margins(T)
    ok = first >= claim - 1e-6 and hold >= 60
    record("spi_tgt MISO setup T=%g clk (claim >= %d ns)" % (T, claim), ok,
           "first response bit %.1f ns, any bit %.1f ns, hold %.1f ns (16 phase pairs)" % (first, allm, hold))
    assert ok


class OnlineSpiTarget:
    # mode 0 target answering the chip's spi_ctrl: MISO changes `delay` clk after each SCK falling edge / CS fall
    def __init__(self, data, delay):
        self.bits = [(b >> (7 - i)) & 1 for b in data for i in range(8)]
        self.delay = delay
        self.ch = []
        self.k = 0
        self.seen = 0

    def update(self, chip):
        tr = chip.pad_trace
        while self.seen < len(tr):
            t, uo, out, oe = tr[self.seen]
            prev = tr[self.seen - 1] if self.seen else (0, 0, 0, 0)
            self.seen += 1
            sck, psck = (out >> 3) & 1, (prev[2] >> 3) & 1
            cs, pcs = (out >> 0) & 1, (prev[2] >> 0) & 1
            if self.k < len(self.bits) and ((pcs and not cs) or (psck and not sck and not cs)):
                self.ch.append((t + self.delay, self.bits[self.k]))
                self.k += 1

    def level(self, t):
        return level_at(self.ch, t, 0)


def run_spi_ctrl(img, data_in, data_out, delay_ns, ph):
    c = boot(img, ph)
    tgt = OnlineSpiTarget(data_in, (delay_ns + 10) / 20.0)
    c.host_in[0] += [len(data_out)] + data_out
    def inputs(t, ch):
        tgt.update(ch)
        return 8, tgt.level(t) << 2
    simulate(c, 400 + 120 * len(data_out), inputs)
    got = c.host_bytes(0)
    sck = changes(c, lambda uo, out, oe: (out >> 3) & 1)
    mosi = changes(c, lambda uo, out, oe: (out >> 1) & 1)
    return c, got, sck, mosi


def test_spi_ctrl_answer_window():
    img = prog("spi_ctrl")          # BIT 8, SMP 6: window (SMP - 3) clk - 10 ns = 50 ns (D081)
    data_in = [0xA5, 0x5A, 0xFF, 0x00, 0x81]
    data_out = [0x3C, 0xC3, 0x01, 0x80, 0x55]
    res = {}
    for d in (0, 35, 49, 51, 90):
        oks = []
        for ph in PHASES:
            c, got, sck, mosi = run_spi_ctrl(img, data_in, data_out, d, ph)
            oks.append(got == data_in)
        res[d] = all(oks)
    ok = res[0] and res[35] and res[49] and not res[51] and not res[90]
    record("spi_ctrl answer window SMP 6 (claim 50 ns)", ok,
           "target delay 0/35/49 ns correct=%s, 51/90 ns correct=%s (4 slot phases)" %
           ([res[0], res[35], res[49]], [res[51], res[90]]))
    assert ok


TIMER = """
.engine 0
.pad uo0, p0
.init
        li    R0, #@C@
        mov   C, R0
        li    R0, #@P@
        mov   P, R0
        li    R0, #@F@
        wcfg  TCFG, R0
        li    R1, #@V@
.entry go
.code
go:     tset  R1
        @tock : p0 <- 1
        @tick : p0 <- 0
        tset  R1
        @tick : tset R1                 ; start in the tick clock
        @tick : nop
        tsync
per:    @tick : p0 <- t
        jmp   per
"""


def test_timer_event_times():
    V, C, P, FR = 40, 10, 20, 85
    img = assemble_text(TIMER.replace("@C@", str(C)).replace("@P@", str(P)).replace("@F@", str(FR))
                        .replace("@V@", str(V)))
    ok_all, meas = True, []
    for ph in PHASES:
        c = boot(img, ph)
        simulate(c, 300 + 260 * (P + 2))
        fl = c.eng[0].fires
        tset0 = [f[0] for f in fl if f[3] == "tset"][0]
        tock = [f[0] for f in fl if f[3] == "pin" and f[0] > tset0][0]
        tick = [f[0] for f in fl if f[3] == "pin" and f[0] > tock][0]
        ok1 = tock == tset0 + V - C + 1 and tick == tset0 + V + 1
        ts = [f[0] for f in fl if f[3] == "tset"]
        k2 = ts[2]                                   # tset fired by a tick
        nxt = [f[0] for f in fl if f[0] > k2][0]     # @tick : nop
        ok2 = nxt == k2 + V + 1
        per = [f[0] for f in fl if f[1] == img.label("per")]
        span = per[256] - per[0]
        ok3 = span == 256 * (P + 1) + FR
        meas.append((tock - tset0, tick - tset0, nxt - k2, span))
        ok_all &= ok1 and ok2 and ok3 and c.eng[0].late == 0
    record("timer: tset V at k -> tock k+V-C+1, tick k+V+1; start in tick clock; frac", ok_all,
           "tock +%d (exp %d), tick +%d (exp %d), restart in tick clock +%d (exp %d), 256 periods %d clk "
           "(exp %d)" % (meas[0][0], V - C + 1, meas[0][1], V + 1, meas[0][2], V + 1, meas[0][3],
                         256 * (P + 1) + FR))
    assert ok_all


def uart_drift(baud, dbits, parity, rates=(-0.02, 0.0, 0.02), nchar=24):
    nom = 50e6 / baud
    img = prog("uart_rx", BAUD=baud, DBITS=dbits, PARITY=parity)
    stop_pc = img.label("stop")
    worst = 0.0
    data = [(k * 0x5B + 0x21) & ((1 << dbits) - 1) for k in range(nchar)]
    for r in rates:
        bit = nom / (1 + r)
        for ph in PHASES:
            c = boot(img, ph)
            s = UartSender(bit, data, dbits=dbits, parity=parity, t0=c.cycle + 20 + ph * 0.3)
            simulate(c, int(s.end) + 3 * int(nom), lambda t, ch: (s.level(t) << 7 | 8, 0))
            hb = c.host_out[1]
            ev = [v for _, v in hb if v & 0x100]
            assert not ev, (baud, r, ph, ev)
            got = [v for _, v in hb if not v & 0x100]
            if dbits > 8:
                got = [got[k] | (got[k + 1] & 1) << 8 for k in range(0, len(got), 2)]
            assert got == data, (baud, r, ph)
            stops = [f[0] for f in c.eng[1].fires if f[1] == stop_pc]
            for st, fire in zip(s.starts, stops):
                centre = st + (1 + dbits + (1 if parity else 0) + 0.5) * bit
                worst = max(worst, abs((fire - 1) - centre))
    return worst, nom / 2 - 2


@pytest.mark.parametrize("baud,dbits,parity,claim", [(1000000, 8, 0, 10.3), (1000000, 9, 1, 12.3),
                                                     (3000000, 8, 0, 4.55), (2100000, 9, 1, 7.39)])
def test_uart_rx_drift(baud, dbits, parity, claim):
    worst, limit = uart_drift(baud, dbits, parity)
    ok = worst <= limit
    record("UART RX drift %d baud %d%s1, senders -2/0/+2 %%" % (baud, dbits, "E" if parity else "N"), ok,
           "%.2f clk (claim %.1f, limit %.2f)" % (worst, claim, limit))
    assert ok


# ---------------------------------------------------------------- also early: stale flags (finding 14, ST4 + ST5)
STALE_TAB = [0xA5, 0x5A, 1, 2, 3, 4, 5, 6]


def stale_case(ph, cs_high, k, setup):
    img = prog("spi_tgt")
    c = boot(img, ph)
    a = SpiController(25, 0, [1, 0, 0], t0=c.cycle + 40)
    r = a.cs_hi
    b = SpiController(25, 0, [2, 0, 0], t0=r + cs_high, cs_setup=setup)
    nz = (r + k, r + k + 2)                      # SCK pulse and MOSI high while CS is high

    def inputs(t, ch):
        miso = (ch.uio_out >> 2) & (ch.uio_oe >> 2) & 1
        if t < r:
            return 8, a.sck(t) << 3 | a.cs(t) | a.mosi(t) << 1 | miso << 2
        if t < b.cs_lo:
            return 8, int(nz[0] <= t < nz[1]) << 3 | 1 | 1 << 1 | miso << 2
        return 8, b.sck(t) << 3 | b.cs(t) | b.mosi(t) << 1 | miso << 2
    simulate(c, int(b.cs_hi) + 40, inputs)
    miso = changes(c, lambda uo, o, oe: (o >> 2) & (oe >> 2) & 1)
    ok = (a.sample(miso) == [0] + STALE_TAB[1:3] and b.sample(miso) == [0] + STALE_TAB[2:4] and
          c.eng[0].ovr == 0)
    return ok, b.first - r


@pytest.mark.slow
def test_stale_flags_st4_st5_combined():
    # D055, D100: first sampling edge >= 32 clk after the CS rise and >= 7 clk after CS falls (11 if CS was high < 25)
    rows = []
    for ph in PHASES:
        for hi in range(5, 40, 2):
            setup = max(32 - hi, 11 if hi < 25 else 7)        # D100
            for k in list(range(0, hi - 2, 3)) + [1000]:
                rows.append((hi, ph, k) + stale_case(ph, hi, k, setup))
    bad = [r for r in rows if not r[3]]
    ok = not bad
    record("stale flags: SCK/MOSI with CS high in the idle path, then CS low (ST4+ST5, D055)", ok,
           "%d cases (CS high 5..39 clk, first edge >= 32 after the rise, >= 7 / 11 after the fall, pulse anywhere), "
           "4 slot phases, ghost bits %d %s" % (len(rows), len(bad), bad[:3]))
    assert ok


# ---------------------------------------------------------------- also early: STOP / RUN in every memory FSM state (D046)
MEMFSM = """
.engine 0
.init
        li    R0, #buf
        li    R1, #0
        li    S, #8
        li    R2, #0
.entry go
.code
go:     ld    R2, [R0+1]!
        add   R1, R2
        st    R1, [R0+S]!
        sub   R0, S
        cmp   R0, #buf + 12
        jnz   go
        @never : nop
buf:    .word 0, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41
        .space 24
"""


def memfsm_state(c):
    e = c.eng[0]
    return (e.R[:], e.S, list(c.mem[:200]), e.trap, e.ovr)


@pytest.mark.slow
def test_stop_run_every_memory_state():
    img = assemble_text(MEMFSM)
    ref = boot(img, 0)
    simulate(ref, 600)
    want = memfsm_state(ref)
    want_seq = [(f[1], f[3]) for f in ref.eng[0].fires if f[0] >= 0 and f[3] not in ("li",)]
    span = ref.eng[0].fires[-1][0]
    bad, n = [], 0
    for ph in PHASES:
        for s in range(10, 70):
            for d in (1, 2, 3, 5):
                c = boot(img, ph)
                t0 = c.cycle
                for _ in range(s):
                    c.step(8, 0)
                c.pulse(0, "stop")
                for _ in range(d):
                    c.step(8, 0)
                c.pulse(0, "run")
                simulate(c, 600)
                n += 1
                seq = [(f[1], f[3]) for f in c.eng[0].fires if f[3] not in ("li",)]
                if memfsm_state(c) != want or seq != want_seq:
                    bad.append((ph, s, d))
    ok = not bad
    record("STOP/RUN at every clock of ld/st/fetch (D046): result and fire sequence unchanged", ok,
           "%d cases (stop at 60 clocks x pause 1/2/3/5 clk x 4 slot phases), mismatches %d %s" % (n, len(bad), bad[:4]))
    assert ok
