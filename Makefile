# Terse wrappers for Claude Code. Full logs go to files; only summaries hit the terminal.
TOP ?= $(shell sed -n 's/^ *top_module: *"\([^"]*\)".*/\1/p' info.yaml)
B ?=
NOW := $(shell date +%s)
OSS ?= $(HOME)/tools/oss-cad-suite/bin
T ?=
# MEM=flop: 128x16 flop fallback instead of the SRAM macro (D131)
MEM ?=
MEMDEF = $(if $(filter flop,$(MEM)),-DJSPE_MEM_FLOP)

.PHONY: test unit lint area swtest swtest-all gl gl-fetch gl-local gl-all formal mutate clean
test:
	@$(MAKE) -s -C test JSPE_MEM=$(MEM) > test/last.log 2>&1; python3 scripts/test_summary.py test/results.xml test/last.log $(NOW)

gl:
	@$(MAKE) -s -C test GATES=yes JSPE_MEM=$(MEM) > test/last_gl.log 2>&1; python3 scripts/test_summary.py test/results.xml test/last_gl.log $(NOW)

# GL sim on this machine: PDK via ciel at the flow version, netlist of a GDS run (tt_submission only).
# OSS iverilog (>= 13): the PDK flop models clock only through $$setuphold delayed_* nets, iverilog 12 leaves them X
PDK_ROOT ?= $(HOME)/.ciel
CIEL ?= $(if $(wildcard .venv/bin/ciel),.venv/bin/ciel,ciel)
RUN ?=
GL_MOD = $(basename $(notdir $(firstword $(shell grep -lw "def $(T)" test/test*.py))))
gl-fetch:
	@test -n "$(RUN)" || { echo "usage: make gl-fetch RUN=<gds run id>"; exit 1; }
	@d=build/gl_$(RUN); rm -rf $$d; gh run download $(RUN) -n tt_submission -D $$d && cp $$d/tt_submission/*.v test/gate_level_netlist.v && \
	 jq -r '"commit \(.commit)"' $$d/tt_submission/commit_id.json && v=$$(jq -r .PDK_VERSION $$d/tt_submission/pdk.json) && echo "pdk $$v" && \
	 $(CIEL) enable --pdk-root $(PDK_ROOT) --pdk-family ihp-sg13 $$v 2>&1 | tail -1
# shim: OSS iverilog, OSS vvp binary on system libs (the OSS vvp wrapper swaps in its own Python and breaks cocotb)
IVL = $(abspath build/ivl)
OSS_TOP = $(abspath $(OSS)/..)
$(IVL)/vvp:
	@mkdir -p $(IVL)/lib && ln -sf $(OSS_TOP)/lib/libvvp.so.1 $(IVL)/lib/ && \
	 printf '#!/bin/sh\nexec $(OSS)/iverilog "$$@"\n' > $(IVL)/iverilog && \
	 printf '#!/bin/sh\nLD_LIBRARY_PATH=$(IVL)/lib exec $(OSS_TOP)/libexec/vvp "$$@"\n' > $@ && chmod +x $(IVL)/iverilog $@
GL_ARGS = GATES=yes JSPE_MEM=$(MEM) PDK_ROOT=$(PDK_ROOT) ICARUS_BIN_DIR=$(IVL) SIM_BUILD=sim_build/gl_local FST=-none
GLD = test/sim_build/gl_local
J ?= 8
GL_TMO ?= 3000
# every test (or module M) as its own sim, J in parallel; results $(GLD)/res, logs $(GLD)/log
gl-all: $(IVL)/vvp
	@test -f test/gate_level_netlist.v || { echo "no netlist: make gl-fetch RUN=<id>"; exit 1; }
	@cd test && $(MAKE) -s $(GL_ARGS) sim_build/gl_local/sim.vvp > $(CURDIR)/test/last_gl.log 2>&1 || { echo "GL compile failed: test/last_gl.log"; exit 1; }
	@rm -rf $(GLD)/res $(GLD)/log; mkdir -p $(GLD)/res $(GLD)/log
	@python3 scripts/gl_all.py $(GL_ARGS) -- $(GLD)/res $(GLD)/log $(J) $(GL_TMO) $(M)
	@python3 scripts/test_summary.py $(GLD)/res $(GLD)/log $(NOW)
gl-local: $(IVL)/vvp
	@test -f test/gate_level_netlist.v || { echo "no netlist: make gl-fetch RUN=<id>"; exit 1; }
	@$(MAKE) -s -C test $(GL_ARGS) $(if $(T),COCOTB_TEST_MODULES=$(GL_MOD) COCOTB_TEST_FILTER='\.$(T)$$$$') > test/last_gl.log 2>&1; python3 scripts/test_summary.py test/results.xml test/last_gl.log $(NOW)

unit:
	@$(MAKE) -s -C test/unit B=$(B) > test/unit/last_$(B).log 2>&1; python3 scripts/test_summary.py test/unit/results_$(B).xml test/unit/last_$(B).log $(NOW)

lint:
	@verilator --lint-only -Wall -Wno-DECLFILENAME -Isrc $(MEMDEF) --top-module $(or $(B),$(TOP)) src/*.v 2>&1 | grep -E '^%(Error|Warning)' | head -20; echo "lint done"

area:
	@MEMDEF=$(MEMDEF) python3 scripts/area.py $(or $(B),$(TOP)) $(LIB)

formal:
	@mkdir -p build/formal; cd formal && PATH=$(OSS):$$PATH sby -f --prefix ../build/formal/$(B) $(B).sby $(T) > ../build/formal/$(B).log 2>&1; cd .. && PATH=$(OSS):$$PATH python3 scripts/formal_summary.py $(B) $(T)

mutate:
	@mkdir -p build/formal; PATH=$(OSS):$$PATH python3 scripts/mutate.py $(B)

swtest:
	@JSPE_LINT=error python3 -m pytest -q -m "not slow" sw/tests 2>&1 | tail -15

swtest-all:
	@JSPE_LINT=error python3 -m pytest -q sw/tests 2>&1 | tail -15

clean:
	rm -rf test/sim_build test/unit/sim_build test/*.xml test/unit/*.xml test/*.log test/unit/*.log build
