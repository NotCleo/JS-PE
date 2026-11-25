# On-chip bridge demo (docs/spec/programs/bridge_design.md): the host link only loads the image and watches events;
# bytes go from one protocol to the other on the chip. The RP2350 GPIOs play the outside devices. clk = 48 MHz.
# BRIDGE = "i2c_uart": SoftI2C controller on GPIO31/30 (uio[6]/uio[5], Pmod pull-ups) writes to 0x42; a PIO UART
#   receiver on GPIO33 (<- uo_out[0]) checks what the chip sends. On the PC:
#   python sw/jspe_asm.py -D UBIT=48 docs/spec/programs/bridge_i2c_uart.s -o bridge
# BRIDGE = "uart_spi": a PIO UART sender on GPIO24 (-> ui_in[7]) sends a burst; SCK/CS/MOSI on uio[3]/uio[0]/uio[1]
#   go to a logic analyser (MicroPython has no SPI target). The host sees only events: 2 = overrun, 1 = framing.
#   python sw/jspe_asm.py -D UBIT=48 -D BIT=8 docs/spec/programs/bridge_uart_spi.s -o bridge
# Bridge timing is in clk (no CLK record): at 48 MHz UBIT 48 = 1 Mbaud, SPI BIT 8 = 6 MHz. Copy bridge.bin and
# host/jspe.py to the board, then run this file.
import time
import machine
import rp2
from machine import Pin, UART, SoftI2C
from ttboard.demoboard import DemoBoard
import jspe

BRIDGE = "i2c_uart"
CLK = 48_000_000
BAUD = 1_000_000


@rp2.asm_pio(sideset_init=rp2.PIO.OUT_HIGH, out_init=rp2.PIO.OUT_HIGH, out_shiftdir=rp2.PIO.SHIFT_RIGHT)
def pio_tx():                      # as host/examples/uart.py
    pull()
    set(x, 7).side(0)[7]
    label("bit")
    out(pins, 1)[6]
    jmp(x_dec, "bit")
    nop().side(1)[6]


@rp2.asm_pio(in_shiftdir=rp2.PIO.SHIFT_RIGHT)
def pio_rx():                      # as host/examples/uart.py
    label("start")
    wait(0, pin, 0)
    set(x, 7)[10]
    label("bit")
    in_(pins, 1)
    jmp(x_dec, "bit")[6]
    jmp(pin, "good")
    wait(1, pin, 0)
    jmp("start")
    label("good")
    push(block)


tt = DemoBoard.get()
tt.shuttle.tt_um_notcleo_jspe.enable()
tt.clock_project_PWM(CLK)
assert machine.freq() % CLK == 0, "clk is not an exact division of the system clock"
tt.reset_project(True)
link = jspe.Link(1, CLK, baudrate=CLK // 16, tx=Pin(20), rx=Pin(37), cts=Pin(38), flow=UART.CTS, rxbuf=8192)
time.sleep_ms(1)
tt.reset_project(False)
time.sleep_ms(1)
link.sync()
msg = b"bridged on chip, no host in the data path\n"

if BRIDGE == "i2c_uart":
    scl = Pin(31, Pin.OPEN_DRAIN, pull=Pin.PULL_UP, value=1)
    sda = Pin(30, Pin.OPEN_DRAIN, pull=Pin.PULL_UP, value=1)
    prx = rp2.StateMachine(1, pio_rx, freq=8 * BAUD, in_base=Pin(33), jmp_pin=Pin(33))
    link.load("bridge.bin")
    prx.active(1)
    time.sleep_ms(1)
    while prx.rx_fifo():           # TXD was low until its pad was enabled
        prx.get()
    i2c = SoftI2C(scl=scl, sda=sda, freq=100_000, timeout=50_000)
    got = bytearray()
    for k in range(0, len(msg), 4):    # PIO RX FIFO holds 4 bytes: drain it between short writes
        i2c.writeto(0x42, msg[k:k + 4])
        t = time.ticks_ms()
        while len(got) < min(k + 4, len(msg)) and time.ticks_diff(time.ticks_ms(), t) < 20:
            if prx.rx_fifo():
                got.append(prx.get() >> 24)
    print("UART partner received:", bytes(got), "ok" if got == msg else "MISMATCH")
    try:
        i2c.readfrom(0x42, 1)
        print("read ACKed: MISMATCH")
    except OSError:
        print("read NACKed: ok (write-only target)")
else:
    ptx = rp2.StateMachine(0, pio_tx, freq=8 * BAUD, sideset_base=Pin(24), out_base=Pin(24))
    link.load("bridge.bin")
    ptx.active(1)
    for b in msg:
        ptx.put(b)
    time.sleep_ms(5)
    print("bytes sent on UART:", len(msg), "- check SCK/CS/MOSI on the analyser")

print("host link data (must be empty):", link.recv(0), link.recv(1))
print("events", link.events, "notices", link.notes)
