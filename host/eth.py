# Host side of the Ethernet family (docs/spec/programs/eth_design.md): link code words and autonegotiation
# arbitration over lp_tx.s / lp_rx.s, the CRC-32 helper for crc32.s, MDIO frame builders for mdio_ctrl.s and a PHY
# register model behind mdio_phy.s. MicroPython and CPython. Stream items: a data byte (int) or ("ev", code).

LP_NLP, LP_FLP, LP_OFF = 0x00, 0x01, 0x02
RX_UP, RX_DOWN, RX_FLP, RX_BAD = 1, 2, 3, 4
MDIO_NOPHY = 1

SEL_8023 = 0x01
A_10HD, A_10FD, A_100HD, A_100FD, A_T4, A_PAUSE, A_APAUSE = 0x20, 0x40, 0x80, 0x100, 0x200, 0x400, 0x800
RF, ACK, NP = 0x2000, 0x4000, 0x8000


def code_word(abilities=A_10HD | A_10FD, ack=False, rf=False, np=False, sel=SEL_8023):
    return (sel & 0x1F) | (abilities & 0x1FE0) | (ACK if ack else 0) | (RF if rf else 0) | (NP if np else 0)


def lp_cmd(mode, word=0):
    return [LP_FLP, word & 0xFF, word >> 8] if mode == LP_FLP else [mode]


def rx_parse(items):
    # lp_rx stream -> [("up",), ("down",), ("flp", word), ("bad",)]
    out, k = [], 0
    while k < len(items):
        it = items[k]
        if it == ("ev", RX_FLP):
            if k + 2 >= len(items):
                break
            out.append(("flp", items[k + 1] | items[k + 2] << 8))
            k += 3
            continue
        out.append({("ev", RX_UP): ("up",), ("ev", RX_DOWN): ("down",), ("ev", RX_BAD): ("bad",)}.get(it, ("?", it)))
        k += 1
    return out


def resolve(local, partner):
    # 802.3 annex 28B priority resolution over the common technologies -> name or None
    common = local & partner & 0x1FE0
    for bit, name in ((A_100FD, "100FD"), (A_T4, "T4"), (A_100HD, "100HD"), (A_10FD, "10FD"), (A_10HD, "10HD")):
        if common & bit:
            return name
    return None


class Autoneg:
    # 802.3 28.3 arbitration, base page only: ability detect (3 identical words) -> send ACK -> acknowledge detect
    # (3 identical words with ACK) -> complete acknowledge (6 more bursts with ACK) -> resolved. A 10BASE-T partner
    # without autonegotiation (lp_rx link up from NLPs) is taken by parallel detection.
    def __init__(self, abilities=A_10HD | A_10FD):
        self.local = code_word(abilities)
        self.state, self.seen, self.n, self.partner, self.result = "ability", [], 0, None, None

    def start(self):
        return lp_cmd(LP_FLP, self.local)

    def item(self, ev):
        # lp_rx parsed item -> lp_tx command bytes to send (possibly empty)
        if ev[0] == "up" and self.state != "done":
            self.state, self.result = "done", "10HD (parallel detection)"
            return lp_cmd(LP_NLP)
        if ev[0] != "flp" or self.state == "done":
            return []
        w = ev[1]
        self.seen = (self.seen + [w])[-3:]
        same = len(self.seen) == 3 and self.seen.count(w) == 3
        if self.state == "ability" and same:
            self.partner, self.state, self.seen = w & ~ACK, "ack", []
            return lp_cmd(LP_FLP, self.local | ACK)
        if self.state == "ack" and same and w & ACK:
            self.state, self.n = "complete", 0
        if self.state == "complete":
            self.n += 1
            if self.n >= 6:
                self.state, self.result = "done", resolve(self.local, self.partner)
                return lp_cmd(LP_NLP)
        return []


# ---------------- CRC-32 (crc32.s)
CRC_RESIDUE = 0x2144DF1C


def crc_cmd(data):
    return [len(data) & 0xFF, len(data) >> 8] + list(data)


def crc_value(b4):
    return b4[0] | b4[1] << 8 | b4[2] << 16 | b4[3] << 24


