#!/usr/bin/env python3
# Run every cocotb test of test/test*.py (or one module) as its own GL sim, J in parallel.
# usage: gl_all.py <make args...> -- <res dir> <log dir> <jobs> <timeout s> [module]
import glob
import os
import re
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

i = sys.argv.index("--")
make_args, (res, logd, jobs, tmo, *mod) = sys.argv[1:i], sys.argv[i + 1:]
pat = re.compile(r"@cocotb\.test\([^)]*\)\s*\n\s*async def (\w+)")
tests = []
for f in sorted(glob.glob(f"test/{mod[0] if mod else 'test*'}.py")):
    m = os.path.basename(f)[:-3]
    tests += [(m, t) for t in pat.findall(open(f).read())]
if not tests:
    sys.exit("no tests found")


def run(mt):
    m, t = mt
    env = dict(os.environ, PWD=os.path.abspath("test"), COCOTB_TEST_FILTER=rf"^{m}\.{t}$",  # test/Makefile pins the module list
               COCOTB_RESULTS_FILE=os.path.abspath(f"{res}/{m}.{t}.xml"))
    t0 = time.time()
    with open(f"{logd}/{m}.{t}.log", "w") as lg:
        p = subprocess.Popen(["make", "-s", "-C", "test", *make_args], env=env, stdout=lg,
                             stderr=subprocess.STDOUT, start_new_session=True)
        try:
            p.wait(timeout=int(tmo))
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            lg.flush()
            lg.write(f"\nERROR: GL timeout after {tmo} s\n")
    x = f"{res}/{m}.{t}.xml"
    if not os.path.exists(x) or os.path.getmtime(x) < t0:
        open(x, "w").write(f'<testsuites><testsuite><testcase name="{t}" classname="{m}"><failure '
                           f'error_msg="no results (timeout {tmo} s or crash), see {logd}/{m}.{t}.log"/>'
                           '</testcase></testsuite></testsuites>')
    return t, time.time() - t0


t0 = time.time()
with ThreadPoolExecutor(int(jobs)) as ex:
    times = sorted(ex.map(run, tests), key=lambda x: -x[1])
print(f"GL {len(tests)} tests, J={jobs}: wall {time.time() - t0:.0f} s, longest "
      + ", ".join(f"{t} {s:.0f} s" for t, s in times[:3]))
