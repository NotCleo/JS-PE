# SRAM macro retry (D124)

Flow/config only on `JSPE_MEM_SRAM`; base = 6896dd8 merged into today's src/config.json. Evidence: run 3 (43845628155) `GDS_logs`; LibreLane 3.1.0.dev4 source.

## Root cause: a real open
- Names are cosmetic: Magic writes the LEF pins `VDD!/VSS!/VDDARRAY!` as `VDD/VSS/VDDARRAY`, netgen reads `\VDD!` from the powered netlist and pairs them ("Cell pin lists are equivalent").
- pdngen cuts stdcell-grid shapes at hard macros: every Metal4 strap, D060's 32 included, stops at y = 195.60 µm (macro top 195.12 + halo 0.48). The top `.ext` has no merge with the macro's supply ports, so the layout has 3 floating nets (1938 vs 1935). PDN log: `PSM-0039 Unconnected instance u_mem.u_sram/VDD!`. Corrects D058, D061, D062.
- JSON crash: unescaped `"\VDD!"` in netgen's `badnets`, mismatch only.

## Fix
- `src/pdn_cfg.tcl` (D129): after the stock `pdngen`, one Metal4 strap per supply column (all 40) from the macro's bottom into the core strap that ends at the halo. A pdngen macro grid can't do it: straps confined to the macro, trimmed (no vias), core straps cut short of the pins.
- No equate, no renamed views: Netgen.LVS never reads the macro CDL (MACROS `spice` unused, `-blackbox`), and renamed LEF pins would diverge from the GDS labels. Fallback if a pin mismatch remains: `"NETGEN_SETUP": "dir::netgen_setup.tcl"` (sources the PDK file, adds `equate pins`); TT passes src/config.json keys through to config_merged.json.
- Local check before attempt 1: full TT harden in the LibreLane 3.1.0.dev4 container (same PDK), PSM grid check and LVS.

## From 6896dd8
MACROS (pdk_dir:: views, (3.36, 3.78) FS), halos, PDN_MACRO_CONNECTIONS, PDN_CFG, Metal4 pitch/offset/spacing 67.66/17.39/3.52, Magic DRC off + overlaps waived + KLayout DRC on. Keep D082 repair settings; add `"VERILOG_DEFINES": ["JSPE_MEM_SRAM"]`. PSM/IR: back on (grid check passes locally).

## GLS model
Vendored IHP behavioural model, test/ihp_sram (Apache-2.0, `-DSIM -DFUNCTIONAL`), already in RTL and GL builds; run with `JSPE_MEM_WORDS=512` (CI gl_test too). Check the GL netlist's macro instance has no supply pins.

## Budget
≤ 2 GDS runs by 2026-12-10, on a branch; else main keeps the flops.

## Risks
- Full top (46.8k cells) never ran with the macro (run 3 = 1.9k-cell skeleton): slow setup (run 8 WS 1.18 ns), read path from the corner, Metal4 congestion (~38 % of tracks above the macro).
- KLayout DRC with straps over the macro untested; LEF OBS bar at local y 39–45 µm.
- Precheck with a macro untested.
