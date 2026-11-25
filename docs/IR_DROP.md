# IR drop — hand estimate (VERIF_PLAN §9)

This is a hand estimate, not a signed-off analysis. It covers the shipped SRAM build (D131, run 45553919240). PSM runs on that build again (D129): it reports every VPWR and VGND shape connected and a worst IR drop of 0.31 mV VPWR / 0.27 mV VGND, but without `VSRC_LOC_FILES` it treats the stripes as ideal sources and sees only rails and vias.

## Inputs
- Power: 10.0 mW (typ corner, 50 MHz, vectorless activity), so I = 8.33 mA. The slow corner draws less (P ∝ V²). A 2× margin covers the fast corner and the worst protocol mix.
- Core: 1718.4 × 703.1 µm.
- PDN (TT template, `FP_PDN_MULTILAYER` 0): Metal4 stripes only, 2.1 µm wide at a 67.66 µm pitch (D058), 25 per net. Metal1 rails are 0.44 µm wide.
- Macro grid (D128–D130): the 512x16 SRAM sits bottom-left. pdngen stops the core stripes at the macro top + halo (y = 195.60 µm), so src/pdn_cfg.tcl adds one Metal4 strap on each of the macro's 40 supply columns, overlapping the core stripe above it, and D060's 32 column straps feed the logic over the macro. Each of those stripes is one pin box from the macro's bottom (y = 3.78 µm) to the die top, so the harness reaches it from both ends.
- Resistance (sg13cmos5l tech LEF): Metal4 0.103 Ω/□, Metal1 0.135 Ω/□, 20 Ω per via cut.

## Assumptions
- Current is spread evenly over the core and shared equally by the 25 base stripes; the macro straps and D060 straps only add paths, so ignoring them is pessimistic.
- Each stripe is fed from one die edge only (TT harness straps ignored).
- VPWR and VGND behave the same.

## Segments (per net, 1×)
| Segment | R | I | Drop |
|---|---|---|---|
| Metal4 stripe, distributed load, die centre (3/8·IR) | 34.5 Ω | 0.333 mA | 4.3 mV |
| same, far end (½·IR) | | | 5.8 mV |
| Via stack M1→M4 (3 × 20 Ω) | 60 Ω | 2.5 µA | 0.15 mV |
| Metal1 rail, halfway between stripes | 10.4 Ω | 1.3 µA | < 0.02 mV |

## Result (die centre)
| Case | Per net | VDD+VSS | % (per net) |
|---|---|---|---|
| Distributed, 1× | 4.5 mV | 8.9 mV | 0.4 % |
| Distributed, 2× | 8.9 mV | 17.9 mV | 0.7 % |
| Lumped I·R (VERIF_PLAN literal), 2× | 23.3 mV | 46.6 mV | 1.9 % |

**Pass:** VERIF_PLAN §9 allows ≤ 5 % per net (60 mV). Every case passes, and the worst combined VDD+VSS case (46.6 mV = 3.9 %) is also under 5 %. The rail and via terms together are under 0.2 mV, which matches PSM's 0.31 mV.

## Flop fallback (`-DJSPE_MEM_FLOP`, run 44625407065)
- 19.5 mW, 35 stripes per net at a 50 µm pitch: 0.464 mA per stripe.
- Distributed 1× 6.2 mV per net (0.5 %); lumped 2× 32.3 mV per net (2.7 %), 64.6 mV combined (5.4 %, over a 5 % combined reading, inside a 10 % combined budget). PSM: 0.29 mV VPWR / 0.27 mV VGND.

## Limits
- There is no vector-based activity file. A local hotspot at 2–3× average density moves only the rail and via terms, and they stay under 1 mV.
- The macro's internal grid is the vendor's; only its 40 supply columns and their joins to the core grid are ours (PSM: all shapes connected).
- The sheet resistance is the LEF typical value. At +30 % the literal 2× case is 30 mV, still a pass.
- Corners: only the typ-corner power was used; the slow corner runs at 1.08 V and should draw less.
- Static only: no di/dt or decap analysis.
