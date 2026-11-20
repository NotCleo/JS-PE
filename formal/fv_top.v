`default_nettype none
// Properties of the top (VERIF_PLAN 6 top row, PINS 1, ARCH 9.1), from an arbitrary initial state; top.sby cuts blocks to free
// outputs (prove/cover: all but the pins and the top glue; idle: only the memory), flattens and exposes rst_i, the PAD write
// strobe and the PAD en bits
module fv_top (
    input wire       clk,
    input wire       rst_n,
    input wire       ena,
    input wire [7:0] ui_in,
    input wire [7:0] uio_in
);
  wire [7:0] uo_out, uio_out, uio_oe;
  wire       rst_i, pad_we;
  wire [3:0] pad_idx;
  wire [6:0] pad_wdata;
  wire       ue0, ue1, ue2, ue3, ue6, ue7;
  wire [6:0] ic0, ic1, ic2, ic3, ic4, ic5, ic6, ic7;

  tt_um_notcleo_jspe dut (
      .ui_in    (ui_in),
      .uo_out   (uo_out),
      .uio_in   (uio_in),
      .uio_out  (uio_out),
      .uio_oe   (uio_oe),
      .ena      (ena),
      .clk      (clk),
      .rst_n    (rst_n),
      .rst_i    (rst_i),
      .pad_we   (pad_we),
      .pad_idx  (pad_idx),
      .pad_wdata(pad_wdata),
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

  // rst_n history (rq[k] = rst_n k+1 clocks ago), valid from step 3
  reg [2:0] rq;
  reg [1:0] fn = 2'd0;
  reg [7:0] oe_q;
  always @(posedge clk) begin
    rq   <= {rq[1:0], rst_n};
    fn   <= (fn == 2'd3) ? fn : fn + 2'd1;
    oe_q <= uio_oe;
  end
  wire f3 = fn == 2'd3;

  // a reset has taken effect; pads written with en = 1 (HOSTIF 5 PAD[6]) since the last reset
  reg         ok = 1'b0;
  reg  [15:0] en;
  wire [15:0] en_w   = {16{pad_we && pad_wdata[6]}} & (16'h0001 << pad_idx);
  wire [15:0] en_rtl = {ic7[6], ic6[6], ic5[6], ic4[6], ic3[6], ic2[6], ic1[6], ic0[6], ue7, ue6, 2'b00, ue3, ue2, ue1, ue0};
  always @(posedge clk) begin
    if (!rst_i) begin
      ok <= 1'b1;
      en <= 16'h0000;
    end else en <= en | en_w;
  end
  wire [7:0] uo_drv  = uo_out & ~en[7:0] & 8'hCF;
  wire [7:0] uio_drv = (uio_out | uio_oe) & ~en[15:8];

  always @(*) if (f3 && !rq[2]) begin
    a_rst:  assert (uo_out == 8'h10 && uio_out == 8'h00 && uio_oe == 8'h00); // rst_n low 3 clk earlier (2-FF sync, pad register): reset values from any state
  end
  always @(*) if (ok) begin
    a_safe: assert (uo_drv == 8'h00 && uio_drv == 8'h00); // after a reset a pad drives nothing (uio oe 0, out 0; uo 0) until the host writes its PAD with en = 1
    h_en:   assert ((en_rtl & ~en) == 16'h0000); // a PAD en bit is set only by an enabling write since the last reset
  end

`ifdef TOP_IDLE
  // whole chip but the memory: reset for 3 clocks, then the host stays silent (H_RX idle high)
  always @(*) begin
    assume (rst_n == f3);
    assume (ui_in[3]);
  end
  always @(*) if (f3) begin
    a_idle: assert (uo_out == 8'h10 && uio_out == 8'h00 && uio_oe == 8'h00); // host silent after reset: no pad driven, H_TX idle high, H_RDY 0, no X
  end
  always @(*) begin
    c_idle: cover (f3 && rq == 3'b111);
  end
`endif

`ifndef TOP_IDLE
  always @(*) begin
    c_rst:     cover (f3 && !rq[2] && oe_q != 8'h00);
    c_safe:    cover (ok && en[11] && uio_oe[3] && uio_out[3]);
    c_safe_uo: cover (ok && en[7] && uo_out[7]);
    c_en:      cover (ok && en_rtl[10] && !en_rtl[3]);
  end
`endif
endmodule
