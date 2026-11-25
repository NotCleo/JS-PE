# Makefile
# See https://docs.cocotb.org/en/stable/quickstart.html for more info

# defaults
SIM ?= icarus
FST ?= -fst # Use more efficient FST format
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src
PROJECT_SOURCES = tt_um_notcleo_jspe.v jspe_pins.v jspe_link.v jspe_mem.v jspe_engine.v jspe_seq.v jspe_alu.v jspe_timer.v jspe_shift.v jspe_epins.v jspe_arb.v jspe_fifo.v jspe_sig.v jspe_lfsr.v RM_IHPSG13_1P_512x16_c2_bm_bist_bb.v
# PDK behavioural model of the SRAM macro (Apache-2.0, vendored: the CI test job has no PDK)
SRAM_MODELS = $(PWD)/ihp_sram/RM_IHPSG13_1P_core_behavioral_bm_bist.v $(PWD)/ihp_sram/RM_IHPSG13_1P_512x16_c2_bm_bist.v

# separate build dirs: the memory define does not trigger a rebuild
MEMSUF = $(if $(filter flop,$(JSPE_MEM)),_flop)

ifneq ($(GATES),yes)

# RTL simulation:
SIM_BUILD				= sim_build/rtl$(MEMSUF)
VERILOG_SOURCES += $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))

else

# Gate level simulation:
SIM_BUILD				= sim_build/gl$(MEMSUF)
COMPILE_ARGS    += -DGL_TEST
COMPILE_ARGS    += -DFUNCTIONAL
COMPILE_ARGS    += -DSIM
# Cell models for the PDK in $(PDK) (ihp-sg13cmos5l or ihp-sg13g2), e.g. sg13cmos5l_stdcell.v
PDK ?= ihp-sg13cmos5l
PDK_CELL_PREFIX = $(patsubst ihp-%,%,$(PDK))
PDK_LIBS_REF = $(PDK_ROOT)/$(PDK)/libs.ref
VERILOG_SOURCES += $(PDK_LIBS_REF)/$(PDK_CELL_PREFIX)_io/verilog/$(PDK_CELL_PREFIX)_io.v
# Newer PDK versions ship the UDP (user-defined primitive) models in a separate file
VERILOG_SOURCES += $(wildcard $(PDK_LIBS_REF)/$(PDK_CELL_PREFIX)_stdcell/verilog/$(PDK_CELL_PREFIX)_udp.v)
VERILOG_SOURCES += $(PDK_LIBS_REF)/$(PDK_CELL_PREFIX)_stdcell/verilog/$(PDK_CELL_PREFIX)_stdcell.v

# this gets copied in by the GDS action workflow
VERILOG_SOURCES += $(PWD)/gate_level_netlist.v

endif

VERILOG_SOURCES += $(SRAM_MODELS)
COMPILE_ARGS    += -DSIM -DFUNCTIONAL $(JSPE_DEFINES)
# SRAM build by default (D131); flop fallback: make JSPE_MEM=flop (128 words)
ifeq ($(JSPE_MEM),flop)
COMPILE_ARGS    += -DJSPE_MEM_FLOP
export JSPE_MEM_WORDS = 128
endif

# Allow sharing configuration between design and testbench via `include`:
COMPILE_ARGS 		+= -I$(SRC_DIR)

# Include the testbench sources:
VERILOG_SOURCES += $(PWD)/tb.v
TOPLEVEL = tb

# List test modules to run, separated by commas and without the .py suffix:
COCOTB_TEST_MODULES = test,test_uart,test_spi,test_i2c,test_ws2812,test_manchester,test_can,test_usb_ls

# include cocotb's make rules to take care of the simulator setup
include $(shell cocotb-config --makefiles)/Makefile.sim
