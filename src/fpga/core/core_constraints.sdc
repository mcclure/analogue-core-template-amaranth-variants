#
# user core constraints
#
# put your clock groups in here as well as any net assignments
#

create_generated_clock -name vid_0 -source [get_ports {clk_74a}] -duty_cycle 50 -divide_by 48 -phase 7.5 [get_registers {core_top:ic|amaranth_core:ac|amaranth_core.video_clk_div:video_clk_div|clk_reg[0]}]
create_generated_clock -name vid_90 -source [get_ports {clk_74a}] -duty_cycle 50 -divide_by 48 -phase 97.5 [get_registers {core_top:ic|amaranth_core:ac|amaranth_core.video_clk_div:video_clk_div|clk_reg[12]}]

set_clock_groups -asynchronous \
 -group { bridge_spiclk } \
 -group { clk_74a } \
 -group { clk_74b } \
 -group { vid_0 } \
 -group { vid_90 }

#proc debug_andi_col2list { col } {
#   set list ""
#   foreach_in_collection c $col { lappend list [get_object_name $c] }
#   return $list
#}
#set debugAndi1 [open "debug_andi_1.txt" "w"]
#puts $debugAndi1 "TEST1234"
#puts $debugAndi1 [debug_andi_col2list [get_ports clk_74a]]
#close $debugAndi1
