---
description: Phase 2b (Fable) — full spec of the chosen architecture, ISA, pins, host interface, paper programs, verification plan
---
Phase 2b. Approved candidate: $ARGUMENTS (if empty, use the recommendation in docs/history/ARCH_CANDIDATES.md).
Read: CLAUDE.md, REQUIREMENTS.md, docs/history/ARCH_CANDIDATES.md, DECISIONS.md. Write each file completely before starting the next; commit after each.

1. docs/spec/ARCH.md — block diagram (text), every block's interface (ports, widths, handshake), cycle-level timing of the pipeline/sequencer, reset and reload behaviour, engine arbitration, memory map, area budget table per block (sum ≤70% of tile budget).
2. docs/spec/ISA.md — instruction formats, full encoding table, and for every instruction: syntax, encoding, semantics in pseudo-RTL, cycle count, effects on pins/flags/FIFOs/timers, illegal/undefined cases. Assembler syntax and directives.
   docs/spec/isa.yaml — the same encodings, machine-readable (assembler, ISS and RTL header are generated from it).
3. docs/spec/PINS.md — fixed host pins; per-demo pin maps for UART/SPI/I2C (controller and target); Pmod parts (pull-ups, level shift) needed.
4. docs/spec/HOSTIF.md — host link framing, commands, register map, load/run/stop/status, FIFO access, max bandwidth, error handling.
5. docs/spec/programs/*.s — paper programs: uart_tx, uart_rx, spi_ctrl (modes 0–3), spi_tgt, i2c_ctrl (ACK/NACK, repeated START, stretching), i2c_tgt, and two protocols NOT designed in (e.g. ws2812, manchester or 1-wire). Header line per program: instruction count, cycles/bit, max rate at 50 MHz, engines used.
   If any program needs hacks or exceeds the memory budget, fix the ISA first (log in DECISIONS.md), then continue.
6. docs/spec/VERIF_PLAN.md — levels (ISS pytest, block cocotb, system lockstep RTL-vs-ISS, formal properties list per block, gate-level sim, STA), test list per protocol incl. error cases, done criteria.
Update PROGRESS.md (Next: /phase3-review in a fresh session). Stop with a ≤10-line summary.
