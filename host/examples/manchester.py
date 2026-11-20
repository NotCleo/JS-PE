# Manchester demo (PINS.md 3.6): manchester.s TX on E0 drives uo_out[3] (GPIO36), RX on E1 reads ui_in[4] (GPIO21).
# Loop-back: a PIO relay copies GPIO36 to GPIO21 (RELAY = False: jumper uo_out[3] -> ui_in[4] instead).
# clk = 48 MHz (PINS.md 2): BIT = 48 clk = 1 Mbit/s; code-violation check on (CVCHK = 1, D099). On the PC:
#   python sw/jspe_asm.py -D BIT=48 docs/spec/programs/manchester.s -o manchester
# Copy manchester.bin and host/jspe.py to the board, then run this file. Untested on hardware.
import time
import machine
import rp2
from machine import Pin, UART
from ttboard.demoboard import DemoBoard
import jspe

CLK = 48_000_000
RELAY = True
SFD = 0xD5


@rp2.asm_pio(out_init=rp2.PIO.OUT_LOW)
def relay():
    wrap_target()
    mov(pins, pins)            # in_base -> out_base, one bit, every PIO clock
    wrap()


tt = DemoBoard.get()
tt.shuttle.tt_um_notcleo_jspe.enable()
tt.clock_project_PWM(CLK)
assert machine.freq() % CLK == 0, "clk is not an exact division of the system clock"
tt.reset_project(True)
link = jspe.Link(1, CLK, baudrate=CLK // 16, tx=Pin(20), rx=Pin(37), cts=Pin(38), flow=UART.CTS, rxbuf=4096)
time.sleep_ms(1)               # H_RX idle high (UART1 owns GPIO20) before reset is released
tt.reset_project(False)
time.sleep_ms(1)
link.sync()
if RELAY:
    sm = rp2.StateMachine(0, relay, freq=machine.freq(), in_base=Pin(36), out_base=Pin(21))
    sm.active(1)
link.load("manchester.bin")
time.sleep_ms(1)

for n in (1, 16, 200):
    payload = bytes((i * 37 + n) & 255 for i in range(n))
    link.events.clear()
    link.rx.pop(1, None)
    link.send(0, b"\x55" * 7 + bytes((SFD,)) + payload)     # 802.3 preamble + SFD + payload
    time.sleep_ms((n + 8) * 8 // 1000 + 5)
    got = link.recv(1)
    ends = [e for e in link.events if e[0] == 1 and e[1] == 4]
    print(n, "bytes:", "ok" if got == payload else "MISMATCH", "end-of-frame events", len(ends))
print("notices", link.notes)
