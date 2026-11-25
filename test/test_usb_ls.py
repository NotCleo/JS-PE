# SPDX-FileCopyrightText: © 2026 NotCleo
# SPDX-License-Identifier: Apache-2.0
# USB LS device (usb_ls.s, not designed in, zero RTL diff, D200): one full control transfer, GET_DESCRIPTOR(device)
# at address 0 (SETUP + DATA0, IN data stage NAKed until the RP2350 stand-in arms each 8-byte chunk by WRMEM over
# the host link at 16 clk per bit, OUT status stage). Pads == ISS every clock (test_uart Bench). Cycle-level: the
# image is built for 48 MHz (BIT 32 clk); the testbench clock period does not enter.

import os
import cocotb

from test_uart import Bench                                          # noqa: E402 (sets sys.path)
from test_ws2812 import PROG, nbench                                 # noqa: E402
from jspe_asm import Assembler                                       # noqa: E402
from usb_host import UsbHost, Rp2350, bus_state, DEV_DESC            # noqa: E402

ERX = 1


class UBench(Bench):
    def start_usb(self, img):
        self.host = UsbHost(full=None)
        self.rp = Rp2350(img, self.hl.send, lambda: len(self.hl.q))

    def pads(self, t):
        c = self.chip
        ui = self.hl.level(c) << 3
        if not hasattr(self, "host"):
            return 0x80 | ui, (~c.uio_oe | c.uio_out) & 0xFF
        st, chip = bus_state(c.uio_out, c.uio_oe, self.host.drive(t))
        self.host.tick(t, st, chip)
        self.rp.tick(t, st, self.hl.got)
        return 0x80 | ui, ((~c.uio_oe | c.uio_out) & 0xCF) | st[0] << 4 | st[1] << 5


@cocotb.test()
async def test_u1_control_read(dut):
    b = await nbench(dut, 201, cls=UBench)
    defs = dict(d.split("=") for d in os.environ.get("USB_DEFS", "").split())      # build options, e.g. USB_DEFS="TXJK=1 EOPCHK=0"
    img = Assembler([(PROG + "/usb_ls.s", open(PROG + "/usb_ls.s").read())], defs).run()
    img.clk = None                                     # cycle-level lockstep (see header)
    await b.load(img)
    await b.set_link(0)                                # 16 clk per bit: the USB demo baseline
    b.hl.got, b.pi, b.items = [], 0, []
    b.start_usb(img)
    await b.run(200_000, lambda: b.host.done, "control transfer")
    h, rp = b.host, b.rp
    assert h.result["dev0"] == DEV_DESC and h.naks >= 1 and not h.clash and h.timeouts == 0
    assert rp.notes == [] and set(rp.ops) == {0x70} and len(rp.setups) == 1
    cocotb.log.info("U1: GET_DESCRIPTOR(device) 18 bytes in 3 packets, %d NAKs, %d clk", h.naks, b.now)
