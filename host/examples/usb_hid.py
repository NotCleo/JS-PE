# USB Low-Speed HID keyboard demo (usb_ls.s, added after rtl-freeze-v1; docs/spec/programs/usb_ls_design.md).
# *** NOT RUN ON HARDWARE. *** The same rules run against the chip in the ISS and in RTL lockstep
# (sw/tests/usb_host.py Rp2350, test/test_usb_ls.py); this file is their MicroPython port.
# Wiring (docs/INTERFACING.md, USB LS): uio[4] = D+, uio[5] = D- (27 Ohm each), uio[6] = 1.5 kOhm to D- (attach).
# The RP2350 GPIOs on uio[4]/uio[5] (GPIO29/GPIO30) stay inputs without pulls; GPIO31 (uio[6]) too.
# clk 48 MHz (BIT = 32 clk), host link B = 16 (3 Mbaud). On the PC, for the build on the board:
#   512 words (default): python sw/jspe_asm.py docs/spec/programs/usb_ls.s -o usb_ls512
#   128 words (flop fallback): python sw/jspe_asm.py -D MEMWORDS=128 --mem 128 docs/spec/programs/usb_ls.s -o usb_ls
# Copy the .bin and host/jspe.py to the board, then run this file. The chip only sends the RP2350 data events;
# descriptors, addresses, toggles and keys are this file's job.
import time
import machine
import rp2
from machine import Pin, UART
from ttboard.demoboard import DemoBoard
import jspe

CLK = 48_000_000
MEMWORDS = 512                          # build on the board: 512 (SRAM, shipped) or 128 (flop fallback)
IMAGE = "usb_ls.bin" if MEMWORDS == 128 else "usb_ls512.bin"
PIDCHK = MEMWORDS > 128                 # 128-word build: the RP2350 STALLs a SETUP whose DATA PID was corrupted
BUF = 118 if MEMWORDS == 128 else 465   # cleo_mem.inc USB_BUF_FLOP / USB_BUF: IN buffer (7 words), KEY0, BUFKEY, KEY1
KEYS = BUF + 7
TEXT = "Hello from Cleo 123\n"
EV_OK, EV_CRC, EV_INACK = 1, 2, 3
PID = {"OUT": 0xE1, "IN": 0x69, "SETUP": 0x2D, "DATA0": 0xC3, "DATA1": 0x4B, "ACK": 0xD2, "NAK": 0x5A, "STALL": 0x1E}

REPORT_DESC = bytes([0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00, 0x25, 0x01,
                     0x75, 0x01, 0x95, 0x08, 0x81, 0x02, 0x95, 0x01, 0x75, 0x08, 0x81, 0x01, 0x95, 0x05, 0x75, 0x01,
                     0x05, 0x08, 0x19, 0x01, 0x29, 0x05, 0x91, 0x02, 0x95, 0x01, 0x75, 0x03, 0x91, 0x01, 0x95, 0x06,
                     0x75, 0x08, 0x15, 0x00, 0x25, 0x65, 0x05, 0x07, 0x19, 0x00, 0x29, 0x65, 0x81, 0x00, 0xC0])
DEV_DESC = bytes([18, 1, 0x10, 0x01, 0, 0, 0, 8, 0x09, 0x12, 0x01, 0x00, 0x00, 0x01, 0, 0, 0, 1])   # pid.codes test ID
CFG_DESC = bytes([9, 2, 34, 0, 1, 1, 0, 0xA0, 50, 9, 4, 0, 0, 1, 3, 1, 1, 0,
                  9, 0x21, 0x11, 0x01, 0, 1, 0x22, len(REPORT_DESC), 0, 7, 5, 0x81, 3, 8, 0, 10])


def crc16_reg(data, crc=0xFFFF):
    for byte in data:
        for i in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc ^ (byte >> i)) & 1 else crc >> 1
    return crc


def key(addr, ep):
    # token key: the chip's LFSR after token bytes b1, b2 (CRC5 included) from 0xFFFF
    v = (addr & 0x7F) | (ep & 0xF) << 7
    crc = 0x1F
    for i in range(11):
        crc = (crc >> 1) ^ 0x14 if (crc ^ (v >> i)) & 1 else crc >> 1
    w = v | (crc ^ 0x1F) << 11
    return crc16_reg((w & 0xFF, w >> 8))


