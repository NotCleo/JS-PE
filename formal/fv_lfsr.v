`default_nettype none
// Properties of jspe_lfsr (VERIF_PLAN 6, ARCH 2.7, ARCH 9.1, D074, D084); lfsr.sby exposes poly and right
module fv_lfsr (
    input wire        clk,
    input wire        rst_n,
    input wire [3:0]  e_step,
    input wire [3:0]  e_bit,
    input wire [3:0]  e_we,
    input wire [11:0] e_idx,
    input wire [63:0] e_wdata
);
  wire [31:0] lfsr, poly;
  wire        right;

  jspe_lfsr dut (
      .clk    (clk),
      .rst_n  (rst_n),
      .e_step (e_step),
      .e_bit  (e_bit),
      .e_we   (e_we),
      .e_idx  (e_idx),
      .e_wdata(e_wdata),
      .lfsr   (lfsr),
      .poly   (poly),
      .right  (right)
  );

  // reset in the first clock; checks start after it
  reg f_past = 1'b0;
  always @(posedge clk) f_past <= 1'b1;
  always @(*) if (!f_past) assume (!rst_n);

  // lowest writing engine (index = configuration index - 16: 0/1 LFSR halves, 2/3 POLY halves, 4 LFCFG), lowest stepping engine's bit
  reg        mw, ms, msb;
  reg [2:0]  mi, nw, ns;
  reg [15:0] md;
  integer i;
  always @(*) begin
    mw  = 1'b0;
    ms  = 1'b0;
    msb = 1'b0;
    mi  = 3'd0;
    md  = 16'h0000;
    nw  = 3'd0;
    ns  = 3'd0;
    for (i = 3; i >= 0; i = i - 1) begin
      if (e_we[i]) begin
        mw = 1'b1;
        mi = e_idx[3*i +: 3];
        md = e_wdata[16*i +: 16];
        nw = nw + 3'd1;
      end
      if (e_step[i]) begin
        ms  = 1'b1;
        msb = e_bit[i];
        ns  = ns + 3'd1;
      end
    end
  end

  // ARCH 2.7 next state, both forms; ARCH 9.1: right resets to 0, LFSR and POLY are not reset
  wire       fb = msb ^ (right ? lfsr[0] : lfsr[31]);
  reg [31:0] n_l, n_p;
  reg        n_r;
  always @(*) begin
    n_l = lfsr;
    n_p = poly;
    n_r = right;
    if (mw)
      case (mi)
        3'd0:    n_l[15:0]  = md;
        3'd1:    n_l[31:16] = md;
        3'd2:    n_p[15:0]  = md;
        3'd3:    n_p[31:16] = md;
        3'd4:    n_r        = md[0];
        default: n_r        = right;
      endcase
    else if (ms) n_l = (right ? (lfsr >> 1) : (lfsr << 1)) ^ (fb ? poly : 32'd0);
    if (!rst_n) n_r = 1'b0;
  end
  wire [31:0] wmask = (mw && mi == 3'd0) ? 32'h0000FFFF : (mw && mi == 3'd1) ? 32'hFFFF0000 : 32'h00000000;

  reg [31:0] x_l, x_p, q_l, q_wmask;
  reg [15:0] q_md;
  reg [2:0]  q_mi;
  reg        x_r, run_q, q_we, q_multi, q_multis, q_right, q_fb, q_steponly, q_bdiff;
  always @(posedge clk) begin
    x_l        <= n_l;
    x_p        <= n_p;
    x_r        <= n_r;
    run_q      <= rst_n;
    q_l        <= lfsr;
    q_wmask    <= wmask;
    q_we       <= mw;
    q_multi    <= nw >= 3'd2;
    q_multis   <= ns >= 3'd2 && !mw;
    q_mi       <= mi;
    q_md       <= md;
    q_right    <= right;
    q_fb       <= fb;
    q_steponly <= ms && !mw;
    q_bdiff    <= e_step[3:2] == 2'b11 && e_bit[3] != e_bit[2] && e_step[1:0] == 2'b00;
  end

  always @(*) if (f_past) begin
    a_step: assert (right == x_r && (!run_q || (lfsr == x_l && poly == x_p))); // next state = ARCH 2.7 for a symbolic LFSR and POLY, left and right form; right reset 0
    a_blk:  assert (!q_we || ((lfsr ^ q_l) & ~q_wmask) == 32'd0); // any write (any index, any engine) blocks every step: only a written LFSR half changes
    a_low:  assert (!q_multi || !run_q || (q_mi == 3'd0 ? lfsr[15:0] == q_md : q_mi == 3'd1 ? lfsr[31:16] == q_md : q_mi == 3'd2 ? poly[15:0] == q_md : q_mi == 3'd3 ? poly[31:16] == q_md : q_mi == 3'd4 ? right == q_md[0] : 1'b1)); // of several writes the lowest engine's lands
    a_sbit: assert (!q_multis || !run_q || lfsr == x_l); // of several steps the lowest engine's bit is used
    a_rst:  assert (run_q || !right); // right = 0 after a reset clock
  end

  always @(*) if (f_past) begin
    c_step:   cover (run_q && q_steponly && !q_right && q_fb && lfsr != q_l);
    c_step_r: cover (run_q && q_steponly && q_right && q_fb && lfsr != q_l);
    c_blk:    cover (q_we && q_mi == 3'd2 && lfsr == q_l && q_l != 32'd0);
    c_low:    cover (q_multi && run_q && q_mi == 3'd1 && lfsr[31:16] == 16'hBEEF);
    c_sbit:   cover (q_multis && run_q && q_bdiff && lfsr != q_l);
    c_rst:    cover (q_right && !run_q && !right);
  end
endmodule
