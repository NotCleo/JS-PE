# Tests (run from repo root)
- `make test` — top-level cocotb suite (tb.v + test_*.py); `make unit B=<jspe_block>` — per-block test in test/unit/<block>/
- `make swtest` — pytest for sw/ (assembler, ISS, checkers; skips slow); `make swtest-all` includes slow tests
- `make gl-fetch RUN=<gds run id>` — download the GDS run's netlist to test/gate_level_netlist.v
- `make gl-local` / `make gl-all` — gate-level sim of the top-level test / all tests against that netlist
- `make formal B=<block>` — SymbiYosys proof of formal/<block>.sby; `make mutate B=<block>` — mutation check of its properties
- `make lint` — verilator -Wall; `make area [B=<module>]` — yosys cell area
