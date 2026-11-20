---
description: Phase 2a (Fable) — independent architecture candidates
---
Phase 2a. This is the most important thinking in the project. Think deeply; write files as you go so nothing is lost if the session ends.

Read: CLAUDE.md, docs/spec/REQUIREMENTS.md, docs/inputs/checklist.md (skim for the range of protocols).
Do not model the design on RP2040 PIO or TI PRU/Sitara.

1. Write docs/history/ARCH_CANDIDATES.md with 2–3 genuinely different architectures. For each:
   - Execution model: number of engines/threads, instruction granularity (per-bit vs per-word/field), how timing is expressed (delays, wait-on-edge, timers).
   - Bit-plane facilities, if any: configurable shifters, line coding (NRZ/NRZI/Manchester), bit stuffing, CRC/LFSR, parity — configured by instructions, not hardwired per protocol.
   - Pin model: logical→physical mapping, OE/open-drain control, sharing/conflict rules between engines, sync + edge detect.
   - Host interface: pins used, protocol, bandwidth, runtime FIFO access, program load, status/IRQ.
   - Program/data memory: size, SRAM macro vs flops, width.
   - Cell estimate per block with stated assumptions; total vs budget.
   - Critical path and fmax argument at 50 and 60 MHz.
   - Walk-through of stress cases: I2C target with clock stretching + controller arbitration; SPI target at max SCLK (state the max); UART RX 16x oversampling + full duplex; CAN bit stuffing + arbitration + resync; USB LS NRZI + stuffing at 1.5 Mbit/s; 10 Mbit Manchester. State which are feasible.
   - Which operating roles (controller/target/TX/RX/monitor/pattern-gen) are pure software.
   - Top 3 risks.
2. Scoring table: generality (protocols not designed in), area, timing risk, verification effort, originality, write-up story.
3. Recommend one with reasons. Add DECISIONS.md lines. Commit.
4. Update PROGRESS.md (Next: user approves candidate, then /phase2b-isa). Stop and give me a ≤10-line summary of the recommendation and the decisions I must confirm.
