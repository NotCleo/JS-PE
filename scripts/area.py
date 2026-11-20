#!/usr/bin/env python3
# Per-module cell count via yosys. usage: area.py <top> [liberty.lib]
# Without liberty: generic yosys cells (a proxy). With liberty: mapped cells + area (um^2).
import glob
import json
import os
import shutil
import subprocess
import sys

top = sys.argv[1]
lib = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else os.environ.get("LIB", "")
ys = shutil.which("yosys") or shutil.which("yowasp-yosys")
if not ys:
    sys.exit("yosys not found")
srcs = " ".join(sorted(glob.glob("src/*.v")))
os.makedirs("build", exist_ok=True)
js = "build/area.json"
flow = f"read_verilog -sv -Isrc {srcs}; synth -top {top}; "
if lib:
    flow += f"dfflibmap -liberty {lib}; abc -liberty {lib}; opt_clean; tee -q -o {js} stat -json -liberty {lib}"
else:
    flow += f"tee -q -o {js} stat -json"
r = subprocess.run([ys, "-q", "-p", flow], capture_output=True, text=True)
if r.returncode:
    print("\n".join((r.stderr or r.stdout).splitlines()[-8:]))
    sys.exit(1)
d = json.load(open(js))
rows = []
for name, m in d["modules"].items():
    rows.append((name.lstrip("\\"), int(m.get("num_cells", 0)), m.get("area")))
rows.sort(key=lambda x: -x[1])
for n, c, a in rows:
    print(f"{n:32s} {c:7d}" + (f"  {float(a):10.1f} um2" if a else ""))
tot = d.get("design", {})
print(f"{'TOTAL(flattened design)':32s} {int(tot.get('num_cells', 0)):7d}" + (f"  {float(tot['area']):10.1f} um2" if tot.get("area") else ""))