def crc_preset(pid):
    # LFSR start value that is 0xFFFF after the chip shifts SYNC | PID << 8
    word, s = 0x80 | pid << 8, 0xFFFF
    for i in range(15, -1, -1):
        fb = s >> 15 & 1
        s = ((s ^ (0xA001 if fb else 0)) << 1 | (fb ^ (word >> i & 1))) & 0xFFFF
    return s


def keycode(ch):
    if "a" <= ch <= "z":
        return 0, 4 + ord(ch) - 97
    if "A" <= ch <= "Z":
        return 2, 4 + ord(ch) - 65                       # left shift
    if "1" <= ch <= "9":
        return 0, 30 + ord(ch) - 49
    return {"0": (0, 39), "\n": (0, 40), " ": (0, 44)}.get(ch, (0, 0))


# SE0 >= 4 us on D+, D- (GPIO29, GPIO30): bus reset (keep-alive SE0 is 1.33 us). 32 samples x 5 PIO clk at 40 MHz.
@rp2.asm_pio()
def se0_timer():
    label("top")
    set(y, 31)
    label("loop")
    mov(isr, null)
    in_(pins, 2)
    mov(x, isr)
    jmp(not_x, "low")
    jmp("top")
    label("low")
    jmp(y_dec, "loop")
    irq(rel(0))
    label("hold")                                       # once per reset: wait for the SE0 to end
    mov(isr, null)
    in_(pins, 2)
    mov(x, isr)
    jmp(not_x, "hold")
    jmp("top")


class Device:
    def __init__(self, link):
        self.link = link
        self.reset_flag = False
        self.reports = []
        self.bus_reset()

    def bus_reset(self):
        self.addr, self.configured, self.chunks, self.after = 0, False, [], None
        self.tog0, self.tog1, self.armed, self.stale, self.words = 1, 0, None, None, None
        self.link.wrmem(KEYS, [key(0, 0), 0, key(0, 1)])  # address-0 keys, BUFKEY 0

    def arm(self, ep, pid=None, data=b"", stall=False):
        if stall:
            words = [16, 0, 0x80 | PID["STALL"] << 8]
        else:
            d = bytes(data) + bytes(len(data) & 1)
            words = [32 + 8 * len(data), crc_preset(PID[pid]), 0x80 | PID[pid] << 8]
            words += [d[i] | d[i + 1] << 8 for i in range(0, len(d), 2)]
        words += [0] * (7 - len(words))
        self.words = words + [key(self.addr, 0), key(self.addr, ep)]      # one WRMEM, BUFKEY last
        self.armed = ep
        self.link.wrmem(BUF, self.words)

    def fence(self):
        # SETUP/OUT to us: the chip wrote BUFKEY 0, but an arm still in the UART can land after it. Write 0, wait
        # until it has landed; INACK events that arrive before that belong to the stale arm (rule b)
        self.stale, self.armed = self.armed, None
        self.link.wrmem(KEYS + 1, [0])
        while not self.link.port.txdone():
            pass
        time.sleep_us(50)
        self.link.poll()
        return len(self.link.events)

    def event(self, ev, pkt, stale_window):
        if ev == EV_INACK:
            if self.armed is None and self.stale is not None and stale_window:
                self.stale = None
                return
            ep, self.armed = self.armed, None
            if ep == 1:
                self.reports.pop(0)
                self.tog1 ^= 1
            elif ep == 0:
                self.ep0_acked()
            return self.resume()
        if ev not in (EV_OK, EV_CRC):
            return
        tok = pkt[-1] if pkt else None
        if tok not in (PID["SETUP"], PID["OUT"]):
            if self.armed is not None:                   # a corrupted token disarmed the buffer: re-arm (rule c)
                self.link.wrmem(BUF, self.words)
            return None
        mark = self.fence()
        if ev == EV_OK and tok == PID["SETUP"] and pkt[0] == PID["DATA0"] and len(pkt) == 12:
            self.setup(pkt[1:9])
        elif ev == EV_OK and tok == PID["SETUP"] and not PIDCHK:
            self.arm(0, stall=True)                      # DATA PID corrupted but ACKed: the host retries (rule d)
        else:
            self.resume()
        return mark

    def setup(self, s):
        self.stale = None
        bm, br, wv, wl = s[0], s[1], s[2] | s[3] << 8, s[6] | s[7] << 8
        self.tog0, self.after, self.chunks = 1, None, []
        if br == 6 and bm in (0x80, 0x81):
            d = {1: DEV_DESC, 2: CFG_DESC, 0x22: REPORT_DESC}.get(wv >> 8)
            if d is None:
                return self.arm(0, stall=True)
            d = d[:wl]
            self.chunks = [d[i:i + 8] for i in range(0, len(d), 8)]
            if len(d) % 8 == 0 and len(d) < wl:
                self.chunks.append(b"")
        elif br == 5 and bm == 0:
            self.chunks, self.after = [b""], ("addr", wv & 0x7F)
        elif br == 9 and bm == 0:
            self.chunks, self.after = [b""], ("config", wv)
        elif br == 0x0A and bm == 0x21:                  # SET_IDLE
            self.chunks = [b""]
        else:
            return self.arm(0, stall=True)
        self.arm(0, "DATA1", self.chunks.pop(0))

    def ep0_acked(self):
        if self.chunks:
            self.tog0 ^= 1
            return self.arm(0, "DATA1" if self.tog0 else "DATA0", self.chunks.pop(0))
        if self.after:
            kind, v = self.after
            self.after = None
            if kind == "addr":
                self.addr = v
                self.link.wrmem(KEYS, [key(v, 0), 0, key(v, 1)])   # new keys only after the status-stage ACK
            else:
                self.configured, self.tog1 = True, 0

    def resume(self):
        if self.configured and self.reports and self.armed is None and not self.chunks and self.after is None:
            self.arm(1, "DATA1" if self.tog1 else "DATA0", self.reports[0])

    def type_text(self, text):
        for ch in text:
            m, c = keycode(ch)
            self.reports += [bytes((m, 0, c, 0, 0, 0, 0, 0)), bytes(8)]
        self.resume()


