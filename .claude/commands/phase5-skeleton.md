---
description: Phase 5 — walking skeleton through the TT GDS flow (top + host link + memory + trivial engine)
---
Phase 5. Goal: flush out flow/macro/IO problems early, not features.
1. info.yaml: title, author, description, language Verilog, clock_hz, tiles, top_module, source_files, pinout names from PINS.md.
2. src/: top module with pin sync, host link per HOSTIF.md (load + readback only), program memory (SRAM macro if ARCH.md says so — find how the TT IHP flow expects macros to be instantiated/configured and record it in DECISIONS.md; provide a behavioural model for simulation), and a stub engine that copies one input pin to one output pin.
3. test/test.py: load a program over the host link model, read it back, check the stub path.
4. `make lint`, `make test`, `make area` locally. Then tell me to push so the GitHub GDS action runs; I'll paste back the summary (cells, utilisation, worst slack, precheck result).
Done = local tests pass and I confirm GDS + precheck green. Record numbers in PROGRESS.md. Commit. Stop.
