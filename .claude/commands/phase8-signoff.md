---
description: Phase 8 — formal properties, gate-level sim, timing closure
argument-hint: formal | gls | timing
---
Sign-off task: $ARGUMENTS
- formal: formal/<block>.sby + properties for each block in VERIF_PLAN's formal list (FIFO invariants, host-link protocol, decoder legality, OE safety, no-X after reset, engine pin-ownership exclusivity). Use prove mode where possible; add cover statements for reachability. Report: property → result → depth.
- gls: run the template's gate-level test (GATES=yes) on the netlist from the GDS action; triage mismatches vs RTL.
- timing: from the GDS action reports I paste, list failing paths, propose minimal RTL fixes (register insertion, retiming), apply after my OK.
Keep output terse. Commit. Update PROGRESS.md.
