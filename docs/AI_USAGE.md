# AI usage

How Claude was used to build this chip, what it cost in tokens, and what I decided or checked myself. Raw material: the session logs of Claude Code on my machine (summarised by [scripts/ai_usage.py](https://github.com/NotCleo/JS-PE/blob/main/scripts/ai_usage.py)), the prompt list by phase ([docs/ai/PROMPT-LIST/prompts.md](https://github.com/NotCleo/JS-PE/blob/main/docs/ai/PROMPT-LIST/prompts.md)), the phase procedures ([.claude/commands/](https://github.com/NotCleo/JS-PE/tree/main/.claude/commands)), the subagent definitions ([.claude/agents/](https://github.com/NotCleo/JS-PE/tree/main/.claude/agents)), [CLAUDE.md](https://github.com/NotCleo/JS-PE/blob/main/CLAUDE.md) and [docs/DECISIONS.md](https://github.com/NotCleo/JS-PE/blob/main/docs/DECISIONS.md).

## 1. Method

| Element | What it is |
|---|---|
| Before the repo | The Claude Code kit (CLAUDE.md, agents, phase commands, scripts) and the overall phase plan were drafted with Claude in a claude.ai chat before the repo existed. No logs of that chat are in the repo; its token use is not recorded (claude.ai chat, no usage export) and not counted below. |
| Phases as commands | Each phase is a slash command whose procedure lives in `.claude/commands/<phase>.md` (`/phase1-distill` … `/phase8-signoff`, `/rtl-block`, `/protocol`, `/writeup`, `/resume`, `/checkpoint`). My prompt for a stage is usually the command plus a few lines of scope or decisions. |
| Files as memory | Sessions were short and restarted often (usage limits, `/clear`). State lived in files, not in the conversation: [PROGRESS.md](https://github.com/NotCleo/JS-PE/blob/main/PROGRESS.md) (handoff + area ledger), DECISIONS.md (one line per choice, 123 so far), the spec in [docs/spec/](https://github.com/NotCleo/JS-PE/tree/main/docs/spec), and a commit after every subtask (204 commits). |
| Spec before code | Spec → ISS → RTL. Ambiguities were stopped and brought to me in ≤ 10 lines with a proposed fix (CLAUDE.md rule); my answer became a DECISIONS line. |
| Subagents | `test-runner` (Haiku) ran make targets and returned pass/fail only; `reviewer` (Fable, Opus) did fresh-eyes reviews of every block and protocol; `web-fetch` (Haiku) read external pages. |
| Guard rails | Area freeze from Phase 7 (RTL bug fixes only, D083), the `rtl-freeze-v1` tag, "never reason through a large design in one response" (added after the CAN design, commit 871bd71), no full GDS artifact downloads. |
| Models | Claude Fable 5.2 for architecture, spec and the spec review (max effort); Claude Opus 5.5 for implementation, verification and this write-up; Haiku 5.5 and Sonnet 5.5 for subagents. Commit trailers: 161 commits co-authored by Opus 5.5, 39 by Fable 5.2. |

## 2. Stages

Tokens are approximate (section 3). "Out" = output tokens including thinking; "read" = cache-read input tokens. Prompts: the phase section of [docs/ai/PROMPT-LIST/prompts.md](https://github.com/NotCleo/JS-PE/blob/main/docs/ai/PROMPT-LIST/prompts.md), compiled from the planning chat, with model, effort and mode per prompt.

| Stage | Model, effort, mode | Prompt (short form) | Tokens | Outcome | My decisions |
|---|---|---|---|---|---|
| 0 kit + plan | Claude in a claude.ai chat | — | not recorded (claude.ai chat, no usage export) | CLAUDE.md, agents, commands, scripts (commit 2085b89) | phase plan, constraints, rules |
| 1 distill + setup | Opus 5.5, high, acceptEdits | `/phase1-distill`, then my answers to REQUIREMENTS §9 ([prompts](https://github.com/NotCleo/JS-PE/blob/main/docs/ai/PROMPT-LIST/prompts.md#phase-1-requirements), [procedure](https://github.com/NotCleo/JS-PE/blob/main/.claude/commands/phase1-distill.md)) | out 63k, read 2.3M | REQUIREMENTS.md; 5 commits | D001–D008: host pins, bandwidth, memory, UART MUST, pull-ups, not-designed-in demos, 50 MHz, RP2350 as partner |
| 2a architecture | Fable 5.2, max | `/phase2a-arch` ([prompts](https://github.com/NotCleo/JS-PE/blob/main/docs/ai/PROMPT-LIST/prompts.md#phase-2a-architecture), [procedure](https://github.com/NotCleo/JS-PE/blob/main/.claude/commands/phase2a-arch.md)) | out 169k, read 3.8M | 3 candidates scored, Cleo recommended; 3 commits | chose Cleo with changes: TDM fetch, latched events, shift-assist and solo-mode evaluations (D017–D021) |
| 2b spec | Fable 5.2, max, acceptEdits | `/phase2b-isa B`, resume prompts ([prompts](https://github.com/NotCleo/JS-PE/blob/main/docs/ai/PROMPT-LIST/prompts.md#phase-2b-full-spec-and-isa), [procedure](https://github.com/NotCleo/JS-PE/blob/main/.claude/commands/phase2b-isa.md)) | out 833k, read 19.9M | ARCH, ISA + isa.yaml, PINS, HOSTIF, 9 paper programs, VERIF_PLAN; 13 commits | UART timing criteria (D040) |
| 3 spec review | Fable 5.2, max, acceptEdits | phase 3 procedure + 5 specific checks ([prompts](https://github.com/NotCleo/JS-PE/blob/main/docs/ai/PROMPT-LIST/prompts.md#phase-3-adversarial-spec-review), [procedure](https://github.com/NotCleo/JS-PE/blob/main/.claude/commands/phase3-review.md)) | out 470k, read 9.6M | REVIEW.md, hand execution, area recheck; 12 commits | ISA trim (D048), I2C hold (D049, D050), area limit and contingency order (D051) |
| 4 ISS + assembler | Opus 5.5, high, auto | phase 4 + "test first" cases ([prompts](https://github.com/NotCleo/JS-PE/blob/main/docs/ai/PROMPT-LIST/prompts.md#phase-4-assembler-and-golden-iss), [procedure](https://github.com/NotCleo/JS-PE/blob/main/.claude/commands/phase4-iss.md)) | out 529k, read 74.5M | gen_isa, assembler with static bound, cycle-accurate ISS, independent models; 51 commits | bound fix (D054), stale-edge fix not a spacing rule (D055), state measured I2C rate (D056), UART options (D057) |
| 5 skeleton + GDS | Opus 5.5, high, auto | `/phase5-skeleton` + info.yaml notes ([prompts](https://github.com/NotCleo/JS-PE/blob/main/docs/ai/PROMPT-LIST/prompts.md#phase-5-walking-skeleton-through-the-tt-gds-flow), [procedure](https://github.com/NotCleo/JS-PE/blob/main/.claude/commands/phase5-skeleton.md)) | out 167k, read 41.1M | GDS runs 1–4; SRAM failed LVS, flop build green; 11 commits | run 3 is the last macro attempt → flops (D062); area on the core (D063); IR-drop task |
| 6 RTL blocks | Opus 5.5 / Fable 5.2, high, plan → auto | `/rtl-block <module>` per block ([prompts](https://github.com/NotCleo/JS-PE/blob/main/docs/ai/PROMPT-LIST/prompts.md#phase-6-rtl-blocks), [procedure](https://github.com/NotCleo/JS-PE/blob/main/.claude/commands/rtl-block.md)) | out 538k, read 48.2M | engine (lockstep vs ISS), arb, fifo, sig, lfsr, pins, link; 18 commits | spec corrections (D070), FIFO coding kept (D075), link cap by area (D079) |
| 6b integration, timing, review | Opus 5.5, max/high, auto | integrate top; GDS failure triage; RTL review ([prompts](https://github.com/NotCleo/JS-PE/blob/main/docs/ai/PROMPT-LIST/prompts.md#integration-and-timing-closure)) | out 316k, read 57.1M | full top, run 5 green, RTL_REVIEW.md, random full-chip lockstep; 18 commits | H_RDY rule (D080); +1 clk pad fix over a flow-side fix (D081); area freeze (D083); same-clock rules (D084–D086) |
| 7 protocols | Opus 5.5, high, auto | `/protocol uart`, `spi`, `i2c`, not-designed-in bring-up, CAN in steps ([prompts](https://github.com/NotCleo/JS-PE/blob/main/docs/ai/PROMPT-LIST/prompts.md#phase-7-protocol-bring-up), [procedure](https://github.com/NotCleo/JS-PE/blob/main/.claude/commands/protocol.md)) | out 1.71M, read 131.8M | UART, SPI, I2C, WS2812, Manchester, CAN in lockstep; 23 commits | reload rule (D088), timing claims stated as measured (D093–D097, D101), CVCHK (D099), sound bound (D100), `rtl-freeze-v1` tag, CAN "correctness first, no cuts" |
| 8a formal | Opus 5.5, max, auto | `/phase8-signoff` formal sub-steps ([prompts](https://github.com/NotCleo/JS-PE/blob/main/docs/ai/PROMPT-LIST/prompts.md#phase-8-formal-verification), [procedure](https://github.com/NotCleo/JS-PE/blob/main/.claude/commands/phase8-signoff.md)) | out 674k, read 79.4M | 11 blocks proven, 213 mutants; found the FP reset bug; 30 commits | fix option (b) for D109; formal obligations rule (D113, D115) |
| 8b timing closure | Opus 5.5, high, auto | flow tuning with run rules ([prompts](https://github.com/NotCleo/JS-PE/blob/main/docs/ai/PROMPT-LIST/prompts.md#phase-8-timing-and-area)) | out 62k, read 10.5M | runs 6–8; run 8 is sign-off; 8 commits | try AREA synthesis + fanout with fallback rules (D119–D121) |
| 8c GL + IR | Opus 5.5, high, auto | local GL, all 45 tests, IR estimate ([prompts](https://github.com/NotCleo/JS-PE/blob/main/docs/ai/PROMPT-LIST/prompts.md#phase-8-gate-level-simulation-and-ir-drop)) | out 70k, read 8.7M | GL 45/45, IR_DROP.md; 5 commits | all 45 tests at gate level (D123) |
| 8d cleanup | Opus 5.5, medium | template leftovers ([prompts](https://github.com/NotCleo/JS-PE/blob/main/docs/ai/PROMPT-LIST/prompts.md#cleanup-and-write-up)) | out 9k, read 0.8M | 1 commit | — |
| 9 write-up | Opus 5.5, xhigh, auto | README + docs in steps ([prompts](https://github.com/NotCleo/JS-PE/blob/main/docs/ai/PROMPT-LIST/prompts.md#cleanup-and-write-up), [procedure](https://github.com/NotCleo/JS-PE/blob/main/.claude/commands/writeup.md)) | in progress | this page and the rest of docs/ | answers to the write-up TODO list |

Human decisions are the lines in DECISIONS.md marked "user decision", "user request" or "approved" (40 lines explicitly, plus the architecture approval D017 and the requirement answers D001–D008); every other line was proposed by Claude and accepted when I approved the step.

## 3. Tokens: method and totals

`python scripts/ai_usage.py` reads `~/.claude/projects/<this repo>/*.jsonl` (32 main sessions) and `<session>/subagents/agent-*.jsonl`, keeps one record per API message id (a message split over several log lines repeats its usage), and assigns each message to a stage by its timestamp: a stage runs from the last commit of the previous stage to its own last commit (the commit list is at the top of the script). Effort comes from each assistant record, permission mode from each typed prompt ("not recorded" in older log lines). Limits: the claude.ai chat before the repo is not included; output tokens include thinking; cache reads dominate because every turn re-reads the cached context, so they are not comparable to fresh input; the write-up stage was still running when this table was made. Totals are approximate.

Snapshot 2026-11-20 (regenerate with `python scripts/ai_usage.py`; `--sessions` adds one row per session):

<!-- ai_usage:begin -->
| Stage | Main-session models (messages) | Effort (messages) | Permission mode (prompts) | Subagents (runs) | Input | Output | Cache write | Cache read |
|---|---|---|---|---|---|---|---|---|
| 1 distill + setup | claude-opus-5-5 25 | high 21, medium 4 | not recorded 5, acceptEdits 1 | statusline-setup (claude-sonnet-5-5) 1 | 106 | 62 952 | 402 151 | 2 302 350 |
| 2a architecture candidates | claude-fable-5-2 27, claude-opus-5-5 2 | max 27, high 2 | not recorded 4 | — | 60 | 168 922 | 239 739 | 3 811 670 |
| 2b spec (ARCH, ISA, PINS, HOSTIF, VERIF) | claude-fable-5-2 75 | max 75 | acceptEdits 4, not recorded 4, auto 1 | web-fetch (claude-haiku-5-5) 1 | 166 | 832 621 | 1 104 183 | 19 857 177 |
| 3 spec review | claude-fable-5-2 39 | max 39 | acceptEdits 3, not recorded 1 | — | 88 | 469 642 | 533 077 | 9 593 402 |
| 4 ISS, assembler, tests | claude-opus-5-5 377, claude-fable-5-2 2 | high 377, max 2 | auto 6, not recorded 3 | test-runner (claude-haiku-5-5) 2 | 801 | 528 589 | 954 620 | 74 528 562 |
| 5 walking skeleton + GDS | claude-opus-5-5 168 | high 168 | not recorded 8, auto 4 | web-fetch (claude-haiku-5-5) 2 | 367 | 166 830 | 756 826 | 41 118 692 |
| 6 RTL blocks | claude-fable-5-2 123, claude-opus-5-5 121 | high 244 | not recorded 13, auto 5, plan 2 | test-runner (claude-haiku-5-5) 11, reviewer (claude-fable-5-2) 1 | 731 | 537 890 | 1 274 485 | 48 176 411 |
| 6b integration, timing fix, RTL review | claude-opus-5-5 206 | max 125, high 81 | auto 6, not recorded 1 | test-runner (claude-haiku-5-5) 5 | 542 | 316 060 | 841 052 | 57 116 051 |
| 7 protocol bring-up (UART..CAN) | claude-opus-5-5 568 | high 563, medium 3, max 2 | auto 19, not recorded 6 | reviewer (claude-opus-5-5) 5, test-runner (claude-haiku-5-5) 5, web-fetch (claude-haiku-5-5) 2 | 1 636 | 1 711 253 | 3 069 460 | 131 811 729 |
| 8a formal | claude-opus-5-5 277 | max 276, high 1 | auto 12, not recorded 4 | test-runner (claude-haiku-5-5) 2 | 636 | 673 911 | 983 051 | 79 368 224 |
| 8b timing closure | claude-opus-5-5 109 | high 97, medium 10, max 2 | not recorded 4, auto 4 | — | 234 | 62 096 | 502 832 | 10 469 814 |
| 8c gate level + IR drop | claude-opus-5-5 96 | high 96 | auto 3, not recorded 2 | test-runner (claude-haiku-5-5) 2 | 264 | 69 567 | 237 110 | 8 667 267 |
| 8d cleanup | claude-opus-5-5 14 | medium 13, high 1 | not recorded 2, auto 2 | test-runner (claude-haiku-5-5) 1 | 60 | 9 203 | 73 627 | 841 970 |
| 9 write-up | claude-opus-5-5 89 | xhigh 88, medium 1 | auto 2, not recorded 1 | test-runner (claude-haiku-5-5) 1 | 190 | 105 707 | 278 230 | 15 812 237 |
| **total** | | | | | 5 881 | 5 715 243 | 11 250 443 | 503 475 556 |

sessions: 32 main logs, subagent messages included in their session's stage by timestamp
<!-- ai_usage:end -->

## 4. What I did myself

| Area | What | Source |
|---|---|---|
| Requirements | wrote the answers that fixed scope, pins, bandwidth, memory, clocks and demos | D001–D008, prompts stage 1 |
| Architecture | chose Cleo and changed it (time-division fetch, latched events, solo map) | D017–D021 |
| Rules of the process | phase plan, CLAUDE.md rules, area freeze, run rules for the GDS flow, macro last-attempt rule | CLAUDE.md, D062, D083, D119–D121 |
| Judgement calls | every "stop and ask" during the build: 40 DECISIONS lines | DECISIONS.md |
| Pushes and CI | pushed every batch and watched the GitHub Actions runs | prompts (`git push`) |
| Images | the two README images ([floorplan](https://github.com/NotCleo/JS-PE/blob/main/docs/images/floorplan.jpeg), [Ethernet concept](https://github.com/NotCleo/JS-PE/blob/main/docs/images/ethernet_concept.jpeg)) | Diagrams drawn by the author; simplified. |

Split: Claude wrote all RTL, Python, tests, formal properties, programs and documents; I set the requirements and constraints, made the architecture choice and the 40+ logged judgement calls, ran the pushes and CI, and reviewed results through the summaries and the reviewer subagent. I did not hand-review every RTL line.
