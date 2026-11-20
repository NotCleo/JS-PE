# UART demo (PINS.md 3.1): uart_tx.s on E0 drives uo_out[0], uart_rx.s on E1 reads ui_in[7]; 1 Mbaud 8N1.
# clk = 48 MHz: the TT firmware sets sys = 96 MHz; clk_peri stays 48 MHz, so link 3 Mbaud = B 16 exactly (PINS.md 2)
# On the PC (CLK_HZ must match CLK; the loader checks the image's CLK record):
#   python sw/jspe_asm.py -D CLK_HZ=48000000 docs/spec/programs/uart_tx.s docs/spec/programs/uart_rx.s -o uart
# Copy uart.bin and host/jspe.py to the board, then run this file.
# Partner: GPIO24 (-> ui_in[7]) is UART1 TX, taken by the host link, so the partner is a PIO UART on GPIO24 and
# GPIO33 (<- uo_out[0]) (pico-sdk io_bank0.h; PINS.md 3.1).
import time
import machine
import rp2
from machine import Pin, UART
from ttboard.demoboard import DemoBoard
import jspe

BAUD = 1_000_000
CLK = 48_000_000


@rp2.asm_pio(sideset_init=rp2.PIO.OUT_HIGH, out_init=rp2.PIO.OUT_HIGH, out_shiftdir=rp2.PIO.SHIFT_RIGHT)
def pio_tx():
    pull()
    set(x, 7).side(0)[7]
    label("bit")
    out(pins, 1)[6]
    jmp(x_dec, "bit")
    nop().side(1)[6]


@rp2.asm_pio(in_shiftdir=rp2.PIO.SHIFT_RIGHT)
def pio_rx():
    label("start")
    wait(0, pin, 0)
    set(x, 7)[10]
    label("bit")
    in_(pins, 1)
    jmp(x_dec, "bit")[6]
    jmp(pin, "good")
    wait(1, pin, 0)            # framing error: drop the byte, wait for idle
    jmp("start")
    label("good")
    push(block)


tt = DemoBoard.get()
tt.shuttle.tt_um_notcleo_jspe.enable()
tt.clock_project_PWM(CLK)
assert machine.freq() % CLK == 0, "clk is not an exact division of the system clock"
tt.reset_project(True)
link = jspe.Link(1, CLK, baudrate=CLK // 16, tx=Pin(20), rx=Pin(37), cts=Pin(38), flow=UART.CTS, rxbuf=8192)
time.sleep_ms(1)               # H_RX idle high (UART1 owns GPIO20) before reset is released
tt.reset_project(False)
time.sleep_ms(1)
link.sync()
ptx = rp2.StateMachine(0, pio_tx, freq=8 * BAUD, sideset_base=Pin(24), out_base=Pin(24))
prx = rp2.StateMachine(1, pio_rx, freq=8 * BAUD, in_base=Pin(33), jmp_pin=Pin(33))
link.load("uart.bin")
ptx.active(1)
prx.active(1)
time.sleep_ms(1)
while prx.rx_fifo():           # TXD was low until its pad was enabled: flush the partner receiver
    prx.get()

msg = b"hello from the host link\x1b\n"
link.send(0, msg)              # host -> E0 -> uo_out[0] -> partner
got = bytearray()
t = time.ticks_ms()
while len(got) < len(msg) and time.ticks_diff(time.ticks_ms(), t) < 100:
    if prx.rx_fifo():
        got.append(prx.get() >> 24)
print("partner received:", bytes(got), "ok" if got == msg else "MISMATCH")

reply = b"hello from the partner\n"
for b in reply:                # partner -> ui_in[7] -> E1 -> host link stream 1
    ptx.put(b)
time.sleep_ms(5)
back = link.recv(1)
print("host received:", back, "ok" if back == reply else "MISMATCH")
print("events", link.events, "notices", link.notes)
