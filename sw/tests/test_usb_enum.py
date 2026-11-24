# usb_ls.s enumeration on the ISS: USB host model + RP2350 stand-in over the real host link (B = 16, WRMEM only).
# Time is scaled (bus reset 1 500 clk, report interval 200 bits); protocol order and rules are the real ones.
import pytest
from harness import prog
from jspe_iss import Chip, HostLink
from usb_host import UsbHost, Rp2350, bus_state, DEV_DESC, CFG_DESC, REPORT_DESC, REPORTS, req


def enumerate_iss(own=1, full=True, corrupt_req=9, max_clk=3_000_000):
    img = prog("usb_ls", OWNCHK=own)
    c = Chip(direct=False)
    c.boot(img, phase=0)
    hl = HostLink(16)
    for _ in range(800):                       # boot() held H_RX low (a break): let its notice pass, then drop it
        c.step(0x08, 0x20)
        hl.sample(c)
    hl.got = []
    rp = Rp2350(img, hl.send, lambda: len(hl.q))
    host = UsbHost(corrupt_setup=lambda p: p[1] == corrupt_req, full=full)
    while not host.done and c.cycle < max_clk:
        t = c.cycle
        st, chip = bus_state(c.uio_out, c.uio_oe, host.drive(t))
        host.tick(t, st, chip)
        rp.tick(t, st, hl.got)
        c.step(hl.level(c) << 3, st[0] << 4 | st[1] << 5)
        hl.sample(c)
    assert host.done, "host script did not finish by cycle %d" % c.cycle
    return c, host, rp


def common(host, rp, corrupt_req):
    assert rp.resets == 1 and rp.addr == 7
    assert host.naks >= 1                                  # NAK until the RP2350 armed the buffer
    assert rp.crc_events == 1 and host.timeouts == 1       # corrupted DATA of one SETUP, host retry
    assert sum(1 for s in rp.setups if s[1] == corrupt_req) == 1
    assert host.dups == 0 and not host.clash and rp.notes == [] and set(rp.ops) == {0x70}


@pytest.mark.parametrize("own", [1, 0])
def test_usb_enum_address(own):
    # bus reset, GET_DESCRIPTOR(device) at 0, SET_ADDRESS (corrupted once), GET_DESCRIPTOR(device) at 7
    c, host, rp = enumerate_iss(own, full=False, corrupt_req=5)
    assert host.result["dev0"] == DEV_DESC and host.result["dev"] == DEV_DESC
    common(host, rp, 5)


@pytest.mark.slow
@pytest.mark.parametrize("own", [1, 0])
def test_usb_enum_keyboard(own):
    # + configuration and HID report descriptors, SET_CONFIGURATION (corrupted once), 10 key reports
    c, host, rp = enumerate_iss(own)
    r = host.result
    assert r["dev0"] == DEV_DESC and r["dev"] == DEV_DESC and r["cfg9"] == CFG_DESC[:9] and r["cfg"] == CFG_DESC
    assert r["report"] == REPORT_DESC and r["report2"] == REPORT_DESC and r["reports"] == REPORTS and rp.configured
    common(host, rp, 9)
