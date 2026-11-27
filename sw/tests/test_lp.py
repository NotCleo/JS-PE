# lp_tx.s and lp_rx.s against independent link pulse models (models.flp_positions, flp_word, PulseLine) and the
# host autonegotiation in host/eth.py; lp_tx -> lp_rx loop-back.
import os
import random
import sys
import pytest
from harness import prog, boot, simulate, ROOT, assemble
from models import trace_levels, pulses_of, flp_positions, flp_word, PulseLine

sys.path.insert(0, os.path.join(ROOT, "host"))
import eth                                                            # noqa: E402

TXP = lambda uo, out, oe: out & oe & 1                               # noqa: E731
LED = lambda uo, out, oe: out >> 3 & oe >> 3 & 1                     # noqa: E731
RX = dict(CLK_HZ=1280000, PRE=0, LOSS=65000)                         # TP 80, TH 120, TEND 262, TMIN 5120 clk
LRX = dict(CLK_HZ=1600000, PRE=0, LOSS=65000)                        # loop-back: TP 100, TH 150, TEND 328
LTX = dict(CLK_HZ=1600000, PW=3)                                     # period 256 x 100 = 25600 clk


def items(c, e):
    return [("ev", v & 63) if v & 0x100 else v for _, v in c.host_out[e]]


def tx_pulses(c):
    return pulses_of(trace_levels(c.pad_trace, TXP))


def bursts(ps, gap):
    out, cur = [], []
    for t, w in ps:
        if cur and t - cur[-1][0] > gap:
            out.append(cur)
            cur = []
        cur.append((t, w))
    return out + ([cur] if cur else [])


def txn_ok(c):
    return all(oe & 2 and not out & 2 for _, _, out, oe in c.pad_trace)


# ---------------- lp_tx
@pytest.mark.parametrize("tp,pw,nt,slot", [(200, 5, 40, 0), (120, 3, 36, 1), (400, 9, 64, 3)])
def test_tx_nlp(tp, pw, nt, slot):
    c = boot(prog("lp_tx", TP=tp, PW=pw, NTICK=nt), slot)
    simulate(c, 4 * tp * nt + 100)
    ps = tx_pulses(c)
    assert len(ps) == 4 and all(w == pw for _, w in ps)
    assert {b[0] - a[0] for a, b in zip(ps, ps[1:])} == {tp * nt}
    assert txn_ok(c) and c.eng[0].ovr == 0


@pytest.mark.parametrize("tp,pw,nt,slot", [(200, 5, 40, 0), (100, 2, 36, 2)])
def test_tx_flp_words(tp, pw, nt, slot):
    words = [0x0061, 0x4061, 0xFFFF, 0x0000, 0x8001, 0x2AA5]
    c = boot(prog("lp_tx", TP=tp, PW=pw, NTICK=nt), slot)
    c.host_in[0] += eth.lp_cmd(eth.LP_FLP, words[0])
    per = tp * nt
    for w in words[1:]:
        simulate(c, per)
        c.host_in[0] += eth.lp_cmd(eth.LP_FLP, w)
    simulate(c, 2 * per)
    bs = bursts(tx_pulses(c), 3 * tp)
    assert all(wd == pw for b in bs for _, wd in b)
    got = [flp_word([t for t, _ in b], tp) for b in bs]
    # each command takes effect at the next period start: every word appears, in order, each burst exact
    assert [g for k, g in enumerate(got) if k == 0 or g != got[k - 1]] == words
    for b in bs:
        assert [t - b[0][0] for t, _ in b] == [p * tp for p in flp_positions(flp_word([t for t, _ in b], tp))]
    assert {b[0] - a[0] for a, b in zip([x[0] for x in bs], [x[0] for x in bs[1:]])} == {per}
    assert txn_ok(c) and c.eng[0].ovr == 0


def test_tx_modes_switch():
    tp, nt = 150, 40
    per = tp * nt
    c = boot(prog("lp_tx", TP=tp, PW=4, NTICK=nt), 1)
    seq = [None, eth.lp_cmd(eth.LP_FLP, 0x0061), eth.lp_cmd(eth.LP_OFF), eth.lp_cmd(eth.LP_NLP),
           eth.lp_cmd(eth.LP_FLP, 0x4061), [0x07]]
    for s in seq:
        if s:
            c.host_in[0] += s
        simulate(c, per)
    simulate(c, per)
    bs = bursts(tx_pulses(c), 3 * tp)
    kinds = [("nlp" if len(b) == 1 else flp_word([t for t, _ in b], tp)) for b in bs]
    # NLP until the FLP command, silence, NLP, FLP with ACK, then silence for the unknown command
    assert kinds[0] == "nlp" and 0x0061 in kinds and 0x4061 in kinds
    i, j = kinds.index(0x0061), kinds.index(0x4061)
    assert set(kinds[:i]) == {"nlp"} and "nlp" in kinds[i:j] and kinds[-1] == 0x4061
    starts = [b[0][0] for b in bs]
    assert any(b - a >= 2 * per for a, b in zip(starts, starts[1:]))       # the silent period
    assert c.cycle - starts[-1] > per + tp                                    # silence after the unknown command


