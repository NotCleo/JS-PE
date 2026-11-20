---
description: Phase 3 — adversarial review of the frozen spec in a fresh session
---
Phase 3. You did not write this spec; try to break it.
Read: REQUIREMENTS.md, ARCH.md, ISA.md, isa.yaml, PINS.md, HOSTIF.md, VERIF_PLAN.md, docs/spec/programs/*.s.

Hunt for:
- Ambiguity: anything two engineers would implement differently.
- isa.yaml vs ISA.md mismatches; encoding collisions; unused encoding space wasted.
- Timing infeasibility at 50 MHz; long combinational decode/compare chains.
- Area overrun vs budget; blocks that are protocol-specific in disguise.
- Hazards: engine pin conflicts, FIFO races, host access during run, reload mid-run, deselect/reset.
- Protocols that can't meet standard settings (UART common bauds, SPI modes 0–3, I2C 100/400k).
- Missing error/timeout/abort paths; stuck bus recovery.
- Hand-execute uart_rx, spi_tgt and i2c_tgt for a few bit-times; report any wrong cycle.

Write docs/history/REVIEW.md: findings ranked (severity, location, scenario, proposed fix).
Apply clear-cut fixes to the spec (DECISIONS.md lines). List judgment calls for me at the end. Commit, update PROGRESS.md (Next: /phase4-iss), stop.
