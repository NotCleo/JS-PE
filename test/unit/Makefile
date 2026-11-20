# Per-block cocotb runner. usage (from repo root): make unit B=<module>
# Test lives in test/unit/<module>/test_<module>.py
SIM ?= icarus
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(abspath ../../src)
B ?= $(error set B=<module>)
# optional wrapper test/unit/<module>/tb_<module>.v becomes the toplevel (e.g. several parameter sets)
TB = $(wildcard $(B)/tb_$(B).v)
VERILOG_SOURCES = $(wildcard $(SRC_DIR)/*.v) $(wildcard $(abspath ../ihp_sram)/*.v) $(abspath $(TB))
TOPLEVEL = $(if $(TB),tb_$(B),$(B))
COCOTB_TEST_MODULES = test_$(B)
SIM_BUILD = sim_build/$(B)
COMPILE_ARGS += -I$(SRC_DIR) -DSIM -DFUNCTIONAL
export COCOTB_RESULTS_FILE = $(CURDIR)/results_$(B).xml
export PYTHONPATH := $(CURDIR)/$(B):$(abspath ../../sw):$(PYTHONPATH)
include $(shell cocotb-config --makefiles)/Makefile.sim
