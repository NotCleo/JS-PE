# LibreLane 3.1.0.dev4 scripts/openroad/common/pdn_cfg.tcl (Apache-2.0) with Cleo's SRAM macro grid (D058, D060, D128):
# the default macro grid is replaced by one grid per hard macro with a Metal4 strap on every supply column.
# Copyright 2025 LibreLane Contributors
#
# Adapted from OpenLane
#
# Copyright 2020-2022 Efabless Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
source $::env(SCRIPTS_DIR)/openroad/common/set_global_connections.tcl
set_global_connections

set secondary []
foreach vdd $::env(VDD_NETS) gnd $::env(GND_NETS) {
    if { $vdd != $::env(VDD_NET)} {
        lappend secondary $vdd

        set db_net [[ord::get_db_block] findNet $vdd]
        if {$db_net == "NULL"} {
            set net [odb::dbNet_create [ord::get_db_block] $vdd]
            $net setSpecial
            $net setSigType "POWER"
        }
    }

    if { $gnd != $::env(GND_NET)} {
        lappend secondary $gnd

        set db_net [[ord::get_db_block] findNet $gnd]
        if {$db_net == "NULL"} {
            set net [odb::dbNet_create [ord::get_db_block] $gnd]
            $net setSpecial
            $net setSigType "GROUND"
        }
    }
}

set_voltage_domain -name CORE -power $::env(VDD_NET) -ground $::env(GND_NET) \
    -secondary_power $secondary



if { $::env(PDN_MULTILAYER) == 1 } {

    set arg_list [list]
    if { $::env(PDN_ENABLE_PINS) } {
        lappend arg_list -pins "$::env(PDN_VERTICAL_LAYER) $::env(PDN_HORIZONTAL_LAYER)"
    }

    define_pdn_grid \
        -name stdcell_grid \
        -starts_with POWER \
        -voltage_domain CORE \
        {*}$arg_list

    set arg_list [list]
    append_if_equals arg_list PDN_EXTEND_TO "core_ring" -extend_to_core_ring
    append_if_equals arg_list PDN_EXTEND_TO "boundary" -extend_to_boundary

    add_pdn_stripe \
        -grid stdcell_grid \
        -layer $::env(PDN_VERTICAL_LAYER) \
        -width $::env(PDN_VWIDTH) \
        -pitch $::env(PDN_VPITCH) \
        -offset $::env(PDN_VOFFSET) \
        -spacing $::env(PDN_VSPACING) \
        -starts_with POWER \
        {*}$arg_list

    add_pdn_stripe \
        -grid stdcell_grid \
        -layer $::env(PDN_HORIZONTAL_LAYER) \
        -width $::env(PDN_HWIDTH) \
        -pitch $::env(PDN_HPITCH) \
        -offset $::env(PDN_HOFFSET) \
        -spacing $::env(PDN_HSPACING) \
        -starts_with POWER \
        {*}$arg_list

    add_pdn_connect \
        -grid stdcell_grid \
        -layers "$::env(PDN_VERTICAL_LAYER) $::env(PDN_HORIZONTAL_LAYER)"
} else {

    set arg_list [list]
    if { $::env(PDN_ENABLE_PINS) } {
        lappend arg_list -pins "$::env(PDN_VERTICAL_LAYER)"
    }

    define_pdn_grid \
        -name stdcell_grid \
        -starts_with POWER \
        -voltage_domain CORE \
        {*}$arg_list

    set arg_list [list]
    append_if_equals arg_list PDN_EXTEND_TO "core_ring" -extend_to_core_ring
    append_if_equals arg_list PDN_EXTEND_TO "boundary" -extend_to_boundary

    add_pdn_stripe \
        -grid stdcell_grid \
        -layer $::env(PDN_VERTICAL_LAYER) \
        -width $::env(PDN_VWIDTH) \
        -pitch $::env(PDN_VPITCH) \
        -offset $::env(PDN_VOFFSET) \
        -spacing $::env(PDN_VSPACING) \
        -starts_with POWER \
        {*}$arg_list
}