def test_tx_real_rate_burst():
    # as written: TP 3125 (62.5 us), PW 5 (100 ns); one FLP burst
    c = boot(prog("lp_tx"), 2)
    c.host_in[0] += eth.lp_cmd(eth.LP_FLP, 0x05E1)
    simulate(c, 34 * 3125)
    ps = tx_pulses(c)
    assert all(w == 5 for _, w in ps)
    assert [t - ps[0][0] for t, _ in ps] == [p * 3125 for p in flp_positions(0x05E1)]


# ---------------- lp_rx with a model pulse source
def rx_run(starts, n, slot=0, width=4, img=None, **d):
    c = boot(img or prog("lp_rx", ENG=0, **dict(RX, **d)), slot)
    t0 = c.cycle + 20
    line = PulseLine([t0 + s for s in starts], width)
    simulate(c, n, lambda t, ch: (8, line.level(t) << 2))
    return c, t0


def flp_starts(word, t0, tp, jit=0.0, r=None):
    return [t0 + p * tp + (r.uniform(-jit, jit) if r else 0) for p in flp_positions(word)]


@pytest.mark.parametrize("slot", [0, 3])
def test_rx_nlp_link_up_down(slot):
    per = 20480
    r = random.Random(slot)
    starts = [0.0]
    for _ in range(4):
        starts.append(starts[-1] + r.uniform(0.5, 1.5) * per)          # 16 +- 8 ms (scaled)
    c, t0 = rx_run(starts, int(starts[-1]) + 66000 + 500, slot)
    ev = eth.rx_parse(items(c, 0))
    assert ev == [("up",), ("down",)]
    led = trace_levels(c.pad_trace, LED)
    assert led[0][1] == 1 and abs(led[0][0] - (t0 + starts[2])) < 400       # up at the group end of the 3rd NLP
    assert led[1][1] == 0 and abs(led[1][0] - (t0 + starts[-1] + 65001)) < 50
    assert c.eng[0].ovr == 0


def test_rx_early_nlp_restarts_count():
    per = 20480
    starts = [0, per, per + 2000, 2 * per + 2000, 3 * per + 2000, 4 * per + 2000]   # 3rd too early (< TMIN 5120)
    c, t0 = rx_run(starts, starts[-1] + 1500)
    assert eth.rx_parse(items(c, 0)) == [("up",)]
    led = trace_levels(c.pad_trace, LED)
    assert len(led) == 1 and led[0][0] > t0 + starts[4]                       # up only at the 5th pulse


@pytest.mark.parametrize("slot,jit", [(0, 0.0), (1, 9.0), (2, 9.0)])
def test_rx_flp_decode(slot, jit):
    # +-9 clk of 80 = +-7 us of 62.5 us (802.3 interval tolerance); bursts 16 ms apart (scaled)
    r = random.Random(7 + slot)
    words = [0x0061, 0x4061, 0xFFFF, 0x0000, 0x8001, 0x5AA5, r.getrandbits(16)]
    starts = []
    for k, w in enumerate(words):
        starts += flp_starts(w, k * 20480, 80, jit, r)
    c, _ = rx_run(starts, len(words) * 20480 + 500, slot)
    assert eth.rx_parse(items(c, 0)) == [("flp", w) for w in words]
    assert trace_levels(c.pad_trace, LED) == [] and c.eng[0].ovr == 0


def test_rx_bad_groups():
    tp, per = 80, 20480
    full = flp_starts(0x0061, 0, tp)
    groups = [full[:-1],                                    # last clock pulse missing: ends after a data... bit count
              full[:15],                                    # truncated
              [0, 80],                                      # two pulses
              [0],                                          # one pulse = NLP (no event)
              [t for t in full if t != 4 * tp],             # a clock missing: 4 TP gap > TEND, two bad groups
              full + [34 * tp]]                             # an extra clock pulse: 17 bits
    starts = []
    for k, g in enumerate(groups):
        starts += [k * per + t for t in g]
    c, _ = rx_run(starts, len(groups) * per + 500)
    ev = eth.rx_parse(items(c, 0))
    assert ev == [("bad",)] * 6, ev


