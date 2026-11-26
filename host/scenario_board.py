# Scenario runner, board backend (scenario_design.md), MicroPython on the RP2350. NOT RUN ON HARDWARE.
# PC: python host/scenario.py <file> --backend board --out build -> <name>.json + <name>.bin; copy both and host/
# (jspe.py, coldstart.py, monitor.py, measure.py, this file) to the board, then scenario_board.run("<name>.json").
# Supports send / fault stimuli, square partners (PWM), stream / events / fifo / decode expects; other partners are
# printed as operator steps, pad / SPI / partner-model expects are reported SKIP (need a logic analyser).
import json
import time
from machine import Pin, UART, PWM
import jspe
import coldstart

LINK_KW = dict(baudrate=0, tx=Pin(20), rx=Pin(37), cts=Pin(38), flow=UART.CTS, rxbuf=8192)   # host/examples


def entries(link, e):
    import monitor
    return monitor.entries(link, e)


def judge(x, link):
    if "skip" in x:
        return None, "SKIP " + x["skip"]
    if "decode" in x:
        a = x["decode"]
        mod, fn = a["fn"].split(".")
        dec = getattr(__import__(mod), fn)(entries(link, a["e"]), **(a.get("args") or {}))[a.get("skip", 0):]
        vals = [d[a["field"]] if "field" in a else d for d in dec]
        want, tol = x["data"], x.get("tol", 0)
        ok = len(vals) >= len(want) and all(v == w or (tol and v is not None and abs(v - w) <= tol)
                                            for v, w in zip(vals, want))
        return ok, "decode %s E%d: %s want %s" % (a["fn"], a["e"], vals[:len(want) + 2], want)
    key = "stream" if "stream" in x else "events" if "events" in x else "fifo"
    e = x[key]["e"]
    ent = entries(link, e)
    if key == "stream":
        vals = [v for v in ent if not isinstance(v, tuple)]
    elif key == "events":
        vals = [v[1] for v in ent if isinstance(v, tuple)]
    else:
        vals = [0x100 | v[1] if isinstance(v, tuple) else v for v in ent]
    for i, m in (x.get("mask") or {}).items():
        if int(i) < len(vals):
            vals[int(i)] &= m
    want = x["data"]
    ok = vals[:len(want)] == want and (x.get("prefix") or len(vals) == len(want))
    return ok, "%s E%d: %d entries, want %d" % (key, e, len(vals), len(want))


def run(plan_path, bin_path=None):
    p = json.load(open(plan_path))
    bin_path = bin_path or plan_path[:-5] + ".bin"
    for n in p.get("notes", []):
        print("NOTE", n)
    tt = coldstart.cold_start(int(p["clk_hz"]), p["rp_ui"], p["rp_uio"], p.get("levels"))
    kw = dict(LINK_KW)
    kw["baudrate"] = int(p["clk_hz"]) // 16
    link = jspe.Link(1, int(p["clk_hz"]), **kw)
    link.sync()
    link.load(bin_path)                              # ends with RUN
    t0 = time.ticks_us()
    pwms = []
    for st in sorted(p["steps"], key=lambda s: s["t_us"]):
        while time.ticks_diff(time.ticks_us(), t0) < st["t_us"]:
            link.poll()
        if "send" in st:
            link.send(st["send"][0], bytes(st["send"][1]))
        elif "fault" in st:
            link.wrmem(st["fault"][0], [st["fault"][1]])
        elif "pwm" in st:
            n, hz, duty = st["pwm"]
            w = PWM(getattr(tt.pins, "ui_in%d" % n).raw_pin, freq=int(hz), duty_u16=int(duty * 65535))
            pwms.append(w)
        elif "operator" in st:
            print("OPERATOR", st["operator"])
    while time.ticks_diff(time.ticks_us(), t0) < p["timeout_us"]:
        link.poll()
    for w in pwms:
        w.deinit()
    link.op_noflow(0x2F)                             # STOP all engines (as the loader's RST record)
    coldstart.release(tt, p["rp_ui"], p["rp_uio"])
    verdict = True
    for x in p["expect"]:
        ok, d = judge(x, link)
        print(("SKIP" if ok is None else "PASS" if ok else "FAIL"), d)
        verdict = verdict and ok is not False
    if link.notes:
        print("FAIL notices", [hex(v) for v in link.notes])
        verdict = False
    print(("PASS" if verdict else "FAIL"), p["name"])
    return verdict