# Supply columns of a hard macro on the stripe layer, as {net x_um}.
proc jspe_macro_columns {inst} {
    set dbu [[ord::get_db_block] getDbUnitsPerMicron]
    set bb [$inst getBBox]
    set orient [$inst getOrient]
    set cols [list]
    foreach mt [[$inst getMaster] getMTerms] {
        set sig [$mt getSigType]
        if { $sig == "POWER" } {
            set net $::env(VDD_NET)
        } elseif { $sig == "GROUND" } {
            set net $::env(GND_NET)
        } else {
            continue
        }
        foreach mp [$mt getMPins] {
            foreach box [$mp getGeometry] {
                set l [$box getTechLayer]
                if { $l == "NULL" || [$l getName] != $::env(PDN_VERTICAL_LAYER) } { continue }
                set lx [expr {([$box xMin] + [$box xMax]) / 2.0 / $dbu}]
                if { $orient == "R0" || $orient == "MX" } {
                    set x [expr {[$bb xMin] / double($dbu) + $lx}]
                } elseif { $orient == "MY" || $orient == "R180" } {
                    set x [expr {[$bb xMax] / double($dbu) - $lx}]
                } else {
                    puts "\[WARNING\] Cleo: macro [$inst getName] orientation $orient not handled"
                    continue
                }
                set col [list $net [expr {round($x * 200.0) / 200.0}]]
                if { [lsearch -exact $cols $col] < 0 } { lappend cols $col }
            }
        }
    }
    return $cols
}

# D060: full-height strap above each macro supply column that the base pitch misses (stdcell grid, cut at the macro).
set jspe_block [ord::get_db_block]
set jspe_dbu [$jspe_block getDbUnitsPerMicron]
set jspe_core_x [expr {[[$jspe_block getCoreArea] xMin] / double($jspe_dbu)}]
set jspe_w $::env(PDN_VWIDTH)
set jspe_macros [list]
foreach jspe_inst [$jspe_block getInsts] {
    if { [[$jspe_inst getMaster] getType] == "BLOCK" } { lappend jspe_macros $jspe_inst }
}
foreach jspe_inst $jspe_macros {
    foreach jspe_col [jspe_macro_columns $jspe_inst] {
        lassign $jspe_col jspe_net jspe_x
        set jspe_base $::env(PDN_VOFFSET)
        if { $jspe_net == $::env(GND_NET) } {
            set jspe_base [expr {$jspe_base + $jspe_w + $::env(PDN_VSPACING)}]
        }
        set jspe_off [expr {$jspe_x - $jspe_core_x}]
        set jspe_k [expr {round(($jspe_off - $jspe_base) / $::env(PDN_VPITCH))}]
        if { abs($jspe_off - $jspe_base - $jspe_k * $::env(PDN_VPITCH)) < $jspe_w } { continue }
        puts "\[INFO\] Cleo: $jspe_net strap above [$jspe_inst getName] column at x = $jspe_x"
        add_pdn_stripe \
            -grid stdcell_grid \
            -layer $::env(PDN_VERTICAL_LAYER) \
            -width $jspe_w \
            -pitch 2000 \
            -offset $jspe_off \
            -number_of_straps 1 \
            -nets $jspe_net \
            {*}$arg_list
    }
}

# Adds the standard cell rails if enabled.
if { $::env(PDN_ENABLE_RAILS) == 1 } {
    add_pdn_stripe \
        -grid stdcell_grid \
        -layer $::env(PDN_RAIL_LAYER) \
        -width $::env(PDN_RAIL_WIDTH) \
        -followpins

    add_pdn_connect \
        -grid stdcell_grid \
        -layers "$::env(PDN_RAIL_LAYER) $::env(PDN_VERTICAL_LAYER)"
}


