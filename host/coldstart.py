# Board cold start (scenario_design.md, D226), MicroPython on the RP2350. Not run on hardware.
# Calls follow tt-micropython-firmware @15ba5ce: src/ttboard/mode.py (RPMode), pins/pins.py
# (begin_asic_manual_inputs, project_clk_driven_by_RP2), demoboard.py (reset_project, clock_project_PWM).
# config.ini must have [DEFAULT] mode = ASIC_MANUAL_INPUTS so boot (main.py) never drives ui_in.
from machine import Pin
from ttboard.demoboard import DemoBoard
from ttboard.mode import RPMode

NAMES = ["ui_in%d" % n for n in range(8)] + ["uio%d" % n for n in range(8)] + ["uo_out%d" % n for n in range(8)]


def cold_start(clk_hz, rp_ui=(3,), rp_uio=(), levels=None):
    # rp_ui / rp_uio: bits the RP2350 drives (board_plan() in host/scenario.py); all others stay inputs, no pull
    levels = levels or {}
    tt = DemoBoard.get()
    if tt.mode != RPMode.ASIC_MANUAL_INPUTS:
        tt.mode = RPMode.ASIC_MANUAL_INPUTS        # ui_in, clk, rst_n inputs
    tt.shuttle.tt_um_notcleo_jspe.enable()
    tt.reset_project(True)
    tt.pins.project_clk_driven_by_RP2(True)
    tt.clock_project_PWM(clk_hz)
    for nm in NAMES:
        p = getattr(tt.pins, nm, None)
        if p is not None:
            p.pull = None                           # firmware default is PULL_DOWN (gpio_map_dbv3 default_pull)
            p.mode = Pin.IN
    tt.uio_oe_pico.value = 0
    tt.reset_project(False)
    for nm in ["ui_in%d" % n for n in rp_ui] + ["uio%d" % n for n in rp_uio]:
        p = getattr(tt.pins, nm)
        if p():                                     # released, no pull: high = something else drives it
            raise OSError("%s reads high before the RP2350 drives it: DIP switch or Pmod contention" % nm)
        p.raw_pin.value(levels.get(nm, 1 if nm == "ui_in3" else 0))
        p.mode = Pin.OUT
    return tt


def release(tt, rp_ui=(3,), rp_uio=()):
    for nm in ["ui_in%d" % n for n in rp_ui] + ["uio%d" % n for n in rp_uio]:
        getattr(tt.pins, nm).mode = Pin.IN
