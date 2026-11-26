# Host side of the debug programs (docs/spec/programs/debug_design.md): JTAG controller commands and TAP walks
# (jtag_ctrl.s), SWD host transactions (swd_host.s), SWD DP emulator box service (swd_dp.s). MicroPython and CPython.

# ---------------- JTAG (jtag_ctrl.s) ----------------
NEXT = {"TLR": ("RTI", "TLR"), "RTI": ("RTI", "SDR"), "SDR": ("CDR", "SIR"), "CDR": ("SHDR", "E1DR"),
        "SHDR": ("SHDR", "E1DR"), "E1DR": ("PDR", "UDR"), "PDR": ("PDR", "E2DR"), "E2DR": ("SHDR", "UDR"),
        "UDR": ("RTI", "SDR"), "SIR": ("CIR", "TLR"), "CIR": ("SHIR", "E1IR"), "SHIR": ("SHIR", "E1IR"),
        "E1IR": ("PIR", "UIR"), "PIR": ("PIR", "E2IR"), "E2IR": ("SHIR", "UIR"), "UIR": ("RTI", "SDR")}


def tms_path(src, dst):
    # shortest TMS sequence from src to dst (breadth first); [] when equal
    if src == dst:
        return []
    seen, q = {src: []}, [src]
    while q:
        s = q.pop(0)
        for b in (0, 1):
            n = NEXT[s][b]
            if n not in seen:
                seen[n] = seen[s] + [b]
                if n == dst:
                    return seen[n]
                q.append(n)
    raise ValueError(dst)


def _chunks(bits, on_tms, held, tag):
    out = []
    for k in range(0, len(bits), 8):
        b = bits[k:k + 8]
        v = 0
        for i, x in enumerate(b):
            v |= (x & 1) << i
        out.append(((len(b) - 1) << 2 | (2 if on_tms else 0) | held, v, len(b), tag))
    return out


class Jtag:
    # Builds commands (header, bits, n, tag); tag = index of the scan whose TDO bits the command returns, else None.
    def __init__(self):
        self.state, self.cmds, self.nscan = None, [], 0

    def reset(self, end="RTI"):
        self.cmds += _chunks([1] * 5, True, 0, None)
        self.state = "TLR"
        self.goto(end)
        return self

    def goto(self, st):
        p = tms_path(self.state, st)
        self.cmds += _chunks(p, True, 0, None)
        self.state = st
        return self

    def clocks(self, n, tms=0):
        # n TCK cycles with TMS constant (Run-Test/Idle when tms = 0)
        self.cmds += _chunks([tms] * n, True, 0, None)
        for _ in range(n):
            self.state = NEXT[self.state][tms]
        return self

    def scan(self, ir, value, n, end="RTI"):
        # shift n bits of value (LSB first) through IR (ir = True) or DR; TDO bits returned by result()
        self.goto("SHIR" if ir else "SHDR")
        bits = [(value >> i) & 1 for i in range(n)]
        self.cmds += _chunks(bits[:-1], False, 0, self.nscan) + _chunks(bits[-1:], False, 1, self.nscan)
        self.nscan += 1
        self.state = "E1IR" if ir else "E1DR"
        self.goto(end)
        return self

    def data(self):
        return bytes(x for h, v, n, t in self.cmds for x in (h, v))

    def result(self, replies):
        # replies: one byte per command, in order -> [value per scan]
        out, pos = [0] * self.nscan, [0] * self.nscan
        for (h, v, n, t), r in zip(self.cmds, replies):
            if t is not None:
                out[t] |= (r & ((1 << n) - 1)) << pos[t]
                pos[t] += n
        return out


# ---------------- SWD host (swd_host.s) ----------------
ACK_OK, ACK_WAIT, ACK_FAULT = 1, 2, 4


def parity(v):
    p = 0
    while v:
        p ^= v & 1
        v >>= 1
    return p


def swd_req(ap, rnw, a):
    # request byte as on the wire, bit 0 first: start, APnDP, RnW, A[2], A[3], parity, stop, park
    b = (ap & 1) | (rnw & 1) << 1 | ((a >> 2) & 3) << 2
    return 1 | b << 1 | parity(b) << 5 | 1 << 7


def swd_raw(bits, release=False):
    # raw bits (list), driven or sampled with SWDIO released; one reply byte per 8 bits
    out = b""
    for k in range(0, len(bits), 8):
        b = bits[k:k + 8]
        v = 0
        for i, x in enumerate(b):
            v |= (x & 1) << i
        out += bytes(((len(b) - 1) << 2 | (2 if release else 0), v))
    return out


def swd_line_reset(idle=8):
    return swd_raw([1] * 56) + swd_raw([0] * idle)


def swd_jtag_to_swd(idle=8):
    seq = [(0xE79E >> i) & 1 for i in range(16)]
    return swd_raw([1] * 56 + seq + [1] * 56) + swd_raw([0] * idle)


