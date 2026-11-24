# MicroPython host side of the Cleo host link (HOSTIF.md): .bin loader with read-back, stream send / receive.
import struct
import time

ESC = 0x1B
ID = 0x4A10


class Link:
    # clk_hz: the clk the host drives; B: link bit time in clk (LINK register, 16 after reset)
    # uart_kw: arguments of machine.UART for the link, incl. flow=UART.CTS on H_RDY
    def __init__(self, uart_id, clk_hz, B=16, **uart_kw):
        from machine import UART
        self.clk_hz, self.B = clk_hz, B
        self.uid, self.kw = uart_id, uart_kw
        self.u = UART(uart_id, **uart_kw)
        self.buf = bytearray()
        self.cur = None
        self.rx = {}           # engine -> bytearray of stream data
        self.events = []       # (engine, code, index in that engine's stream: the event follows that many bytes)
        self.notes = []        # notice codes 0x80-0x8C
        self.reply = None
        self.rd_len = 0

    def op(self, code, *args):
        self.u.write(bytes((ESC, code) + args))

    def poll(self):
        n = self.u.any()
        if n:
            self.buf += self.u.read(n)
        b, k = self.buf, 0
        while k < len(b):
            x = b[k]
            if x != ESC:
                self.rx.setdefault(self.cur, bytearray()).append(x)
                k += 1
                continue
            if k + 1 >= len(b):
                break
            c = b[k + 1]
            n = {0xC1: self.rd_len, 0xC3: 2, 0xC4: 2}.get(c, 0)
            if k + 2 + n > len(b):
                break
            p = bytes(b[k + 2:k + 2 + n])
            k += 2 + n
            if c == ESC:
                self.rx.setdefault(self.cur, bytearray()).append(ESC)
            elif c <= 3:
                self.cur = c
            elif 0x40 <= c < 0x80:
                self.events.append((self.cur, c & 63, len(self.rx.get(self.cur, b""))))
            elif c in (0xC1, 0xC3, 0xC4):
                self.reply = (c, p)
            else:
                self.notes.append(c)
        self.buf = self.buf[k:]

    def op_noflow(self, code):
        # a command for a blocked stream: hardware CTS would hold it back too (HOSTIF 1)
        self.u.init(**dict(self.kw, flow=0))
        self.op(code)
        self.u.flush()
        self.u.init(**self.kw)

    def wait_reply(self, code, ms=200):
        t = time.ticks_ms()
        while self.reply is None:
            self.poll()
            if time.ticks_diff(time.ticks_ms(), t) > ms:
                raise OSError("no reply %02x" % code)
        c, p = self.reply
        self.reply = None
        if c != code:
            raise OSError("reply %02x, expected %02x" % (c, code))
        return p

    def sync(self):
        self.op(0x74)
        p = self.wait_reply(0xC4)
        if p[0] | p[1] << 8 != ID:
            raise OSError("bad ID")

    def rdreg(self, r):
        self.op(0x73, r)
        p = self.wait_reply(0xC3)
        return p[0] | p[1] << 8

    def wrreg(self, r, v):
        self.op(0x72, r, v & 0xFF, v >> 8)

    def rdmem(self, a, n):
        self.rd_len = 2 * n
        self.op(0x71, a & 0xFF, a >> 8, n - 1)
        return struct.unpack("<%dH" % n, self.wait_reply(0xC1))

    def wrmem(self, a, words):
        self.op(0x70, a & 0xFF, a >> 8, len(words) - 1)
        self.u.write(struct.pack("<%dH" % len(words), *words))

    def load(self, path):
        # HOSTIF 7: replay the records in file order; every MEM chunk is read back, in short RDMEMs while other
        # engines run so their streams never wait > 2000 clk behind a reply (HOSTIF 8, D088)
        d = open(path, "rb").read()
        if d[:5] != b"JSPE\x01":
            raise ValueError("not a version 1 .bin")
        running = any(self.rdreg(0x08 + e) >> 9 & 1 for e in range(4))
        chunk = (200 // self.B - 3) // 2 if running else 256
        p = 8
        for _ in range(struct.unpack_from("<H", d, 6)[0]):
            t = d[p]
            p += 1
            if t == 1:
                a, n = struct.unpack_from("<HH", d, p)
                ws = struct.unpack_from("<%dH" % n, d, p + 4)
                p += 4 + 2 * n
                for k in range(0, n, 256):
                    self.wrmem(a + k, ws[k:k + 256])
                for k in range(0, n, chunk):
                    ch = ws[k:k + chunk]
                    if self.rdmem(a + k, len(ch)) != tuple(ch):
                        raise OSError("load read-back")
            elif t == 2:
                r, v = struct.unpack_from("<BH", d, p)
                p += 3
                self.wrreg(r, v)
            elif t == 3:
                e, w = struct.unpack_from("<BH", d, p)
                p += 3
                self.op(0x60 | e, w & 0xFF, w >> 8)
            elif t == 4:
                self.op(0x30 | d[p])
                p += 1
            elif t == 5:
                self.op_noflow(0x20 | d[p])
                self.op(0x50 | d[p])
                p += 1
            elif t == 6:
                hz = struct.unpack_from("<I", d, p)[0]
                p += 4
                if hz != self.clk_hz:
                    raise ValueError("image assembled for clk %d Hz, host drives %d Hz" % (hz, self.clk_hz))
            else:
                raise ValueError("record type %d" % t)

    def send(self, e, data):
        self.op(e)
        self.op(e)             # SEL twice: H_RDY shows stream e before the first data byte
        self.u.write(bytes(data).replace(b"\x1b", b"\x1b\x1b"))

    def recv(self, e):
        self.poll()
        return bytes(self.rx.pop(e, b""))
