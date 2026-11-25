# I2C demo (PINS.md 3.4): i2c_tgt.s on E0 is a target at 0x42 on SCL uio[6] (GPIO31), SDA uio[5] (GPIO30); the
# RP2350 is the controller (SoftI2C, which waits while the chip stretches SCL). Pull-ups: the Pmod's 2 x 4.7 kOhm, or
# the RP2350 internal ones at 100 kHz (functional only, D005). Chip against the RP2350 only: the independent I2C
# models are in test/test_i2c.py. On the PC (add -D ORG=80 --mem 128 for the flop fallback):
#   python sw/jspe_asm.py docs/spec/programs/i2c_tgt.s -o i2c
# Copy i2c.bin and host/jspe.py to the board, then run this file.
import time
import machine
from machine import Pin, UART, SoftI2C
from ttboard.demoboard import DemoBoard
import jspe

CLK = 48_000_000
ADDR = 0x42

tt = DemoBoard.get()
tt.shuttle.tt_um_notcleo_jspe.enable()
tt.clock_project_PWM(CLK)
assert machine.freq() % CLK == 0, "clk is not an exact division of the system clock"
tt.reset_project(True)
link = jspe.Link(1, CLK, baudrate=CLK // 16, tx=Pin(20), rx=Pin(37), cts=Pin(38), flow=UART.CTS, rxbuf=8192)
scl = Pin(31, Pin.OPEN_DRAIN, pull=Pin.PULL_UP, value=1)
sda = Pin(30, Pin.OPEN_DRAIN, pull=Pin.PULL_UP, value=1)
time.sleep_ms(1)
tt.reset_project(False)
time.sleep_ms(1)
link.sync()
link.load("i2c.bin")
i2c = SoftI2C(scl=scl, sda=sda, freq=100_000, timeout=50_000)


def host_bytes(n, ms=100):
    got, t = b"", time.ticks_ms()
    while len(got) < n and time.ticks_diff(time.ticks_ms(), t) < ms:
        got += link.recv(0)
    return got


ok = True
found = i2c.scan()
print("scan:", [hex(a) for a in found])
ok &= ADDR in found
host_bytes(64, 20)                                  # drop the address bytes of the scan
data = bytes([0x11, 0x22, 0x1B, 0xFE])
i2c.writeto(ADDR, data)
got = host_bytes(1 + len(data))
print("write:", got.hex(), "ok" if got == bytes([ADDR << 1]) + data else "MISMATCH")
ok &= got == bytes([ADDR << 1]) + data
reply = bytes([0xA5, 0x5A, 0x3C])
link.send(0, reply)                                 # bytes to send are queued before the read starts
rd = i2c.readfrom(ADDR, len(reply))
got = host_bytes(1)
print("read:", rd.hex(), "ok" if rd == reply and got == bytes([ADDR << 1 | 1]) else "MISMATCH")
ok &= rd == reply and got == bytes([ADDR << 1 | 1])
print("all ok" if ok else "FAILED", "events", link.events, "notices", link.notes)