def swd_targetsel(v):
    # SWD v2 multidrop: TARGETSEL write (DP 0xC), no ACK from any target: request, 5 released cycles, data + parity
    req = [(swd_req(0, 0, 0xC) >> i) & 1 for i in range(8)]
    data = [(v >> i) & 1 for i in range(32)] + [parity(v)]
    return swd_raw(req) + swd_raw([1] * 5, release=True) + swd_raw(data)


def swd_read(ap, a):
    return bytes([swd_req(ap, 1, a)])


def swd_write(ap, a, v):
    return bytes([swd_req(ap, 0, a), v & 255, (v >> 8) & 255, (v >> 16) & 255, (v >> 24) & 255, parity(v)])


def swd_replies(ops, rx):
    # ops: list of ("r"|"w"|"raw", nbytes for raw); rx: reply bytes -> [(ack, value, parity_ok) | raw byte]
    out, i = [], 0
    for op in ops:
        if op[0] == "raw":
            out += list(rx[i:i + op[1]])
            i += op[1]
            continue
        ack = rx[i]
        i += 1
        if op[0] == "r" and ack == ACK_OK:
            v = rx[i] | rx[i + 1] << 8 | rx[i + 2] << 16 | rx[i + 3] << 24
            out.append((ack, v, (rx[i + 4] & 1) == parity(v)))
            i += 5
        else:
            out.append((ack, None, None))
    return out


def swd_xfer(port, op, ap, a, v=0, retries=100):
    # one access with WAIT retries; port.send(bytes), port.recv(n) -> bytes. -> (ack, value or None)
    for _ in range(retries):
        port.send(swd_read(ap, a) if op == "r" else swd_write(ap, a, v))
        ack = port.recv(1)[0]
        if ack == ACK_OK and op == "r":
            d = port.recv(5)
            val = d[0] | d[1] << 8 | d[2] << 16 | d[3] << 24
            if (d[4] & 1) != parity(val):
                return "parity", val
            return ack, val
        if ack != ACK_WAIT:
            return ack, None
    return ACK_WAIT, None


# ---------------- SWD DP emulator (swd_dp.s): the box service ----------------
class DpBox:
    # Serves the chip's AP accesses and SELECT writes (request bytes 0x10 | A3 A2 RnW APnDP from the stream) as an
    # AP register file keyed by (APSEL, address). rd(addr, n) -> words, wr(addr, words): RDMEM / WRMEM (ST last).
    # fail: set of (apsel, addr) whose accesses report an AP error (STICKYERR).
    def __init__(self, base, ap=None, fail=()):
        self.base, self.ap, self.fail, self.select, self.log = base, dict(ap or {}), set(fail), 0, []

    def serve(self, req, rd, wr):
        k = req & 15
        isap, rnw, a = k & 1, k >> 1 & 1, (k >> 2 & 3) << 2
        b = self.base
        if not rnw:
            dl, dh = rd(b + 3, 2)
            v = dl | dh << 16
        key = (self.select >> 24, (self.select & 0xF0) | a)
        err = isap and key in self.fail
        if not isap:
            self.select = v
            self.log.append(("select", v))
            wr(b + 2, [0])
        elif rnw:
            v = self.ap.get(key, 0)
            self.log.append(("r", key, v))
            wr(b, [v & 0xFFFF, v >> 16, 0x20 if err else 0])
        else:
            if not err:
                self.ap[key] = v
            self.log.append(("w", key, v))
            wr(b + 2, [0x20 if err else 0])


# ---------------- SWO (uart_rx.s receives the NRZ bytes unchanged) ----------------
def itm_decode(data):
    # ITM/DWT packet stream -> [("sync",), ("overflow",), ("sw"|"hw", port, value), ("ts", value)]
    out, i, n = [], 0, len(data)
    while i < n:
        h = data[i]
        if h == 0:
            j = i
            while j < n and data[j] == 0:
                j += 1
            if j < n and data[j] == 0x80 and j - i >= 5:
                out.append(("sync",))
                i = j + 1
                continue
            i += 1
            continue
        if h == 0x70:
            out.append(("overflow",))
            i += 1
        elif h & 3:
            size = {1: 1, 2: 2, 3: 4}[h & 3]
            if i + size >= n:
                break
            v = 0
            for k in range(size):
                v |= data[i + 1 + k] << (8 * k)
            out.append(("hw" if h & 4 else "sw", h >> 3, v))
            i += 1 + size
        elif h & 0x0F == 0 and not h & 0x80:
            out.append(("ts", h >> 4))                     # short local timestamp
            i += 1
        elif h & 0xCF == 0xC0:
            v, k, i = 0, 0, i + 1
            while i < n:
                v |= (data[i] & 0x7F) << (7 * k)
                k += 1
                i += 1
                if not data[i - 1] & 0x80:
                    break
            out.append(("ts", v))
        else:
            i += 1                                         # reserved or extension: skipped
    return out