tt = DemoBoard.get()
tt.shuttle.tt_um_notcleo_jspe.enable()
tt.clock_project_PWM(CLK)
assert machine.freq() % CLK == 0, "clk is not an exact division of the system clock"
tt.reset_project(True)
for g in (29, 30, 31):
    Pin(g, Pin.IN, pull=None)                            # uio[4..6]: inputs, no pulls, during USB operation
link = jspe.Link(1, CLK, baudrate=CLK // 16, tx=Pin(20), rx=Pin(37), cts=Pin(38), flow=UART.CTS, rxbuf=8192)
time.sleep_ms(1)
tt.reset_project(False)
time.sleep_ms(1)
link.sync()
link.load(IMAGE)                                         # no RDMEM / RDREG / SYNC after this (HOST_INTERFACE 7)
dev = Device(link)
sm = rp2.StateMachine(0, se0_timer, freq=40_000_000, in_base=Pin(29))
sm.irq(lambda _: setattr(dev, "reset_flag", True))
sm.active(1)

rx, used, typed, t_cfg = None, 0, False, None
while True:
    if dev.reset_flag:
        dev.reset_flag = False
        dev.bus_reset()
        typed, t_cfg = False, None
    link.poll()
    i, mark = 0, 0
    while i < len(link.events):
        e, code, idx = link.events[i]
        if e == 1:
            rx = link.rx.setdefault(1, bytearray())
            pkt, used = bytes(rx[used:idx]), idx
            m = dev.event(code, pkt, i < mark)
            if m:
                mark = m
        i += 1
    del link.events[:i]
    if rx is not None and used:
        del rx[:used]
        used = 0
    if dev.configured and not typed:
        t_cfg = t_cfg or time.ticks_ms()
        if time.ticks_diff(time.ticks_ms(), t_cfg) > 1000:
            dev.type_text(TEXT)
            typed = True
