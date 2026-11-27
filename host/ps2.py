# Host side of the PS/2 programs (docs/spec/programs/ps2_design.md): keyboard and mouse personalities that run
# behind ps2_dev.s (one byte in flight), and a driver for ps2_host.s. MicroPython and CPython.
# Stream items: a data byte (int) or ("ev", code).

DEV_SENT, DEV_ABORT, DEV_PERR, DEV_FERR, DEV_RABORT, DEV_DROP = 1, 2, 3, 4, 5, 6
HOST_ACK, HOST_NACK, HOST_PERR, HOST_FERR, HOST_TMO = 1, 2, 3, 4, 5
ACK, RESEND, ERROR, BAT_OK, ECHO = 0xFA, 0xFE, 0xFC, 0xAA, 0xEE

# scan code set 2: key name -> make code (0xE0xx = extended)
SET2 = {"A": 0x1C, "B": 0x32, "C": 0x21, "D": 0x23, "E": 0x24, "F": 0x2B, "1": 0x16, "2": 0x1E, "SPACE": 0x29,
        "ENTER": 0x5A, "ESC": 0x76, "LSHIFT": 0x12, "CAPS": 0x58, "F1": 0x05, "UP": 0xE075, "DOWN": 0xE072,
        "LEFT": 0xE06B, "RIGHT": 0xE074, "RCTRL": 0xE014, "DEL": 0xE071}


def key_codes(name, up=False):
    c = SET2[name]
    out = [0xE0] if c > 0xFF else []
    return out + ([0xF0] if up else []) + [c & 0xFF]


class Keyboard:
    # command(byte) -> (response bytes, flush pending data)
    def __init__(self):
        self.reset_state()
        self.arg = None
        self.last = None

    def reset_state(self):
        self.leds, self.set, self.enabled, self.typematic = 0, 2, True, 0x2B

    def command(self, b):
        if self.arg is not None:
            op, self.arg = self.arg, None
            if op == 0xED:
                self.leds = b & 7
                return [ACK], False
            if op == 0xF3:
                self.typematic = b & 0x7F
                return [ACK], False
            if op == 0xF0:
                if b == 0:
                    return [ACK, self.set], False
                if b in (1, 2, 3):
                    self.set = b
                    return [ACK], False
                return [RESEND], False
        if b == 0xFF:
            self.reset_state()
            return [ACK, BAT_OK], True
        if b == 0xFE:
            return ([self.last] if self.last is not None else []), False
        if b == 0xEE:
            return [ECHO], False
        if b in (0xED, 0xF3, 0xF0):
            self.arg = b
            return [ACK], False
        if b == 0xF2:
            return [ACK, 0xAB, 0x83], False
        if b == 0xF4:
            self.enabled = True
            return [ACK], False
        if b in (0xF5, 0xF6):
            self.reset_state()
            self.enabled = b == 0xF6
            return [ACK], True
        return [RESEND], False

    def key(self, name, up=False):
        return key_codes(name, up) if self.enabled else []


class Mouse:
    # stream mode; IntelliMouse: sample rates 200, 100, 80 -> ID 3 (wheel); then 200, 200, 80 -> ID 4 (5 buttons)
    def __init__(self):
        self.reset_state()
        self.arg = None
        self.last = None

    def reset_state(self):
        self.id, self.rate, self.res, self.scale, self.enabled, self.rates = 0, 100, 2, 1, False, []

    def command(self, b):
        if self.arg is not None:
            op, self.arg = self.arg, None
            if op == 0xF3:
                self.rate = b
                self.rates = (self.rates + [b])[-3:]
                if self.rates == [200, 100, 80] and self.id == 0:
                    self.id = 3
                elif self.rates == [200, 200, 80] and self.id == 3:
                    self.id = 4
                return [ACK], False
            if op == 0xE8:
                self.res = b & 3
                return [ACK], False
        if b == 0xFF:
            self.reset_state()
            return [ACK, BAT_OK, 0x00], True
        if b == 0xFE:
            return ([self.last] if self.last is not None else []), False
        if b in (0xF3, 0xE8):
            self.arg = b
            return [ACK], False
        if b == 0xF2:
            return [ACK, self.id], False
        if b == 0xF4:
            self.enabled = True
            return [ACK], False
        if b in (0xF5, 0xF6):
            id_ = self.id
            self.reset_state()
            self.id = id_ if b == 0xF5 else 0
            return [ACK], True
        if b in (0xE6, 0xE7):
            self.scale = 1 if b == 0xE6 else 2
            return [ACK], False
        if b == 0xE9:
            return [ACK, (0x20 if self.enabled else 0) | (0x10 if self.scale == 2 else 0), self.res, self.rate], False
        if b == 0xEA:
            return [ACK], False
        return [RESEND], False

    def move(self, dx, dy, buttons=0, wheel=0):
        # one movement packet (3 bytes, 4 with ID 3 / 4); dx, dy clipped to 9-bit two's complement with overflow
        if not self.enabled:
            return []
        xo, yo = not -256 <= dx <= 255, not -256 <= dy <= 255
        dx, dy = max(-256, min(255, dx)), max(-256, min(255, dy))
        b0 = (yo << 7) | (xo << 6) | ((dy < 0) << 5) | ((dx < 0) << 4) | 8 | (buttons & 7)
        out = [b0, dx & 0xFF, dy & 0xFF]
        if self.id == 3:
            out.append(max(-128, min(127, wheel)) & 0xFF)
        elif self.id == 4:
            out.append((max(-8, min(7, wheel)) & 0x0F) | (buttons & 0x18) << 1)
        return out