# Adds the core ring if enabled.
if { $::env(PDN_CORE_RING) == 1 } {
    if { $::env(PDN_MULTILAYER) == 1 } {
        set arg_list [list]
        append_if_flag arg_list PDN_CORE_RING_ALLOW_OUT_OF_DIE -allow_out_of_die
        append_if_flag arg_list PDN_CORE_RING_CONNECT_TO_PADS -connect_to_pads
        append_if_equals arg_list PDN_EXTEND_TO "boundary" -extend_to_boundary
        append_if_exists_argument arg_list PDN_CORE_RING_CONNECT_TO_PAD_LAYERS -connect_to_pad_layers

        set pdn_core_vertical_layer $::env(PDN_VERTICAL_LAYER)
        set pdn_core_horizontal_layer $::env(PDN_HORIZONTAL_LAYER)

        if { [info exists ::env(PDN_CORE_VERTICAL_LAYER)] } {
            set pdn_core_vertical_layer $::env(PDN_CORE_VERTICAL_LAYER)
        }

        if { [info exists ::env(PDN_CORE_HORIZONTAL_LAYER)] } {
            set pdn_core_horizontal_layer $::env(PDN_CORE_HORIZONTAL_LAYER)
        }

        add_pdn_ring \
            -grid stdcell_grid \
            -layers "$pdn_core_vertical_layer $pdn_core_horizontal_layer" \
            -widths "$::env(PDN_CORE_RING_VWIDTH) $::env(PDN_CORE_RING_HWIDTH)" \
            -spacings "$::env(PDN_CORE_RING_VSPACING) $::env(PDN_CORE_RING_HSPACING)" \
            -core_offset "$::env(PDN_CORE_RING_VOFFSET) $::env(PDN_CORE_RING_HOFFSET)" \
            {*}$arg_list

        if { [info exists ::env(PDN_CORE_VERTICAL_LAYER)] } {
            add_pdn_connect \
                -grid stdcell_grid \
                -layers "$::env(PDN_CORE_VERTICAL_LAYER) $::env(PDN_HORIZONTAL_LAYER)"
        }

        if { [info exists ::env(PDN_CORE_HORIZONTAL_LAYER)] } {
            add_pdn_connect \
                -grid stdcell_grid \
                -layers "$::env(PDN_CORE_HORIZONTAL_LAYER) $::env(PDN_VERTICAL_LAYER)"
        }

        if { [info exists ::env(PDN_CORE_VERTICAL_LAYER)] && [info exists ::env(PDN_CORE_HORIZONTAL_LAYER)] } {
            add_pdn_connect \
                -grid stdcell_grid \
                -layers "$::env(PDN_CORE_VERTICAL_LAYER) $::env(PDN_CORE_HORIZONTAL_LAYER)"
        }

    } else {
        throw APPLICATION "PDN_CORE_RING cannot be used when PDN_MULTILAYER is set to false."
    }
}

# D128: one grid per hard macro, a strap on every supply column, extended to macro + halo where the straps above start.
foreach jspe_inst $jspe_macros {
    set jspe_grid "jspe_macro_[regsub -all {[^A-Za-z0-9_]} [$jspe_inst getName] _]"
    define_pdn_grid \
        -macro \
        -instances [$jspe_inst getName] \
        -name $jspe_grid \
        -starts_with POWER \
        -halo "$::env(PDN_HORIZONTAL_HALO) $::env(PDN_VERTICAL_HALO)" \
        -grid_over_boundary
    set jspe_ref [expr {[[$jspe_inst getBBox] xMin] / double($jspe_dbu)}]
    foreach jspe_col [jspe_macro_columns $jspe_inst] {
        lassign $jspe_col jspe_net jspe_x
        puts "\[INFO\] Cleo: $jspe_net strap over [$jspe_inst getName] column at x = $jspe_x"
        add_pdn_stripe \
            -grid $jspe_grid \
            -layer $::env(PDN_VERTICAL_LAYER) \
            -width $jspe_w \
            -pitch 2000 \
            -offset [expr {$jspe_x - $jspe_ref}] \
            -number_of_straps 1 \
            -nets $jspe_net \
            -extend_to_boundary
    }
}
