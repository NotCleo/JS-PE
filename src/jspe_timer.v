`default_nettype none
// Engine timer (ARCH 7): T, P, C, fraction accumulator, prescaler, instruction and edge starts; tick, tock.
module jspe_timer (
    input  wire        clk,
    input  wire        rst,
    input  wire [15:0] ir,
    input  wire        fire,
    input  wire [15:0] opb,
    input  wire        wr_en,
    input  wire [2:0]  wr_reg,
    input  wire [15:0] wr_data,
    input  wire [3:0]  rise,
    input  wire [3:0]  fall,
    output reg  [15:0] t,
    output reg  [15:0] p,
    output reg  [15:0] c,
    output wire        tick,
    output wire        tock,
    output wire        t_start
);
/* verilator lint_off UNUSEDPARAM */
`include "jspe_isa.vh"
/* verilator lint_on UNUSEDPARAM */

  reg [14:0] tcfg;
  reg [7:0]  fa, psc;
  reg        trun, tone, skip;

  wire [7:0] frac  = tcfg[7:0];
  wire [1:0] pre   = tcfg[9:8];
  wire [1:0] sedge = tcfg[11:10];
  wire [1:0] spin  = tcfg[13:12];
  wire       swin  = tcfg[14];

  wire x_tsync = fire && (ir & I_TSYNC_MASK) == I_TSYNC_VAL;
  wire x_tstop = fire && (ir & I_TSTOP_MASK) == I_TSTOP_VAL;
  wire x_tset  = fire && (ir & I_TSET_MASK) == I_TSET_VAL;
  wire x_wcfg  = fire && (ir & I_WCFG_MASK) == I_WCFG_VAL && ir[7:3] == CREG_TCFG;

  wire stp = (pre == TPRE_DIV16) ? (psc[3:0] == 4'hF) : (pre == TPRE_DIV256) ? (psc == 8'hFF) : 1'b1;
  wire tz  = (t == 16'd0);
  wire cnt = trun & stp & ~skip;
  assign tick = cnt & tz;
  assign tock = cnt & (t == c);

  // automatic sync on the selected edge of logical pin spin; an instruction start wins
  wire edge_s = (sedge[0] & rise[spin]) | (sedge[1] & fall[spin]);
  wire auto   = edge_s & (~swin | ~trun | (t < c));
  wire start  = x_tsync | x_tset | auto;
  assign t_start = start;
  wire [8:0] fsum = {1'b0, fa} + {1'b0, frac};

  always @(posedge clk) begin
    if (rst) begin
      tcfg <= 15'd0;
      fa   <= 8'd0;
      psc  <= 8'd0;
      trun <= 1'b0;
      tone <= 1'b0;
      skip <= 1'b0;
    end else begin
      if (x_wcfg) tcfg <= opb[14:0];
      if (start) begin
        t    <= x_tset ? opb : p;
        tone <= x_tset | (~x_tsync & swin);
        trun <= 1'b1;
        fa   <= 8'd0;
        psc  <= 8'd0;
        skip <= 1'b0;
      end else if (x_tstop) begin
        trun <= 1'b0;
      end else if (trun) begin
        psc <= psc + 8'd1;
        if (stp) begin
          if (skip) skip <= 1'b0;
          else if (!tz) t <= t - 16'd1;
          else if (tone) trun <= 1'b0;
          else begin
            t          <= p;
            {skip, fa} <= fsum;
          end
        end
      end
      if (wr_en && wr_reg == REG_P) p <= wr_data;
      if (wr_en && wr_reg == REG_C) c <= wr_data;
    end
  end
endmodule