def mouse_decode(pkt, id_=0):
    # packet -> (dx, dy, buttons, wheel, overflow)
    b0 = pkt[0]
    dx = pkt[1] - (256 if b0 & 0x10 else 0)
    dy = pkt[2] - (256 if b0 & 0x20 else 0)
    btn, wheel = b0 & 7, 0
    if id_ == 3:
        wheel = pkt[3] - (256 if pkt[3] & 0x80 else 0)
    elif id_ == 4:
        wheel = (pkt[3] & 0x0F) - (16 if pkt[3] & 8 else 0)
        btn |= (pkt[3] >> 1) & 0x18
    return dx, dy, btn, wheel, bool(b0 & 0xC0)


class DevLink:
    # a personality behind ps2_dev.s: one byte in flight (sent again after ABORT, put back after DROP), command
    # responses ahead of queued data, FE asked for after a parity error. out() -> bytes to give the chip now.
    def __init__(self, dev):
        self.dev, self.q, self.inflight, self.nresp, self.perr, self.hold = dev, [], None, 0, False, False
        self.cmds, self.errors, self.sent = [], [], []

    def queue(self, data):
        self.q += list(data)

    def item(self, it):
        if isinstance(it, tuple):
            c = it[1]
            if c == DEV_SENT:
                self.dev.last = self.inflight
                self.sent.append(self.inflight)
                self.inflight = None
            elif c in (DEV_ABORT, DEV_DROP):
                self.hold = c == DEV_DROP                 # a command byte follows
                if self.inflight is not None:
                    self.q.insert(self.nresp if c == DEV_DROP else 0, self.inflight)
                    if c == DEV_ABORT and self.nresp:
                        self.nresp += 1
                self.inflight = None
            elif c == DEV_PERR:
                self.perr = self.hold = True
            else:
                self.errors.append(c)
                if c == DEV_FERR:                         # framing error: ask the host to send again
                    self.q.insert(self.nresp, RESEND)
                    self.nresp += 1
            return
        self.cmds.append(it)
        self.hold = False
        if self.perr:
            self.perr = False
            resp, flush = [RESEND], False
        else:
            resp, flush = self.dev.command(it)
        rest = [] if flush else self.q[self.nresp:]
        self.q = self.q[:self.nresp] + resp + rest
        self.nresp += len(resp)

    def out(self):
        if self.inflight is not None or self.hold or not self.q:
            return b""
        self.inflight = self.q.pop(0)
        if self.nresp:
            self.nresp -= 1
        return bytes([self.inflight])


# ---------------- ps2_host.s ----------------
def h_release():
    return b"\x00"


def h_inhibit():
    return b"\x01"


def h_send(b):
    return bytes([2, b & 0xFF])


def h_parse(items):
    # chip items -> [("ack",) | ("nack",) | ("tmo",) | ("ferr",) | ("byte", v, parity ok)]
    out, perr = [], False
    for it in items:
        if isinstance(it, tuple):
            c = it[1]
            if c == HOST_PERR:
                perr = True
            else:
                out.append(({HOST_ACK: "ack", HOST_NACK: "nack", HOST_FERR: "ferr", HOST_TMO: "tmo"}.get(c, c),))
        else:
            out.append(("byte", it, not perr))
            perr = False
    return out


def transact(port, cmd, args=(), nresp=0, tries=3):
    # one device command over ps2_host.s: each byte must be ACKed on the wire and answered FA; FE -> send again.
    # port.send(bytes); port.recv() -> next parsed result. Returns the nresp response bytes, or raises.
    for b in [cmd] + list(args):
        for _ in range(tries):
            port.send(h_send(b))
            r = port.recv()
            if r != ("ack",):
                continue
            r = port.recv()
            if r[0] == "byte" and r[2] and r[1] == ACK:
                break
            if r[0] == "byte" and r[1] == ECHO and cmd == 0xEE:
                return [ECHO]
        else:
            raise IOError("PS/2 command %02X: byte %02X not acknowledged" % (cmd, b))
    out = []
    while len(out) < nresp:
        r = port.recv()
        if r[0] == "byte" and r[2]:
            out.append(r[1])
        elif r[0] == "byte" or r in (("ferr",), ("tmo",)):
            port.send(h_send(RESEND))
            if port.recv() != ("ack",):
                raise IOError("PS/2 resend not acknowledged")
        else:
            raise IOError("PS/2 command %02X: %s" % (cmd, r))
    return out
