---
description: Phase 1 — read task, template, checklist; write REQUIREMENTS.md
---
Phase 1. Reading and distilling only — do NOT design anything.

Step A — read in this order:
1. docs/inputs/task.md (the competition brief).
2. The template: info.yaml, src/project.v, src/config.json, test/Makefile, test/test.py, .github/workflows/gds.yaml (skim). Note what the flow requires.
3. docs/inputs/checklist.md (my protocol/feature exploration list).

Step B — write docs/spec/REQUIREMENTS.md (≤1500 words), sections:
- Hard constraints: process, tiles/cell budget, pins with electrical capability per group, clock, host, reset/deselect behaviour, flow requirements (precheck, GL sim).
- Deliverables required by the brief.
- Judging criteria → what evidence we must produce for each.
- v1 MUST: UART (baud range, 5–9 bit, parity, stop bits), SPI controller modes 0–3 + SPI target, I2C controller 100/400 kHz with ACK/NACK, repeated START, clock stretching; I2C target. Concrete numbers.
- SHOULD: concurrency (≥2 engines), monitor mode, error injection, one protocol not designed in.
- STRETCH (later): from the checklist, grouped by what HW primitive each needs.
- Out of scope v1.
- Operating roles (controller/target/TX/RX/monitor/pattern-gen): which primitive each needs.
- Open questions for me.
Commit.

Commit, update PROGRESS.md (Next: /phase2a-arch), stop.
