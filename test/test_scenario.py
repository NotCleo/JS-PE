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
    b = await bench(dut)
    await b.load(img)
    r = S.Run(scn, img, b.chip, hl=b.hl, sample=False)
    b.pads = lambda t: r.inputs()

    def step_done():
        r.after()
        return r.finished()
    try:
        await b.run(scn["timeout"], step_done, "scenario %s" % scn["name"])
    except AssertionError as x:
        if "timeout" not in str(x):
            raise
    ok, text = S.report(scn["name"], r.results())
    for ln in text.splitlines():
        cocotb.log.info(ln)
    assert ok, text
