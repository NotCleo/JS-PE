# USB LS host model and RP2350 stand-in for usb_ls.s, clocked per chip cycle (ISS and cocotb share them).
# Host: transactions with USB 2.0 retry rules (NAK -> retry, timeout/CRC -> up to 3 errors), control transfers.
# RP2350: descriptors, data toggle, IN buffer arming by WRMEM over the host link, bus reset from the uio4/uio5
# levels (its GPIOs); it sends no command with a reply (no RDMEM/RDREG/SYNC).
from models import (USB_J, USB_K, USB_SE0, USB_PID, UsbHostLine, usb_token, usb_data, usb_key, usb_decode,
                    usb_crc_preset, usb_crc16_reg)

ESC = 0x1B
EV_OK, EV_CRC, EV_INACK = 1, 2, 3
PID_NAME = {v: k for k, v in USB_PID.items()}

DEV_DESC = [18, 1, 0x10, 0x01, 0, 0, 0, 8, 0x09, 0x12, 0x01, 0x00, 0x00, 0x01, 0, 0, 0, 1]
REPORT_DESC = [0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00, 0x25, 0x01,
               0x75, 0x01, 0x95, 0x08, 0x81, 0x02, 0x95, 0x01, 0x75, 0x08, 0x81, 0x01, 0x95, 0x05, 0x75, 0x01,
               0x05, 0x08, 0x19, 0x01, 0x29, 0x05, 0x91, 0x02, 0x95, 0x01, 0x75, 0x03, 0x91, 0x01, 0x95, 0x06,
               0x75, 0x08, 0x15, 0x00, 0x25, 0x65, 0x05, 0x07, 0x19, 0x00, 0x29, 0x65, 0x81, 0x00, 0xC0]
CFG_DESC = ([9, 2, 34, 0, 1, 1, 0, 0xA0, 50] + [9, 4, 0, 0, 1, 3, 1, 1, 0] +
            [9, 0x21, 0x11, 0x01, 0, 1, 0x22, len(REPORT_DESC), 0] + [7, 5, 0x81, 3, 8, 0, 10])
