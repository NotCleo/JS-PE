# PIO host link (docs/spec/pio_link_design.md): host/jspe.py's PIO programs, DMA ring and Link run in CPython on a
# PIO model (sw/pio_emu.py) clocked with the ISS chip at sys = 2 x clk (96 / 48 MHz), LINK B = 4. Emulated only.
import io
import os
import pytest
from harness import PROG, assemble, assemble_text
from jspe_iss import Chip
import fake_rp2
from pio_emu import SM

ESC = 0x1B
CLK = 48_000_000
LOOP = {"CLK_HZ": "48000000", "BAUD": "1000000"}

TX_TEXT = """
    pull block
    out x, 1
    jmp x-- send
    wait 0 pin 0
send:
    set pins, 0 [6]
    set y, 7
bit:
    out pins, 1
    jmp y-- bit [6]
    set pins, 1 [4]
"""
RX_TEXT = """
start:
    wait 0 pin 0
    set x, 7 [10]
bit:
    in pins, 1
    jmp x-- bit [6]
    jmp pin ok
    wait 1 pin 0
    jmp start
ok:
    push block
"""


def world(pad=0, ui=lambda c: 0x80, ring_bits=14, B=4, start=True):
    c = Chip(direct=False)
    w, jspe = fake_rp2.install(c, pad=pad, ui=ui)
    w.run(200)
    k = jspe.Link(None, CLK, 16, pio=dict(sm=0, ring_bits=ring_bits))
    k.timeout_ms = 2                                     # simulated ms; a lost reply fails fast
    if start:
        k.sync()
        if B != 16:
            k.set_rate(B)
    return c, w, jspe, k


def load(k, tmp_path, img):
    p = os.path.join(str(tmp_path), "img.bin")
    with open(p, "wb") as f:
        f.write(img.bin_bytes())
    k.load(p)


def test_programs_match_pioasm():
    import adafruit_pioasm
    _, jspe = fake_rp2.install(Chip(direct=False))
    tx, rx = jspe.pio_progs()
    assert tx.words == list(adafruit_pioasm.assemble(TX_TEXT))
    assert rx.words == list(adafruit_pioasm.assemble(RX_TEXT))


