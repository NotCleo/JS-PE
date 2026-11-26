# SPDX-FileCopyrightText: © 2026 NotCleo
# SPDX-License-Identifier: Apache-2.0
# Scenario runner, RTL backend (docs/spec/scenario_design.md): SCENARIO (default scenarios/uart_echo.yaml) is
# loaded over the real host link and run with the ISS in lockstep (pads == ISS every clock, Bench.run); stimulus,
# partners and the PASS/FAIL report come from host/scenario.py, as on the ISS backend.

import os
import sys

import cocotb

from test_uart import ROOT, bench                                   # noqa: E402 (sets sys.path)

sys.path.insert(0, os.path.join(ROOT, "host"))
import scenario as S                                                # noqa: E402

SCN = os.environ.get("SCENARIO") or os.path.join(ROOT, "scenarios", "uart_echo.yaml")


@cocotb.test()
async def test_scenario_rtl(dut):
    scn = S.load(SCN)
    img, _ = S.check(scn)
    assert not scn.get("backends") or "rtl" in scn["backends"], "rtl not in backends %s" % scn["backends"]
    b = await bench(dut)
    await b.load(img)                               # ends with RUN: t0 = now (direct ISS: RUN; link time differs)
    b.parse()
    r = S.Run(scn, img, b.chip, hl=b.hl, sample=False)
    r.pi, r.cur = b.pi, b.cur                       # a stream announced during the load stays selected
    b.pads = lambda t: r.inputs()

    def step_done():
        r.after()
        return r.finished()
    try:
        await b.run(scn["timeout"], step_done, "scenario %s" % scn["name"])
    except AssertionError as x:
        if "timeout" not in str(x):
            raise
        r.timed_out = True
    ok, text = S.report(scn["name"], r.results())
    for ln in text.splitlines():
        cocotb.log.info(ln)
    assert ok, text
