# IR drop — hand estimate (VERIF_PLAN §9)

This is a hand estimate, not a signed-off analysis. D061 turned off PSM for the SRAM build. On the flop build, run 44625407065 does run PSM (0.29 mV VPWR / 0.27 mV VGND), but without `VSRC_LOC_FILES` it treats the stripes as ideal sources and sees only rails and vias.

## Inputs
- Power: 19.5 mW (typ corner, 50 MHz, vectorless activity), so I = 16.25 mA. The slow corner draws less (P ∝ V²). A 2× margin covers the fast corner and the worst protocol mix.
- Core: 1718.4 × 703.1 µm.
- PDN (TT template, `FP_PDN_MULTILAYER` 0): Metal4 stripes only, 2.1 µm wide at a 50 µm pitch, 35 per net. Metal1 rails are 0.44 µm wide.
- Resistance (sg13cmos5l tech LEF): Metal4 0.103 Ω/□, Metal1 0.135 Ω/□, 20 Ω per via cut.

## Assumptions
- Current is spread evenly over the core and shared equally by the stripes.
- Each stripe is fed from one die edge only (TT harness straps ignored).
- VPWR and VGND behave the same.

## Segments (per net, 1×)
| Segment | R | I | Drop |
|---|---|---|---|
| Metal4 stripe, distributed load, die centre (3/8·IR) | 34.5 Ω | 0.464 mA | 6.0 mV |
| same, far end (½·IR) | | | 8.0 mV |
| Via stack M1→M4 (3 × 20 Ω) | 60 Ω | 2.5 µA | 0.15 mV |
| Metal1 rail, halfway between stripes | 7.7 Ω | 1.3 µA | < 0.01 mV |

## Result (die centre)
| Case | Per net | VDD+VSS | % (per net) |
|---|---|---|---|
| Distributed, 1× | 6.2 mV | 12.3 mV | 0.5 % |
| Distributed, 2× | 12.3 mV | 24.7 mV | 1.0 % |
| Lumped I·R (VERIF_PLAN literal), 2× | 32.3 mV | 64.6 mV | 2.7 % |

**Pass:** VERIF_PLAN §9 allows ≤ 5 % per net (60 mV), and every case is under that. The rail and via terms together are under 0.2 mV, which matches PSM's 0.29 mV.

## Combined VDD+VSS vs the 5 % target
- Worst combined case: 64.6 mV = 5.4 %. That is 4.6 mV over VERIF_PLAN's 5 %, which is a per-net limit, but inside a 10 % combined budget.
- The worst case is pessimistic on three counts at once:
  - 2× the reported power.
  - Stripes fed from one die edge only, although the TT harness also connects them.
  - The whole stripe current is lumped at the far end; with the real distributed load, the drop at the die centre is 3/8 of that.
- Realistic combined figure (distributed load, 1×): 12.3 mV = 1.0 % (2×: 24.7 mV = 2.1 %).
- Corners: only the typ-corner power (19.5 mW) was used. The slow-corner power report exists only in the full GDS_logs artifact (~390 MB), which was not downloaded. The slow corner runs at 1.08 V, so it should draw less current than typ.

## Limits
- There is no vector-based activity file. A local hotspot at 2–3× average density moves only the rail and via terms, and they stay under 1 mV.
- The sheet resistance is the LEF typical value. At +30 % the literal 2× case is 42 mV, still a pass.
- Static only: no di/dt or decap analysis.
