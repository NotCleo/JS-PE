---
description: Phase 4 — isa.yaml generator, assembler, cycle-accurate ISS, protocol checkers; prove the paper programs run
---
Phase 4. Read ISA.md, isa.yaml, ARCH.md (timing sections), HOSTIF.md (load format), docs/spec/programs/.

Build in sw/ (plain Python 3, no deps beyond pyyaml/pytest):
1. gen_isa.py — isa.yaml → sw/isa_def.py and src/jspe_isa.vh (localparams). Generated files carry a one-line "generated, do not edit" header.
2. jspe_asm.py — two-pass, labels, expressions, directives from ISA.md; outputs .hex ($readmemh) and .bin (host load). Clear error messages with line numbers.
3. jspe_iss.py — cycle-accurate per ISA.md: engines, pins (in/out/oe per pin), sync delay, timers, FIFOs, host port. Optional trace of (cycle, engine, pc, pins) to a text file.
4. sw/tests/ — protocol checkers (UART decoder, SPI controller/target model, I2C bus model with open-drain wired-AND, ACK, stretching) and pytest tests running every program in docs/spec/programs/ on the ISS at ≥2 configurations each, including error cases.
Done = `make swtest` all pass, every paper program assembles and passes. Any ISA defect found → fix spec first (DECISIONS.md), regenerate.
Commit per file. Update PROGRESS.md (Next: /phase5-skeleton). Stop.
