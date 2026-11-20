# WS2812 demo (PINS.md 3.5): ws2812.s on E0 drives uo_out[2] (GPIO35) -> 74AHCT1G125 -> strip DIN (Pmod, PINS.md 4).
# clk = 48 MHz (PINS.md 2): THIRD = 20 clk = 417 ns, so T0H 417, T1H 833, T0L 833, T1L 417 ns (each within 150 ns).
# On the PC (ws2812.s has no CLK_HZ record: its timing is set by THIRD):
#   python sw/jspe_asm.py -D THIRD=20 docs/spec/programs/ws2812.s -o ws2812
# Copy ws2812.bin and host/jspe.py to the board, then run this file. Untested on hardware.
import time
import machine
from machine import Pin, UART
from ttboard.demoboard import DemoBoard
import jspe

CLK = 48_000_000
N = 64                         # LEDs; one frame = N * 30 us, streamed behind H_RDY

tt = DemoBoard.get()
tt.shuttle.tt_um_notcleo_jspe.enable()
tt.clock_project_PWM(CLK)
assert machine.freq() % CLK == 0, "clk is not an exact division of the system clock"
tt.reset_project(True)
link = jspe.Link(1, CLK, baudrate=CLK // 16, tx=Pin(20), rx=Pin(37), cts=Pin(38), flow=UART.CTS, rxbuf=1024)
time.sleep_ms(1)               # H_RX idle high (UART1 owns GPIO20) before reset is released
tt.reset_project(False)
time.sleep_ms(1)
link.sync()
link.load("ws2812.bin")


def wheel(k):
    k &= 255
    if k < 85:
        return 255 - 3 * k, 3 * k, 0
    if k < 170:
        k -= 85
        return 0, 255 - 3 * k, 3 * k
    k -= 170
    return 3 * k, 0, 255 - 3 * k


for f in range(256):
    frame = bytearray()
    for i in range(N):
        r, g, b = wheel(f + i * 256 // N)
        frame += bytes((g >> 3, r >> 3, b >> 3))     # G, R, B, dimmed
    link.send(0, frame)
    time.sleep_ms(N * 30 // 1000 + 2)                 # frame time, then low >= 280 us latches the LEDs
print("events", link.events, "notices", link.notes)
