#!/usr/bin/env python3
# Terse summary of a cocotb run. usage: test_summary.py <results.xml | dir of *.xml> <log> <t_start_epoch>
import glob
import os
import sys
import xml.etree.ElementTree as ET

res, log = sys.argv[1], sys.argv[2]
t0 = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0


def log_errors(n=8):
    if not os.path.isfile(log):
        return ["(no log)"]
    keys = ("error", "syntax", "unable", "not found", "undefined", "traceback", "exception")
    out = [l.rstrip() for l in open(log, errors="replace") if any(k in l.lower() for k in keys)]
    return out[:n] or [l.rstrip() for l in open(log, errors="replace").readlines()[-n:]]


files = sorted(glob.glob(f"{res}/*.xml")) if os.path.isdir(res) else [res]
if not files or any(not os.path.exists(f) or os.path.getmtime(f) < t0 for f in files):
    print("BUILD/ELAB FAILED (no fresh results). log:", log)
    print("\n".join(log_errors()))
    sys.exit(1)

cases = [c for f in files for c in ET.parse(f).getroot().iter("testcase")
         if not os.path.isdir(res) or f'{c.get("classname")}.{c.get("name")}' == os.path.basename(f)[:-4]]  # dir: <module>.<test>.xml
fails, skips = [], 0
for c in cases:
    if c.find("skipped") is not None:
        skips += 1
        continue
    f = c.find("failure")
    if f is None:
        f = c.find("error")
    if f is not None:
        msg = (f.get("message") or f.get("error_msg") or (f.text or "")).strip().replace("\n", " ")
        fails.append(f"  FAIL {c.get('name')} @ {c.get('sim_time_ns', '?')}ns: {msg[:220]}")
passes = len(cases) - len(fails) - skips
status = "FAIL" if fails else ("PASS" if passes else "NONE RUN")
print(f"{status}: PASS {passes}, FAIL {len(fails)}, SKIP {skips} of {len(cases)}  (log: {log})")
print("\n".join(fails[:5]))
sys.exit(0 if status == "PASS" else 1)
