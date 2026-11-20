---
description: Phase 7 — system-level bring-up of one protocol (RTL vs ISS lockstep + cocotb bus models)
argument-hint: uart | spi | i2c | <other>
---
Protocol bring-up: $ARGUMENTS
Read: VERIF_PLAN.md (this protocol), PINS.md, HOSTIF.md, docs/spec/programs/ for this protocol, test/test.py.

1. System tests in test/test_$ARGUMENTS.py (add to COCOTB_TEST_MODULES in test/Makefile): host loads the assembled program via the host-link model, then the DUT talks to a cocotb bus model (reuse sw/tests checkers where possible). Cover every role/config in VERIF_PLAN incl. error injection and timeouts.
2. Lockstep: run the same program on jspe_iss.py and compare per-cycle pin values; report the first divergence (cycle, pin, expected, got).
3. Fix RTL bugs. If the ISS is wrong, fix sw/ and say why. If the spec is wrong, stop and report.
4. Run the `reviewer` subagent on the touched RTL + programs; fix findings.
5. Add host/examples/$ARGUMENTS.py (MicroPython demo), minimal.
Done = all tests pass, lockstep clean, reviewer findings resolved, committed, PROGRESS.md updated.
