# SWO (PROTOCOL_MATRIX 1.6): ITM trace over NRZ (UART 8N1, LSB first) received by uart_rx.s unchanged; packets decoded
# by host/debug.py itm_decode against an independent packet encoder.
import os
import random
import sys
import pytest
from harness import prog, boot, simulate, ROOT
from models import UartSender

sys.path.insert(0, os.path.join(ROOT, "host"))
import debug                                                          # noqa: E402


def itm_stream(r, n):
    # packets and their bytes, written from the ITM packet format (sync, overflow, instrumentation, timestamps)
    pk, by = [("sync",)], [0, 0, 0, 0, 0, 0x80]
    for _ in range(n):
        k = r.randrange(5)
        if k < 3:
            size = r.choice((1, 2, 4))
            port, v = r.randrange(32), r.getrandbits(8 * size)
            src = "hw" if k == 2 else "sw"
            by += [port << 3 | (4 if src == "hw" else 0) | {1: 1, 2: 2, 4: 3}[size]] + [v >> (8 * i) & 255
                                                                                      for i in range(size)]
            pk.append((src, port, v))
        elif k == 3:
            v = r.randrange(1, 7)
            by.append(v << 4)
            pk.append(("ts", v))
        else:
            v = r.getrandbits(20)
            b = [0xC0]
            x = v
            while True:
                b.append((x & 0x7F) | (0x80 if x >> 7 else 0))
                x >>= 7
                if not x:
                    break
            by += b
            pk.append(("ts", v))
    by.append(0x70)
    pk.append(("overflow",))
    return pk, by


def swo_run(baud, rate, slot, seed, n=40):
    r = random.Random(seed)
    pk, by = itm_stream(r, n)
    img = prog("uart_rx", BAUD=baud)
    c = boot(img, slot)
    s = UartSender(50e6 / baud * (1 + rate), by, t0=c.cycle + 20 + slot * 0.3)
    simulate(c, int(s.end) + 200, lambda t, ch: (s.level(t) << 7 | 8, 0))
    return c, pk, by


@pytest.mark.parametrize("baud,rate,slot", [(1000000, 0.02, 0), (2000000, -0.02, 1), (3125000, 0.02, 2),
                                            (115200, -0.02, 3)])
def test_swo_nrz_itm(baud, rate, slot):
    c, pk, by = swo_run(baud, rate, slot, baud + slot, 40 if baud > 200000 else 10)
    got = [v for _, v in c.host_out[1]]
    assert got == by and debug.itm_decode(bytes(got)) == pk and c.eng[1].ovr == 0


def test_swo_nrz_too_fast_refused():
    # negative: 4.17 Mbaud (BIT 12) is above uart_rx's 3.3 Mbaud limit; the assembler's deadline check refuses it
    from jspe_asm import AsmError
    with pytest.raises(AsmError, match="deadline"):
        prog("uart_rx", BAUD=4166666)