REPORTS = [[0, 0, 0x04 + k // 2, 0, 0, 0, 0, 0] if k % 2 == 0 else [0] * 8 for k in range(10)]   # press / release


def req(bm, br, wv, wi, wl):
    return [bm, br, wv & 0xFF, wv >> 8, wi & 0xFF, wi >> 8, wl & 0xFF, wl >> 8]


def bus_state(uio_out, uio_oe, host_state):
    # chip drive wins (the host never drives at the same time: checked by the host model); released D+ low, D- high
    if uio_oe & 0x30:
        return (uio_out >> 4 & 1 if uio_oe & 0x10 else 0, uio_out >> 5 & 1 if uio_oe & 0x20 else 1), True
    return (host_state if host_state is not None else USB_J), False


class LinkParser:
    # chip -> host bytes (HOSTIF 4) -> items (engine, kind, value)
    def __init__(self):
        self.i, self.cur, self.items = 0, None, []

    def feed(self, got):
        while self.i < len(got):
            b = got[self.i]
            if b != ESC:
                self.items.append((self.cur, "d", b))
                self.i += 1
                continue
            if self.i + 1 >= len(got):
                return
            x = got[self.i + 1]
            self.i += 2
            if x == ESC:
                self.items.append((self.cur, "d", ESC))
            elif x <= 3:
                self.cur = x
            elif 0x40 <= x < 0x80:
                self.items.append((self.cur, "ev", x & 63))
            else:
                self.items.append((None, "note", x))


class UsbHost:
    def __init__(self, bit=32, reset_clk=1500, corrupt_setup=None, full=True):
        # full: True = enumeration + reports, False = up to the address switch, None = one GET_DESCRIPTOR
        self.bit, self.line, self.full = bit, UsbHostLine(bit), full
        self.log, self.oe = [], []
        self.now, self.wake, self.done = 0, 0, False
        self.reset_clk, self.corrupt_setup = reset_clk, corrupt_setup
        self.naks, self.timeouts, self.clash, self.dups = 0, 0, [], 0
        self.result = {}
        self.gen = self.script() if full is not None else self.get_device()

    # ---------------- per-cycle interface
    def drive(self, t):
        return self.line.state(t)

    def tick(self, t, st, chip_drives):
        if not self.log:
            self.log = [None] * t
            self.oe = [False] * t
        self.log.append(st)
        self.oe.append(chip_drives)
        if chip_drives and self.line.state(t) is not None:
            self.clash.append(t)
        self.now = t
        if not self.done and t >= self.wake:
            try:
                self.wake = next(self.gen)
            except StopIteration:
                self.done = True

    # ---------------- primitives (generators yielding a wake-up time)
    def send(self, pkts, gap=4):
        t = max(self.line.t, self.now + 1)
        for k, p in enumerate(pkts):
            self.line.packet(p, t=t if k == 0 else self.line.t + gap * self.bit)
        te = self.line.eop_ends[-1]
        yield int(self.line.t) + 1
        seen = None
        while True:
            if seen is None:
                seen = next((k for k in range(int(te), self.now + 1) if self.oe[k]), None)
                if seen is None and self.now > te + 18 * self.bit:
                    self.timeouts += 1
                    return None
            elif not any(self.oe[self.now - 8:self.now + 1]):
                p = [x for x in usb_decode(self.log, self.bit, int(te)) if x[0] >= seen - 2]
                self.line.t = max(self.line.t, self.now + 2 * self.bit)      # host inter-packet delay
                return p[0] if p else None
            yield self.now + 4

    def handshake(self, pid):
        self.line.packet([USB_PID[pid]], t=max(self.line.t, self.now + 2 * self.bit))
        yield int(self.line.t) + 1

    def idle(self, n_bits):
        self.line.t = max(self.line.t, self.now) + n_bits * self.bit
        yield int(self.line.t)

    def bus_reset(self):
        self.line.t = max(self.line.t, self.now)
        self.line._seg(self.reset_clk / self.bit, USB_SE0)
        self.line._seg(1, USB_J)
        yield from self.idle(100)                         # reset recovery (scaled)

    # ---------------- transactions
    def setup_txn(self, addr, payload):
        for _ in range(3):
            bad = self.corrupt_setup is not None and self.corrupt_setup(payload)
            if bad:
                self.corrupt_setup = None
            r = yield from self.send([usb_token("SETUP", addr, 0), usb_data("DATA0", payload, bad_crc=bad)])
            if r and r[3] and r[2] == [USB_PID["ACK"]]:
                return
            yield from self.idle(20)
        raise AssertionError("SETUP failed %s" % payload)

    def in_txn(self, addr, ep, tog, max_nak=400, poll_gap=30):
        errs = 0
        while True:
            r = yield from self.send([usb_token("IN", addr, ep)])
            if r is None or not r[3]:
                errs += 1
                assert errs < 3, "IN errors"
                yield from self.idle(20)
                continue
            pid = r[2][0]
            if pid == USB_PID["NAK"]:
                self.naks += 1
                max_nak -= 1
                assert max_nak > 0, "NAK forever"
                yield from self.idle(poll_gap)
                continue
            if pid == USB_PID["STALL"]:
                return "STALL"
            if usb_crc16_reg(r[2][1:]) != 0xB001:
                errs += 1                                 # corrupted data: no ACK, retry
                yield from self.idle(20)
                continue
            yield from self.handshake("ACK")
            if pid != USB_PID["DATA1" if tog else "DATA0"]:
                self.dups += 1                            # toggle mismatch: ACKed and dropped
                continue
            return r[2][1:-2]

    def out_txn(self, addr, ep, tog, payload):
        for _ in range(400):
            r = yield from self.send([usb_token("OUT", addr, ep), usb_data("DATA1" if tog else "DATA0", payload)])
            if r and r[3] and r[2] == [USB_PID["ACK"]]:
                return
            if r and r[2] == [USB_PID["NAK"]]:
                self.naks += 1
            yield from self.idle(20)
        raise AssertionError("OUT failed")

    def control_read(self, addr, setup):
        yield from self.line_keepalive()
        yield from self.setup_txn(addr, setup)
        n, data, tog = setup[6] | setup[7] << 8, [], 1
        while len(data) < n:
            p = yield from self.in_txn(addr, 0, tog)
            if p == "STALL":
                return "STALL"
            data += p
            tog ^= 1
            if len(p) < 8:
                break
        yield from self.out_txn(addr, 0, 1, [])
        return data

    def control_write(self, addr, setup):
        yield from self.line_keepalive()
        yield from self.setup_txn(addr, setup)
        p = yield from self.in_txn(addr, 0, 1)
        assert p == [], p

    def line_keepalive(self):
        self.line.keepalive(max(self.line.t, self.now) + 4 * self.bit)
        yield from self.idle(8)

    def get_device(self):
        # one control read at the address-0 keys of the image: SETUP, IN data stage (NAK until armed), OUT status
        yield from self.idle(20)
        self.result["dev0"] = yield from self.control_read(0, req(0x80, 6, 0x0100, 0, 18))
        yield from self.idle(20)

    # ---------------- enumeration of a HID keyboard
    def script(self, addr=7, n_reports=10, interval=200):
        yield from self.idle(20)
        yield from self.bus_reset()
        r = self.result
        r["dev0"] = yield from self.control_read(0, req(0x80, 6, 0x0100, 0, 64))
        yield from self.control_write(0, req(0x00, 5, addr, 0, 0))
        yield from self.idle(60)                          # SET_ADDRESS recovery (scaled)
        r["dev"] = yield from self.control_read(addr, req(0x80, 6, 0x0100, 0, 18))
        if not self.full:
            return
        r["cfg9"] = yield from self.control_read(addr, req(0x80, 6, 0x0200, 0, 9))
        r["cfg"] = yield from self.control_read(addr, req(0x80, 6, 0x0200, 0, r["cfg9"][2]))
        r["report"] = yield from self.control_read(addr, req(0x81, 6, 0x2200, 0, len(REPORT_DESC)))
        yield from self.control_write(addr, req(0x00, 9, 1, 0, 0))
        # as Linux/Windows: report descriptor and SET_IDLE after SET_CONFIGURATION, while a report is being armed
        r["report2"] = yield from self.control_read(addr, req(0x81, 6, 0x2200, 0, len(REPORT_DESC)))
        yield from self.control_write(addr, req(0x21, 0x0A, 0, 0, 0))
        r["reports"], tog = [], 0
        for _ in range(n_reports):
            p = yield from self.in_txn(addr, 1, tog, poll_gap=interval)
            r["reports"].append(p)
            tog ^= 1
        yield from self.idle(20)


class Rp2350:
    # stand-in for the RP2350 firmware. send(bytes) queues bytes on the host link (H_RX); queued() = bytes still
    # waiting in its UART. Events are attributed in stream order; actions follow after lat clk (firmware latency).
    def __init__(self, img, send, queued=lambda: 0, lat=1500, poll=60, link_b=16):
        self.inbuf, self.keys = img.label("inbuf"), img.label("keys")
        self.send_raw, self.queued, self.lat, self.poll, self.link_b = send, queued, lat, poll, link_b
        self.ops, self.parser, self.pkt = [], LinkParser(), []
        self.todo = []                                     # (t_due, fn)
        self.addr, self.configured = 0, False
        self.chunks, self.tog0, self.tog1, self.after = [], 1, 0, None
        self.reports = [list(x) for x in REPORTS]
        self.armed, self.words, self.stale, self.fence_until = None, None, None, 0
        self.se0_polls, self.resets, self.crc_events, self.notes, self.setups = 0, 0, 0, [], []
        self.rearms, self.fenced_acks, self.now = 0, 0, 0

    def wrmem(self, a, words):
        self.ops.append(0x70)
        self.send_raw(bytes([ESC, 0x70, a & 0xFF, a >> 8, len(words) - 1] + [x for w in words for x in (w & 0xFF, w >> 8)]))

    def later(self, fn):
        self.todo.append((self.now + self.lat, fn))

    def set_keys(self, addr):
        self.wrmem(self.keys, [usb_key(addr, 0), 0, usb_key(addr, 1)])

    def arm(self, ep, pid=None, data=(), stall=False):
        if stall:
            words = [16, 0, 0x80 | USB_PID["STALL"] << 8]
        else:
            d = list(data) + [0] * (len(data) & 1)
            words = [32 + 8 * len(data), usb_crc_preset(USB_PID[pid]), 0x80 | USB_PID[pid] << 8] + \
                [d[i] | d[i + 1] << 8 for i in range(0, len(d), 2)]
        words += [0] * (7 - len(words))
        self.words = words + [usb_key(self.addr, 0), usb_key(self.addr, ep)]          # BUFKEY written last
        self.armed = ep
        self.wrmem(self.inbuf, self.words)

    def tick(self, t, st, got):
        self.now = t
        if t % self.poll == 0:                             # GPIO poll of D+, D-: SE0 on 3 reads over 2.5 us
            self.se0_polls = self.se0_polls + 1 if st == USB_SE0 else 0
            if self.se0_polls == 3:
                self.bus_reset()
        self.parser.feed(got)
        for e, kind, v in self.parser.items:
            if kind == "note":
                self.notes.append(v)
            elif e == 1 and kind == "d":
                self.pkt.append(v)
            elif e == 1 and kind == "ev":
                self.event(v, self.pkt)
                self.pkt = []
        self.parser.items = []
        due = [x for x in self.todo if x[0] <= t]
        self.todo = [x for x in self.todo if x[0] > t]
        for _, fn in due:
            fn()

    def bus_reset(self):
        self.resets += 1
        self.addr, self.configured, self.chunks, self.after, self.todo = 0, False, [], None, []
        self.armed = self.stale = None
        self.set_keys(0)                                   # also BUFKEY 0

    # ---------------- events, in stream order
    def event(self, ev, pkt):
        if ev == EV_INACK:
            if self.armed is None and self.stale is not None and self.now < self.fence_until:
                ep, self.stale = self.stale, None          # an arm that landed after the chip's disarm was used
                self.fenced_acks += 1
            else:
                ep, self.armed = self.armed, None
            if ep == 1:
                self.reports.pop(0)
                self.tog1 ^= 1
                self.later(self.resume)
            elif ep == 0:
                self.later(self.ep0_acked)
            return
        if ev not in (EV_OK, EV_CRC):
            return
        self.crc_events += ev == EV_CRC
        tok = pkt[-1] if pkt else None
        if tok not in (USB_PID["SETUP"], USB_PID["OUT"]):
            if self.armed is not None:                     # a corrupted token disarmed the buffer: re-arm it
                self.rearms += 1
                words = self.words
                self.later(lambda: self.wrmem(self.inbuf, words))
            return
        # SETUP/OUT to us: the chip disarmed the buffer; an arm still in flight may land after that: fence it
        self.stale, self.armed = self.armed, None
        self.later(self.fence)
        if ev == EV_OK and tok == USB_PID["SETUP"] and pkt[0] == USB_PID["DATA0"]:
            payload = pkt[1:-3]
            self.setups.append(payload)
            self.todo.append((self.now + self.lat + 1, lambda: self.after_fence(lambda: self.setup(payload))))
        elif ev == EV_OK and tok == USB_PID["SETUP"]:            # bad DATA PID, ACKed by the chip: request unknown
            self.todo.append((self.now + self.lat + 1, lambda: self.after_fence(lambda: self.arm(0, stall=True))))
        else:
            self.todo.append((self.now + self.lat + 1, lambda: self.after_fence(self.resume)))
        # OUT data: status stage (zero length, DATA1); a duplicate would be dropped by its toggle

    def fence(self):
        self.wrmem(self.keys + 1, [0])
        self.fence_until = self.now + (self.queued() + 2) * 10 * self.link_b + 200

    def after_fence(self, fn):
        if self.now < self.fence_until:
            self.todo.append((self.fence_until, fn))
        else:
            self.stale = None
            fn()

    # ---------------- control endpoint and reports
    def setup(self, s):
        self.stale = None
        bm, br, wv, wl = s[0], s[1], s[2] | s[3] << 8, s[6] | s[7] << 8
        self.tog0, self.after, self.chunks = 1, None, []
        if br == 6 and bm in (0x80, 0x81):
            d = {0x01: DEV_DESC, 0x02: CFG_DESC, 0x22: REPORT_DESC}.get(wv >> 8)
            if d is None:
                return self.arm(0, stall=True)
            d = d[:wl]
            self.chunks = [d[i:i + 8] for i in range(0, len(d), 8)]
            if len(d) % 8 == 0 and len(d) < wl:
                self.chunks.append([])
        elif br == 5 and bm == 0:
            self.chunks, self.after = [[]], ("addr", wv & 0x7F)
        elif br == 9 and bm == 0:
            self.chunks, self.after = [[]], ("config", wv)
        elif br == 0x0A and bm == 0x21:                    # SET_IDLE
            self.chunks = [[]]
        else:
            return self.arm(0, stall=True)
        self.next_chunk()

    def next_chunk(self):
        self.arm(0, "DATA1" if self.tog0 else "DATA0", self.chunks.pop(0))

    def ep0_acked(self):
        if self.chunks:
            self.tog0 ^= 1
            return self.next_chunk()
        if self.after:
            kind, v = self.after
            self.after = None
            if kind == "addr":
                self.addr = v
                self.set_keys(v)                          # key switch only after the status-stage ACK
            else:
                self.configured, self.tog1 = True, 0
        self.resume()

    def resume(self):
        if self.configured and self.reports and self.armed is None and not self.chunks and self.after is None:
            self.arm(1, "DATA1" if self.tog1 else "DATA0", self.reports[0])