def test_rx_burst_ending_in_data_is_bad():
    # 0x8000: D15 = 1 -> data pulse at position 31; drop the final clock pulse at 32 -> ends inside a data pulse
    tp = 80
    s = [t for t in flp_starts(0x8000, 0, tp)][:-1]
    c, _ = rx_run(s, 3200)
    assert eth.rx_parse(items(c, 0)) == [("bad",)]


def test_rx_real_rate_with_ringing():
    # as written (50 MHz, PRE 2: 5.12 us steps): one FLP burst, every pulse followed by a ringing rise 1.2 us later
    tp = 3125
    s = flp_starts(0x4061, 0, tp)
    s = sorted(s + [t + 60 for t in s])
    c, _ = rx_run(s, 33 * tp + 12000, img=prog("lp_rx", ENG=0), width=5)
    assert eth.rx_parse(items(c, 0)) == [("flp", 0x4061)]


def test_rx_ringing_breaks_without_holdoff_at_pre0():
    # negative: at PRE 0 the hold-off is 1 clk, so a ringing rise 30 clk after a clock pulse is taken as a data
    # pulse: the word is corrupted (0x0061 -> 0x0065)
    s = flp_starts(0x0061, 0, 80)
    s = sorted(s + [s[3] + 30])
    c, _ = rx_run(s, 4000)
    assert eth.rx_parse(items(c, 0)) == [("flp", 0x0065)]


# ---------------- loop-back lp_tx (E0, TXP uio[0]) -> lp_rx (E1, RXD uio[2])
def loop_img(**d):
    files = [os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "docs",
                          "spec", "programs", n) for n in ("lp_tx.s", "lp_rx.s")]
    defs = {"lp_tx.s:%s" % k: str(v) for k, v in LTX.items()}
    defs.update({"lp_rx.s:%s" % k: str(v) for k, v in LRX.items()})
    defs.update({k: str(v) for k, v in d.items()})
    return assemble(files, defs)


def loop_inputs(t, c):
    return 8, (c.uio_out & c.uio_oe & 1) << 2


@pytest.mark.parametrize("slot", [0, 2])
def test_loopback_autoneg(slot):
    # both ends are Cleo: our TX advertises, our RX hears our own words (a mirror partner); the host arbitration
    # reaches complete acknowledge on its own bursts and resolves 10FD, then lp_tx falls back to NLP -> link up
    c = boot(loop_img(), slot)
    an = eth.Autoneg()
    c.host_in[0] += an.start()
    k = 0
    for _ in range(16):
        simulate(c, 25600, loop_inputs)
        ev = eth.rx_parse(items(c, 1))
        for e in ev[k:]:
            c.host_in[0] += an.item(e)
        k = len(ev)
        if an.state == "done" and ("up",) in ev:
            break
    assert an.result == "10FD"
    ev = eth.rx_parse(items(c, 1))
    flps = [e[1] for e in ev if e[0] == "flp"]
    assert flps[:3] == [0x0061] * 3 and 0x4061 in flps and ev[-1] == ("up",)
    assert c.eng[0].ovr == 0 and c.eng[1].ovr == 0


# ---------------- scenarios for T5 / T6 (test_bounds, test_sweep)
def tx_scenario(img, ph, pp, full=False):
    # TP 100, PW 3, NTICK 36: NLP, FLP, a split command, silence, FLP
    c = boot(img, ph)
    simulate(c, 3600 + pp)
    for cmd in ([1, 0x61, 0x40], [0x02], [1, 0xFF, 0xFF], [0x00]) if full else ([1, 0x61, 0x40],):
        c.host_in[0] += cmd
        simulate(c, 3600)
    return c


def rx_scenario(img, ph, pp, full=False):
    # RX dict constants: NLPs to link up, FLP bursts, a bad group, ringing, then silence to link loss when full
    r = random.Random(ph * 4 + pp)
    starts = [0, 20480, 41000, 61500]
    for k, w in enumerate((0x0061, r.getrandbits(16))):
        starts += flp_starts(w, 82000 + k * 20480, 80, 9.0, r)
    starts += [123000, 123080, 123081, 143500]
    c, _ = rx_run(starts, (210000 if full else 145000) + pp, img=img, slot=ph)
    return c
