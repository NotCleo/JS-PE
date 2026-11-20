---
description: Phase 6 — implement one RTL block test-first against the spec
argument-hint: <module name, e.g. jspe_shifter>
---
Implement block: $ARGUMENTS
Read: the ARCH.md section for this block, the relevant ISA.md parts, src/jspe_isa.vh, VERIF_PLAN.md entries for it, PROGRESS.md area ledger. Read existing neighbour modules' port lists only.

1. Plan (≤15 lines): ports, internal state, corner cases from VERIF_PLAN, est. cells. Wait for my OK if I'm in plan mode.
2. Tests first: test/unit/$ARGUMENTS/test_$ARGUMENTS.py (cocotb 2.x). Cover normal ops, every corner case listed, reset mid-operation, back-to-back. Use a reference model from sw/ where one exists.
3. RTL src/$ARGUMENTS.v per CLAUDE.md rules. Add to info.yaml + test/Makefile.
4. `make lint` clean → test-runner `make unit B=$ARGUMENTS` → `make area B=$ARGUMENTS`.
5. If over budget: say so with the cause; don't silently redesign.
Done = tests pass, lint clean, area recorded in PROGRESS.md ledger, committed. Don't touch the spec; if it's wrong, stop and report.
