`default_nettype none
// Properties of jspe_pins (VERIF_PLAN 6, ARCH 2.1, HOSTIF 5, D076, D081); pins.sby renames + exposes the PAD registers
module fv_pins (
    input wire        clk,
    input wire        rst_n,
    input wire [7:0]  ui_in,
    input wire [7:0]  uio_in,
    input wire [15:0] filt,
    input wire [15:0] e_val,
    input wire [15:0] e_oe,
    input wire        pad_we,
    input wire [3:0]  pad_idx,
    input wire [6:0]  pad_wdata,
    input wire        h_tx,
    input wire        h_rdy
);
  wire [15:0] lvl, lvl_raw, held;
  wire [7:0]  uo_out, uio_out, uio_oe;
  wire [4:0]  uc0, uc1, uc2, uc3, uc6, uc7;
  wire        ue0, ue1, ue2, ue3, ue6, ue7;
  wire [6:0]  ic0, ic1, ic2, ic3, ic4, ic5, ic6, ic7;

  jspe_pins dut (
      .clk      (clk),
      .rst_n    (rst_n),
      .ui_in    (ui_in),
      .uio_in   (uio_in),
      .lvl      (lvl),
      .lvl_raw  (lvl_raw),
      .filt     (filt),
      .e_val    (e_val),
      .e_oe     (e_oe),
      .pad_we   (pad_we),
      .pad_idx  (pad_idx),
      .pad_wdata(pad_wdata),
      .h_tx     (h_tx),
      .h_rdy    (h_rdy),
      .uo_out   (uo_out),
      .uio_out  (uio_out),
      .uio_oe   (uio_oe),
      .held     (held),
      .fv_uc0   (uc0),
      .fv_uc1   (uc1),
      .fv_uc2   (uc2),
      .fv_uc3   (uc3),
      .fv_uc6   (uc6),
      .fv_uc7   (uc7),
      .fv_ue0   (ue0),
      .fv_ue1   (ue1),
      .fv_ue2   (ue2),
      .fv_ue3   (ue3),
      .fv_ue6   (ue6),
      .fv_ue7   (ue7),
      .fv_ic0   (ic0),
      .fv_ic1   (ic1),
      .fv_ic2   (ic2),
      .fv_ic3   (ic3),
      .fv_ic4   (ic4),
      .fv_ic5   (ic5),
      .fv_ic6   (ic6),
      .fv_ic7   (ic7)
  );

  // PAD[j] at [7j+6:7j] as HOSTIF 5 {en, od, inv, eng, lp}; uo pads store no od, pads 4 and 5 no register (D076)
  wire [111:0] pad = {ic7, ic6, ic5, ic4, ic3, ic2, ic1, ic0,
                      ue7, 1'b0, uc7, ue6, 1'b0, uc6, 7'd0, 7'd0,
                      ue3, 1'b0, uc3, ue2, 1'b0, uc2, ue1, 1'b0, uc1, ue0, 1'b0, uc0};

  // reset in the first clock; checks start once the wrapper's history registers are filled
  reg [1:0] fn = 2'd0;
  always @(posedge clk) fn <= (fn == 2'd3) ? fn : fn + 2'd1;
  wire f1 = fn != 2'd0;
  wire f2 = fn[1];
  wire f3 = fn == 2'd3;
  always @(*) if (!f1) assume (!rst_n);

  // ARCH 2.1 output function of one pad: {uio out, uio oe, uo out}
  function [2:0] padf;
    input [6:0]  p;
    input [15:0] ev, eo;
    reg v, o;
    begin
      v = ev[4 * p[3:2] + p[1:0]] ^ p[4];
      o = eo[4 * p[3:2] + p[1:0]];
      if (!p[6])      padf = 3'b000;
      else if (!p[5]) padf = {v, o, v};
      else            padf = {1'b0, o & ~v, v};
    end
  endfunction

  reg [7:0]  uo_d, uio_d, oe_d, uo_m, uio_m, oe_m;
  reg [2:0]  tu, ti;
  reg [15:0] en_now;
  integer j;
  always @(*) begin
    for (j = 0; j < 8; j = j + 1) begin
      tu = padf(pad[7*j +: 7], e_val, e_oe);
      ti = padf(pad[7*(j+8) +: 7], e_val, e_oe);
      uo_d[j]  = tu[0];
      uio_d[j] = ti[2];
      oe_d[j]  = ti[1];
    end
    uo_d[4] = h_tx;
    uo_d[5] = h_rdy;
    for (j = 0; j < 16; j = j + 1) en_now[j] = pad[7*j+6];
  end

  reg         rst_q, we_q, htx_q, hrdy_q;
  reg [3:0]   idx_q;
  reg [6:0]   wd_q;
  reg [111:0] pad_q;
  reg [15:0]  ev_q, eo_q, p1, p2, r1, r2, r3, lvl_q, filt_q, fm_q, en_seen;
  reg [15:0]  fm;
  always @(posedge clk) begin
    uo_m    <= rst_n ? uo_d : 8'h10;
    uio_m   <= rst_n ? uio_d : 8'h00;
    oe_m    <= rst_n ? oe_d : 8'h00;
    rst_q   <= rst_n;
    we_q    <= pad_we;
    idx_q   <= pad_idx;
    wd_q    <= pad_wdata;
    pad_q   <= pad;
    ev_q    <= e_val;
    eo_q    <= e_oe;
    htx_q   <= h_tx;
    hrdy_q  <= h_rdy;
    p1      <= {uio_in, ui_in};
    p2      <= p1;
    r1      <= lvl_raw;
    r2      <= r1;
    r3      <= r2;
    lvl_q   <= lvl;
    filt_q  <= filt;
    fm_q    <= fm;
    en_seen <= rst_n ? (en_seen | en_now) : 16'h0000;
  end

  // expected PAD: last write to its own index since reset (HOSTIF 5)
  reg [111:0] pad_m;
  reg [15:0]  en_q;
  reg [7:0]   od_q;
  always @(*) begin
    pad_m = pad_q;
    if (we_q && idx_q != 4'd4 && idx_q != 4'd5)
      pad_m[7*idx_q +: 7] = idx_q[3] ? wd_q : {wd_q[6], 1'b0, wd_q[4:0]};
    if (!rst_q) pad_m = 112'd0;
    for (j = 0; j < 16; j = j + 1) en_q[j] = pad_q[7*j+6];
    for (j = 0; j < 8; j = j + 1) od_q[j] = pad_q[7*(j+8)+5];
  end

  // filter (ARCH 2.1, D076): three equal samples -> the sample, else the previous output
  always @(*)
    for (j = 0; j < 16; j = j + 1)
      fm[j] = (lvl_raw[j] == r1[j] && lvl_raw[j] == r2[j]) ? lvl_raw[j] : held[j];
  wire [15:0] eq3 = ~(lvl_raw ^ r1) & ~(lvl_raw ^ r2);
  wire [15:0] chg = filt & filt_q & (lvl ^ lvl_q) & {16{rst_q}};

  always @(*) if (f1) begin
    a_pad:  assert (pad == pad_m); // PAD[j] = 0 after reset, changes only by its own pad_we and takes pad_wdata (uo: no od; 4, 5: none)
    a_fn:   assert (uo_out == uo_m && uio_out == uio_m && uio_oe == oe_m); // pads = ARCH 2.1 function of PAD[j] and only its owner's e_val/e_oe, 1 clk later
    a_mon:  assert (((uio_out | uio_oe) & ~en_q[15:8]) == 8'h00 && (uo_out & ~en_q[7:0] & 8'hCF) == 8'h00); // en = 0: the pad drives nothing (monitor guarantee)
    a_od:   assert ((uio_out & od_q) == 8'h00); // od: uio out 0, so never oe and out (never drives high)
    a_link: assert (uo_out[4] == (rst_q ? htx_q : 1'b1) && uo_out[5] == (rst_q ? hrdy_q : 1'b0)); // uo_out[4], [5] = H_TX, H_RDY 1 clk later whatever PAD holds (reset 1, 0)
  end
  always @(*) if (f2) begin
    a_raw:  assert (lvl_raw == p2); // lvl_raw = pads 2 clk earlier (two flops, no logic between)
    a_unf:  assert ((lvl & ~filt) == (lvl_raw & ~filt)); // FILT = 0: lvl = lvl_raw
    a_filt: assert ((lvl & filt) == (fm & filt)); // FILT = 1: lvl = the sample after three equal samples, else the previous filter output
    a_fchg: assert ((chg & ~(eq3 & ~(lvl ^ lvl_raw))) == 16'h0000); // filtered level changes only after three equal samples (black box)
  end
  always @(*) if (f3) begin
    h_held: assert (held == (rst_q ? fm_q : 16'h0000)); // filter output register = previous filter output, 0 after reset (runs whatever FILT holds)
  end

  always @(*) if (f1) begin
    c_pad:      cover (rst_q && we_q && idx_q == 4'd10 && pad[70 +: 7] != pad_q[70 +: 7]);
    c_pad_uo:   cover (rst_q && we_q && idx_q == 4'd7 && pad[49 +: 7] != pad_q[49 +: 7]);
    c_fn:       cover (rst_q && uio_oe[0] && uio_out[0] && uio_oe[1] && !uio_out[1] && uo_out[7] && uo_out[0]);
    c_fn_inv:   cover (rst_q && en_q[0] && pad_q[4] && ev_q == 16'h0000 && uo_out[0]);
    c_mon:      cover (rst_q && en_seen[11] && !en_q[11] && (ev_q & eo_q) == 16'hFFFF && uio_oe == 8'h00 && uio_out == 8'h00);
    c_od:       cover (rst_q && en_q[10] && od_q[2] && uio_oe[2]);
    c_od_rel:   cover (rst_q && en_q[10] && od_q[2] && eo_q == 16'hFFFF && !uio_oe[2]);
    c_link:     cover (rst_q && we_q && idx_q == 4'd4 && wd_q[6] && ev_q == 16'hFFFF && !uo_out[4]);
    c_link_rdy: cover (rst_q && uo_out[5] && !uo_out[4]);
  end
  always @(*) if (f2) begin
    c_raw:      cover (lvl_raw != r1);
    c_unf:      cover (filt == 16'h0000 && lvl != lvl_q);
    c_filt:     cover (rst_q && filt[3] && filt_q[3] && lvl[3] && !lvl_q[3]);
    c_fchg:     cover (chg != 16'h0000);
  end
  always @(*) if (f3) begin
    c_fchg_glitch: cover (rst_q && filt[3] && filt_q[3] && !lvl[3] && !lvl_q[3] && !lvl_raw[3] && r1[3] && r2[3] && !r3[3]);
    c_held:     cover (held != 16'h0000 && filt == 16'h0000);
  end
endmodule