# ---------------- MDIO station (mdio_ctrl.s)
def c22_read(phy, reg, pre=True):
    return _hdr(0b01, 0b10, phy, reg, pre)


def c22_write(phy, reg, val, pre=True):
    return _hdr(0b01, 0b01, phy, reg, pre) + [val & 0xFF, val >> 8 & 0xFF]


def c45_addr(prt, dev, addr, pre=True):
    return _hdr(0b00, 0b00, prt, dev, pre) + [addr & 0xFF, addr >> 8 & 0xFF]


def c45_write(prt, dev, val, pre=True):
    return _hdr(0b00, 0b01, prt, dev, pre) + [val & 0xFF, val >> 8 & 0xFF]


def c45_read(prt, dev, inc=False, pre=True):
    return _hdr(0b00, 0b10 if inc else 0b11, prt, dev, pre)


def _hdr(st, op, a, r, pre):
    h = st << 12 | op << 10 | (a & 31) << 5 | (r & 31) | (0 if pre else 0x4000)
    return [h & 0xFF, h >> 8]


def is_read(cmd):
    return bool(cmd[1] & 0x08)


def mdio_parse(items):
    # mdio_ctrl stream -> [(value, phy answered)] for read frames
    out, nophy, k = [], False, 0
    while k < len(items):
        it = items[k]
        if it == ("ev", MDIO_NOPHY):
            nophy = True
            k += 1
            continue
        if k + 1 >= len(items):
            break
        out.append((it | items[k + 1] << 8, not nophy))
        nophy, k = False, k + 2
    return out


# ---------------- MDIO PHY register model (mdio_phy.s)
MDIO_BOX = 422      # docs/spec/programs/cleo_mem.inc
C45_WORD = 32


class PhyRegs:
    # Clause 22 register semantics behind mdio_phy.s: the chip stores every write in the box and pushes it; this
    # model applies read-only masks and self-clearing bits and returns the box words to rewrite (WRMEM). Clause 45:
    # an address frame sets the MMD address; reads answer from box word 32, refreshed here after each address frame
    # and read-increment.
    RO = {1: 0xFFFF, 2: 0xFFFF, 3: 0xFFFF, 5: 0xFFFF, 6: 0xFFFF}

    def __init__(self, oui_id=(0x0022, 0x1556), abilities=A_10HD | A_10FD):
        self.r = [0] * 32
        self.r[0] = 0x1000                          # BMCR: autoneg enable
        self.r[1] = 0x7809 | 0x0020                 # BMSR: 10/100 abilities, AN able, extended, AN complete
        self.r[2], self.r[3] = oui_id
        self.r[4] = code_word(abilities)
        self.mmd = {}                               # (dev, addr) -> value
        self.addr = {}                              # dev -> current address
        self.log = []

    def image(self):
        return list(self.r) + [0]

    def item_bytes(self, b):
        # pushed (type, lo, hi) or (type) -> list of (box index, value) to write back
        t = b[0]
        if not t & 0x80:
            reg, val = t & 31, b[1] | b[2] << 8
            self.log.append(("w22", reg, val))
            old = self.r[reg]
            ro = self.RO.get(reg, 0)
            new = (old & ro) | (val & ~ro)
            if reg == 0 and val & 0x8000:           # reset: self-clearing
                new = 0x1000
            self.r[reg] = new
            return [(reg, new)]
        dev, op = t & 31, t >> 5 & 3
        if op == 0:
            self.addr[dev] = b[1] | b[2] << 8
            self.log.append(("a45", dev, self.addr[dev]))
        elif op == 1:
            self.mmd[(dev, self.addr.get(dev, 0))] = b[1] | b[2] << 8
            self.log.append(("w45", dev, b[1] | b[2] << 8))
        elif op == 2:                               # read-increment: the read used the old address
            self.addr[dev] = (self.addr.get(dev, 0) + 1) & 0xFFFF
        return [(C45_WORD, self.mmd.get((dev, self.addr.get(dev, 0)), 0))]

    @staticmethod
    def item_len(t):
        return 1 if t & 0x80 and t >> 5 & 3 >= 2 else 3
