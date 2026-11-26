# MicroPython host side of the Cleo host link (HOSTIF.md): .bin loader with read-back, stream send / receive.
import struct
import time

ESC = 0x1B
ID = 0x4A10


class Parser:
    # chip -> host byte stream (HOSTIF 4-5): streams, events, notices, replies. MicroPython and CPython (pass-through).
    def __init__(self):
        self.buf = bytearray()
        self.cur = None
        self.rx = {}           # engine -> bytearray of stream data
        self.events = []       # (engine, code, index in that engine's stream: the event follows that many bytes)
        self.notes = []        # notice codes 0x80-0x8C
        self.reply = None
        self.rd_len = 0

    def feed(self, data):
        self.buf += data
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


class UartPort:
    # hardware UART, CTS on H_RDY (flow=UART.CTS in uart_kw)
    def __init__(self, uart_id, uart_kw):
        from machine import UART
        self.uid, self.kw = uart_id, uart_kw
        self.u = UART(uart_id, **uart_kw)

    def cmd(self, b):
        self.u.write(b)

    def data(self, b):
        self.u.write(bytes(b).replace(b"\x1b", b"\x1b\x1b"))

    def cmd_noflow(self, b):
        # a command for a blocked stream: hardware CTS would hold it back too (HOSTIF 1)
        self.u.init(**dict(self.kw, flow=0))
        self.u.write(b)
        self.u.flush()
        self.u.init(**self.kw)

    def txdone(self):
        return self.u.txdone()

    def read(self):
        n = self.u.any()
        return self.u.read(n) if n else b""

    def set_rate(self, clk_hz, B):
        self.kw = dict(self.kw, baudrate=clk_hz // B)
        self.u.init(**self.kw)


_progs = None


def pio_progs():
    # PIO link programs (docs/spec/pio_link_design.md), 8 PIO cycles per bit; built on first use (needs rp2)
    global _progs
    if _progs:
        return _progs
    import rp2

    # FIFO word = data << 1 | force; force skips the H_RDY wait (command bytes, HOSTIF 1)
    @rp2.asm_pio(set_init=rp2.PIO.OUT_HIGH, out_init=rp2.PIO.OUT_HIGH, out_shiftdir=rp2.PIO.SHIFT_RIGHT)
    def tx():
        pull()
        out(x, 1)
        jmp(x_dec, "send")
        wait(0, pin, 0)
        label("send")
        set(pins, 0)        [6]
        set(y, 7)
        label("bit")
        out(pins, 1)
        jmp(y_dec, "bit")   [6]
        set(pins, 1)        [4]

    # byte in ISR[31:24]; a low stop bit (framing error, break) drops the byte and waits for idle
    @rp2.asm_pio(in_shiftdir=rp2.PIO.SHIFT_RIGHT)
    def rx():
        label("start")
        wait(0, pin, 0)
        set(x, 7)           [10]
        label("bit")
        in_(pins, 1)
        jmp(x_dec, "bit")   [6]
        jmp(pin, "ok")
        wait(1, pin, 0)
        jmp("start")
        label("ok")
        push(block)

    _progs = (tx, rx)
    return _progs


class PioPort:
    # PIO UART on the link pins; chip -> host bytes go by DMA into a ring that Python only reads
    def __init__(self, clk_hz, B, tx=20, rx=37, rdy=38, sm=0, ring_bits=14):
        import rp2
        import machine
        import uctypes
        self.rp2, self.Pin = rp2, machine.Pin
        self.sys_hz = machine.freq()
        self.tx_pin, self.rx_pin, self.rdy_pin, self.smi = tx, rx, rdy, sm
        if max(tx, rx, rdy) > 31:
            rp2.PIO(sm // 4).gpio_base(self.Pin(16))      # RP2350B: bench check (design note)
        self.R = 1 << ring_bits
        self.mem = bytearray(2 * self.R)
        base = uctypes.addressof(self.mem)
        a = (base + self.R - 1) & ~(self.R - 1)            # write-ring needs size alignment
        self.ring = memoryview(self.mem)[a - base:a - base + self.R]
        self.N = 0x0FFFFFFF
        self.got = self.lost = 0
        self.set_rate(clk_hz, B)
        pio, k = sm // 4, (sm + 1) % 4
        self.dma = rp2.DMA()
        ctrl = self.dma.pack_ctrl(size=0, inc_read=False, inc_write=True, ring_size=ring_bits, ring_sel=True,
                                  treq_sel=8 * pio + 4 + k, chain_to=self.dma.channel)
        rxf = 0x50200000 + 0x100000 * pio + 0x20 + 4 * k + 3   # PIOx RXF[k], byte [31:24]
        self.dma.config(read=rxf, write=a, count=self.N, ctrl=ctrl, trigger=True)
        self.last = self.N

    def set_rate(self, clk_hz, B):
        self.f = 8 * clk_hz // B
        if self.sys_hz % self.f:
            raise ValueError("sys %d Hz is not a multiple of 8 x link baud" % self.sys_hz)
        self.byte_us = 81 * 1_000_000 // self.f + 1
        P = self.Pin
        self.rxsm = self.rp2.StateMachine(self.smi + 1, pio_progs()[1], freq=self.f, in_base=P(self.rx_pin),
                                          jmp_pin=P(self.rx_pin))
        self.rxsm.active(1)
        self._tx_init()

    def _tx_init(self):
        # (re)start the TX state machine: FIFO and OSR cleared, H_RX high
        P = self.Pin
        self.txsm = self.rp2.StateMachine(self.smi, pio_progs()[0], freq=self.f, out_base=P(self.tx_pin),
                                          set_base=P(self.tx_pin), in_base=P(self.rdy_pin))
        self.txsm.active(1)

    def cmd(self, b):
        from array import array
        self.txsm.put(array("H", [x << 1 | 1 for x in b]))

    def data(self, b):
        from array import array
        w = []
        for x in b:
            w += [ESC << 1 | 1, ESC << 1] if x == ESC else [x << 1]
        self.txsm.put(array("H", w))

    def cmd_noflow(self, b):
        # data still held for a blocked stream is dropped (TX restart) once the line is idle, then the command
        n = self.txsm.tx_fifo()
        while True:
            time.sleep_us(2 * self.byte_us)
            m = self.txsm.tx_fifo()
            if m == 0 or m == n:
                break
            n = m
        time.sleep_us(self.byte_us)
        self._tx_init()
        self.cmd(b)

    def txdone(self):
        # FIFO empty; the last byte may still be on the wire (≤ 1 byte time)
        return self.txsm.tx_fifo() == 0

    def brk(self, us=20):
        # H_RX low for >= 11 bit times (HOSTIF 9), after the TX FIFO has drained
        while self.txsm.tx_fifo():
            pass
        time.sleep_us(self.byte_us)
        self.txsm.exec("set(pins, 0)")
        time.sleep_us(us)
        self.txsm.exec("set(pins, 1)")

    def read(self):
        # new ring bytes; more than the ring since the last read = overrun: keep the newest R, count the rest lost
        c = self.dma.count & self.N
        n = (self.last - c) % self.N
        self.last = c
        w = (self.got + n) % self.R
        if n > self.R:
            self.lost += n - self.R
            n = self.R
        a = (w - n) % self.R
        self.got = w
        if a + n <= self.R:
            return bytes(self.ring[a:a + n])
        return bytes(self.ring[a:]) + bytes(self.ring[:a + n - self.R])


class Link(Parser):
    # notes gets 0x100 on a PIO ring overrun (chip -> host bytes lost; resync with SYNC)
    # clk_hz: the clk the host drives; B: link bit time in clk (LINK register, 16 after reset)
    # uart_kw: arguments of machine.UART for the link, incl. flow=UART.CTS on H_RDY
    # pio: None = hardware UART (default); a dict (tx, rx, rdy, sm) = PIO link (docs/spec/pio_link_design.md)
    def __init__(self, uart_id, clk_hz, B=16, pio=None, **uart_kw):
        Parser.__init__(self)
        self.clk_hz, self.B = clk_hz, B
        self.port = PioPort(clk_hz, B, **pio) if pio is not None else UartPort(uart_id, uart_kw)

    def op(self, code, *args):
        self.port.cmd(bytes((ESC, code) + args))

    def poll(self):
        lost = getattr(self.port, "lost", 0)
        d = self.port.read()
        if getattr(self.port, "lost", 0) != lost:
            self.buf, self.cur = bytearray(), None       # ring overrun: resync (HOSTIF 9)
            self.notes.append(0x100)
        if d:
            self.feed(d)

    def op_noflow(self, code):
        self.port.cmd_noflow(bytes((ESC, code)))

    timeout_ms = 200

    def wait_reply(self, code, ms=None):
        ms = ms or self.timeout_ms
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
        self.port.cmd(struct.pack("<%dH" % len(words), *words))

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
        self.port.data(data)

    def recv(self, e):
        self.poll()
        return bytes(self.rx.pop(e, b""))

    def set_rate(self, B, tries=3):
        # LINK register (HOSTIF 6): wait for H_TX idle, change the host baud, SYNC
        self.wrreg(0x01, {16: 0, 8: 1, 4: 2}[B])
        while not self.port.txdone():
            pass
        time.sleep_us(3 * 10 * self.B * 1_000_000 // self.clk_hz + 1)   # last byte out, H_TX idle
        self.port.set_rate(self.clk_hz, B)
        self.B = B
        for _ in range(tries):
            try:
                return self.sync()
            except OSError:
                self.buf = bytearray()
        raise OSError("link at B = %d failed" % B)

    def forward(self, out, ms):
        # pass-through: raw chip -> host bytes to out (USB CDC) for ms, no decoding on the board; PC side: Parser
        t = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t) < ms:
            d = self.port.read()
            if d:
                out.write(d)


def connect(clk_hz, uart_kw, pio=None, reset=None, B=4):
    # PIO link at B if possible, else (reset() = rst_n pulse) the hardware UART at B = 16; the caller then loads
    if pio is not None:
        try:
            k = Link(None, clk_hz, 16, pio=pio)
            k.sync()
            k.set_rate(B)
            return k
        except OSError:
            if reset:
                reset()
    k = Link(1, clk_hz, 16, **uart_kw)
    k.sync()
    return k
