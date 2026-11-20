`default_nettype none
// jspe_seq events and OVR (VERIF_PLAN 6, ARCH 5-6, D065, D084): F/D, fire, waiting and OVR against a model of ARCH 6.
// Clock class inputs taken from the DUT: irv, mo_v, wf/wabort (sub-step 3), undef (sub-step 4), cv0/cv1. gnt is free;
// proven without the link guarantees, covers reached with them (LINK_ENV).
module fv_seq_evt (
    input wire        clk,
    input wire        rst,
    input wire        run_p,
    input wire        stop_p,
    input wire        step_p,
    input wire        exec_p,
    input wire [15:0] exec_word,
    input wire        clr_ovr,
    input wire        clr_ht,
    input wire        rx_ovf,
    input wire [3:0]  lv,
    input wire [3:0]  rise,
    input wire [3:0]  fall,
    input wire        tick,
    input wire        tock,
    input wire        t_start,
    input wire        mis,
    input wire        hrx,
    input wire        htx,
    input wire        fb,
    input wire        fpar,
    input wire        frun,
    input wire        fw,
    input wire        loop_end,
    input wire        z_n,
    input wire        cy_n,
    input wire        flag_we,
    input wire [15:0] opb,
    input wire        wr_en,
    input wire [2:0]  wr_reg,
    input wire [8:0]  wr_data,
    input wire [8:0]  maddr,
    input wire        gnt,
    input wire [15:0] mem_rdata
);
`include "jspe_isa.vh"
`include "fv_seq_dut.vh"
`ifdef LINK_ENV
`include "fv_link_env.vh"
`endif

  // reset in the first clock
  reg f_past = 1'b0;
  always @(posedge clk) f_past <= 1'b1;
  always @(*) if (!f_past) assume (rst);

  // ARCH 6 model: events rise p0-p3, fall p0-p3, tick, tock; guards of formats E, JP, GM (isa.yaml formats.guarded)
  wire       act = (run | xm) & ~rst;
  wire [9:0] live = {tock, tick, fall, rise};
  wire       guarded = ((ir & FMT_E_MASK) == FMT_E_VAL) | ((ir & FMT_JP_MASK) == FMT_JP_VAL) | ((ir & FMT_GM_MASK) == FMT_GM_VAL);
  wire [3:0] gd = guarded ? ir[3:0] : G_ALWAYS;
  wire       ev_g = (gd >= G_RISE_P0) | (gd == G_TICK) | (gd == G_TOCK);
  wire [3:0] ev_i = (gd == G_TICK) ? 4'd8 : (gd == G_TOCK) ? 4'd9 : (gd >= G_FALL_P0) ? gd - G_FALL_P0 + 4'd4 : gd - G_RISE_P0;
  reg  [9:0] mf, md;
  reg        movr, g_true;
  always @(*) begin
    case (gd)
      G_ALWAYS: g_true = 1'b1;
      G_HRX:    g_true = hrx;
      G_HTX:    g_true = htx;
      G_C0:     g_true = cv0;
      G_C1:     g_true = cv1;
      G_NEVER:  g_true = 1'b0;
      default:  g_true = live[ev_i] | mf[ev_i];
    endcase
  end
  wire       arm_m   = act & irv & ~wf & ~mo_v;
  wire       fire_m  = arm_m & ~undef & g_true;
  wire       wait_m  = arm_m & ~undef & ~g_true;
  wire       busy    = act & ~wait_m & ~wabort;
  wire [9:0] cons    = (fire_m & ev_g) ? (10'd1 << ev_i) : 10'd0;
  wire [9:0] tclr    = t_start ? 10'h300 : 10'h000;
  wire [9:0] mf_n    = busy ? ((cons & mf & live) | (~cons & (mf | live))) & ~tclr : 10'd0;
  wire [9:0] md_n    = busy ? (~cons & ((live & mf) | (~live & md))) & ~tclr : 10'd0;
  wire       x_txw   = ((ir & I_PUSH_MASK) == I_PUSH_VAL) | ((ir & I_PUSHH_MASK) == I_PUSHH_VAL) | ((ir & I_EVT_MASK) == I_EVT_VAL);
  wire       x_pull  = ((ir & I_PULL_MASK) == I_PULL_VAL) | ((ir & I_PULLH_MASK) == I_PULLH_VAL);
  wire       x_ovclr = (ir & I_OVCLR_MASK) == I_OVCLR_VAL;
  wire       dbl     = |(cons & md);
  wire       eset    = dbl | (fire_m & x_txw & ~htx) | (fire_m & x_pull & ~hrx);
  wire       movr_n  = rx_ovf | (~clr_ovr & ((movr & ~(fire_m & x_ovclr)) | eset));  // D065: rx_ovf > host clear > engine
  always @(posedge clk) begin
    mf   <= mf_n;
    md   <= md_n;
    movr <= rst ? 1'b0 : movr_n;
  end

  // one-clock history for the VERIF_PLAN wording
  reg       wait_q, inact_q, ovr_q, oset_q, oclr_q, host_q, ovd_q;
  reg [9:0] nd_q, dd_q;
  always @(posedge clk) begin
    wait_q  <= ~rst & wait_m;
    inact_q <= ~act;
    nd_q    <= {10{busy}} & live & ~cons & ~tclr;
    dd_q    <= {10{busy}} & live & ~cons & ~tclr & mf;
    ovr_q   <= ovr;
    oset_q  <= eset | rx_ovf;
    oclr_q  <= rst | clr_ovr | (fire_m & x_ovclr & ~dbl);
    host_q  <= clr_ovr & ~rx_ovf & ~rst;
    ovd_q   <= fire_m & x_ovclr & dbl & ~clr_ovr & ~rx_ovf & ~rst;
  end

  always @(*) if (f_past) begin
    a_fire:     assert (fire == fire_m);                 // fires <=> armed, defined, guard true (edge/timer guard: live or latched)
    a_wait:     assert (waiting == wait_q);              // EST waiting: armed instruction with a false guard
    a_flags:    assert (f == mf && d == md);             // F, D follow ARCH 6 (latch, double, consume, clears)
    a_ovr:      assert (ovr == movr);                    // OVR follows ARCH 6 with the D065/D084 priorities
    a_inact:    assert (act || (!fire && !req));         // not active: nothing fires, no request
    a_inact_f:  assert (!inact_q || (f == 10'd0 && d == 10'd0)); // not active: F = D = 0
    a_nodrop:   assert ((f & nd_q) == nd_q && (d & dd_q) == dd_q); // busy clock: a live event is consumed, latched or doubled
    a_waitclr:  assert (!wait_q || (f == 10'd0 && d == 10'd0));  // no flag survives a waiting clock
    a_ovr_up:   assert (!ovr || ovr_q || oset_q);        // OVR rises only on a consumed doubled flag, a FIFO error or rx_ovf
    a_ovr_dn:   assert (ovr || !ovr_q || oclr_q);        // OVR falls only by ovclr (no doubled consume), host clear, reset
    a_ovr_host: assert (!host_q || !ovr);                // a host clear wins over the engine setting OVR (D084)
    a_ovr_ovd:  assert (!ovd_q || ovr);                  // ovclr whose guard consumes a doubled flag leaves OVR set (D084)
  end

  always @(*) if (f_past) begin
    c_fire:      cover (fire && ev_g && mf[ev_i] && !live[ev_i]);
    c_fire_live: cover (fire && ev_g && live[ev_i] && wait_q);
    c_wait:      cover (wait_q && act && ev_g);
    c_flags:     cover (md != 10'd0 && act);
    c_flags_t:   cover (t_start && busy && mf[9:8] != 2'd0);
    c_flags_ab:  cover (wabort && mf != 10'd0);
    c_ovr:       cover (ovr && !ovr_q);
    c_inact:     cover (!act && f_past && irv && live != 10'd0);
    c_inact_f:   cover (!act && !rst && mf != 10'd0 && md != 10'd0);
    c_nodrop:    cover (nd_q != 10'd0 && !irv);
    c_nodrop_d:  cover (dd_q != 10'd0);
    c_waitclr:   cover (wait_m && mf != 10'd0);
    c_ovr_up_dbl: cover (dbl && !movr);
    c_ovr_up_tx: cover (fire_m && x_txw && !htx && !movr);
    c_ovr_up_rx: cover (fire_m && x_pull && !hrx && !movr);
    c_ovr_up_ovf: cover (rx_ovf && !movr && act);
    c_ovr_dn:    cover (fire_m && x_ovclr && movr && !dbl && !rx_ovf);
    c_ovr_dn_host: cover (clr_ovr && movr && !rx_ovf && !rst);
    c_ovr_host:  cover (clr_ovr && eset && !rx_ovf && !rst);
    c_ovr_ovd:   cover (fire_m && x_ovclr && dbl);
  end
endmodule