def decode_wave(wave, cyc=8):
    out, t = [], 0
    while True:
        try:
            t = wave.index(0, t)
        except ValueError:
            return out
        if t + 10 * cyc > len(wave):
            return out
        out.append(sum(wave[t + cyc * (j + 1) + cyc // 2] << j for j in range(8)))
        assert wave[t + 9 * cyc + cyc // 2] == 1
        t += 9 * cyc + cyc // 2


@pytest.mark.parametrize("force", [0, 1])
def test_tx_waveform_8_cycles_per_bit(force):
    _, jspe = fake_rp2.install(Chip(direct=False))
    sm = SM(jspe.pio_progs()[0].words, out_base=20, set_base=20, in_base=38, levels={20: 1})
    for b in (0xA5, ESC):
        sm.put(b << 1 | force)
    wave = []
    for _ in range(200):
        sm.step(lambda g: 0)
        wave.append(sm.levels[20])
    t0 = wave.index(0) - 1
    for i, b in enumerate((0xA5, ESC)):
        gap = 80 + (1 - force)                  # stop bit 8 cycles forced, 9 when it waits for H_RDY
        bits = [0] + [b >> j & 1 for j in range(8)]
        for j, v in enumerate(bits):
            assert wave[t0 + i * gap + 8 * j + 1: t0 + i * gap + 8 * j + 9] == [v] * 8, (i, j)
        assert wave[t0 + i * gap + 73: t0 + (i + 1) * gap + 1] == [1] * (gap - 72)


def test_tx_waits_for_h_rdy_unless_forced():
    _, jspe = fake_rp2.install(Chip(direct=False))
    sm = SM(jspe.pio_progs()[0].words, out_base=20, set_base=20, in_base=38, levels={20: 1})
    sm.put(0x55 << 1)
    sm.put(0x33 << 1 | 1)
    sm.put(0x0F << 1)
    wave = []
    for t in range(800):
        sm.step(lambda g: 0 if 300 <= t < 302 else 1)    # H_RDY low for one moment only
        wave.append(sm.levels[20])
    assert wave[:300] == [1] * 300                        # data byte held while H_RDY is high
    assert decode_wave(wave) == [0x55, 0x33]              # then it, the forced byte; 0x0F waits again


def rx_run(levels, sync=2):
    _, jspe = fake_rp2.install(Chip(direct=False))
    sm = SM(jspe.pio_progs()[1].words, in_base=37, jmp_pin=37)
    seen = []
    for t in range(len(levels)):
        v = levels[t - sync] if t >= sync else 1
        sm.step(lambda g: v)
        while sm.rxf:
            seen.append(sm.rxf.popleft() >> 24)
    return seen


def frame(b, cyc=8, stop=1):
    return [0] * cyc + sum(([b >> j & 1] * cyc for j in range(8)), []) + [stop] * cyc


@pytest.mark.parametrize("lead", range(8))
def test_rx_decodes_at_every_phase_and_drops_breaks(lead):
    data = [0x00, 0xFF, ESC, 0xA5, 0x5A]
    wave = [1] * (5 + lead) + sum((frame(b) for b in data), [])
    wave += [0] * 160 + [1] * 20                         # break: dropped, receiver waits for idle
    wave += frame(0x3C, stop=0) + [1] * 20               # framing error: dropped
    wave += frame(0x81) + [1] * 30
    assert rx_run(wave) == data + [0x81]


def test_b4_sync_load_readback_and_gpio_base(tmp_path):
    c, w, jspe, k = world()
    assert fake_rp2.PIO.bases.get(0) == 16 and k.B == 4 and c.link.bit_time() == 4
    img = assemble([os.path.join(PROG, n + ".s") for n in ("uart_tx", "uart_rx")], LOOP)
    load(k, tmp_path, img)                               # every MEM chunk read back over the PIO link
    assert k.rdreg(0x01) == 2 and not k.notes


@pytest.mark.parametrize("pad", [0, 3])
def test_b4_h_rdy_back_pressure_loopback(tmp_path, pad):
    # uart_tx (E0) -> TXD looped to uart_rx (E1) at 1 Mbaud, 25x slower than the link: H_RDY must hold the host
    c, w, jspe, k = world(pad=pad, ui=lambda c: (c.uo & 1) << 7)
    load(k, tmp_path, assemble([os.path.join(PROG, n + ".s") for n in ("uart_tx", "uart_rx")], LOOP))
    held = []
    w.on_clk = lambda c: held.append(c.link.h_rdy) if len(held) < 1 or held[-1] != c.link.h_rdy else None
    data = bytes([ESC, 0x00, 0xFF] + [(37 * i) & 0xFF for i in range(40)] + [ESC, ESC])
    k.send(0, data)
    for _ in range(4000):
        k.poll()
        if len(k.rx.get(1, b"")) >= len(data):
            break
    assert bytes(k.rx.get(1, b"")) == data and not k.notes
    assert c.eng[0].ovr == 0 and c.eng[1].ovr == 0 and 1 in held


def test_b4_blocked_stream_noflow_command(tmp_path):
    # echo engine stopped: data held by H_RDY; op_noflow drops the held bytes and RUN gets through
    c, w, jspe, k = world()
    img = assemble_text("""
.engine 0
.entry l
.code
l:      @hrx  : pull R0
        @htx  : push R0
        jmp   l
""")
    load(k, tmp_path, img)
    k.op(0x20 | 1)
    data = bytes(range(0x40, 0x48))
    k.send(0, data)
    w.run(2000)
    assert (c.uo >> 5) & 1 == 1 and 3 <= len(c.rx[0].q) <= 4 and c.eng[0].ovr == 0
    n = len(c.rx[0].q)
    k.op_noflow(0x31)
    for _ in range(300):
        k.poll()
    assert bytes(k.rx.get(0, b"")) == data[:n] and c.eng[0].ovr == 0
    k.sync()


def test_break_then_sync():
    c, w, jspe, k = world()
    k.port.cmd(bytes([ESC, 0x72, 0x01]))                 # a command cut by the break
    k.port.brk()
    for _ in range(100):
        k.poll()
    assert k.notes == [0x8C]
    k.sync()
    assert k.rdreg(0x01) == 2


def test_ring_overrun_detected_then_resync():
    c, w, jspe, k = world(ring_bits=6)
    k.rd_len = 2 * 64
    k.op(0x71, 0, 0, 63)                                 # 130-byte reply into a 64-byte ring, nobody polling
    w.run(130 * 80 + 400)
    k.poll()
    assert 0x100 in k.notes and k.port.lost == 130 - 64
    k.notes = []
    k.sync()
    k.wrmem(0, list(range(8)))
    assert k.rdmem(0, 8) == tuple(range(8))


def test_pass_through_decoded_on_pc(tmp_path):
    # board: raw ring bytes to "USB CDC"; PC: jspe.Parser + host/monitor.py give what on-board decoding gives
    c, w, jspe, k = world(ui=lambda c: (c.uo & 1) << 7)
    load(k, tmp_path, assemble([os.path.join(PROG, n + ".s") for n in ("uart_tx", "uart_rx")], LOOP))
    data = bytes([(11 * i) & 0xFF for i in range(30)] + [ESC])
    k.send(0, data)
    cdc = io.BytesIO()
    k.forward(cdc, 1)
    raw = cdc.getvalue()
    assert ESC in raw
    pc = jspe.Parser()
    for i in range(0, len(raw), 7):                      # arbitrary USB packet split
        pc.feed(raw[i:i + 7])
    import monitor
    assert bytes(pc.rx[1]) == data and monitor.entries(pc, 1) == list(data)
