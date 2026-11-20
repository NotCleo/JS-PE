# SPI demo (PINS.md 3.2, 3.3): spi_ctrl.s on E0 drives SCK uio[3], CS uio[0], MOSI uio[1]; spi_tgt.s on E1 answers
# on MISO uio[2] through the same pads, so no wiring is needed. The host reads E1's register table through E0.
# Chip against itself only: the independent SPI models are in test/test_spi.py.
# clk = 48 MHz (PINS.md 2), BIT 12 = 4 MHz SCK, mode 0. On the PC (CLK_HZ must match CLK; the loader checks it):
#   python sw/jspe_asm.py -D CLK_HZ=48000000 -D spi_ctrl.s:BIT=12 -D spi_tgt.s:ENG=1 \
#       docs/spec/programs/spi_ctrl.s docs/spec/programs/spi_tgt.s -o spi
# Modes 1-3: add -D spi_ctrl.s:CPHA=1 / -D spi_ctrl.s:CPOL=1, and -D spi_tgt.s:MODE12=1 for modes 1 and 2.
# Copy spi.bin and host/jspe.py to the board, then run this file.
import time
import machine
from machine import Pin, UART
from ttboard.demoboard import DemoBoard
import jspe

CLK = 48_000_000
TABLE = bytes([0xA5, 0x5A, 1, 2, 3, 4, 5, 6])      # spi_tgt.s table

tt = DemoBoard.get()
tt.shuttle.tt_um_notcleo_jspe.enable()
tt.clock_project_PWM(CLK)
assert machine.freq() % CLK == 0, "clk is not an exact division of the system clock"
tt.reset_project(True)
link = jspe.Link(1, CLK, baudrate=CLK // 16, tx=Pin(20), rx=Pin(37), cts=Pin(38), flow=UART.CTS, rxbuf=8192)
for g in range(25, 29):
    Pin(g, Pin.IN)             # GPIO25-28 = uio[0:3]: the chip drives the bus, the RP2350 only listens
time.sleep_ms(1)
tt.reset_project(False)
time.sleep_ms(1)
link.sync()
link.load("spi.bin")


def read_regs(reg, n):
    # one transfer: count, command byte, n dummy bytes; the byte clocked back during the command is 0x00
    link.send(0, bytes([1 + n, reg] + [0] * n))
    got = b""
    t = time.ticks_ms()
    while len(got) < 1 + n and time.ticks_diff(time.ticks_ms(), t) < 100:
        got += link.recv(0)
    return got


ok = True
for reg, n in ((0, 8), (1, 2), (7, 1)):
    got = read_regs(reg, n)
    want = bytes([0]) + TABLE[reg:reg + n]
    print("reg %d x %d:" % (reg, n), got.hex(), "ok" if got[:len(want)] == want else "MISMATCH")
    ok &= got[:len(want)] == want
print("all ok" if ok else "FAILED", "events", link.events, "notices", link.notes)
