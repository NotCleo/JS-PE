`default_nettype none
// Engine shifter (ARCH 8, ISA 7): S, bit counter N, taps B / PAR / RUN / LV / LD, auto-transfer at the word end.
module jspe_shift (
    input  wire        clk,
    input  wire        rst,
    input  wire [15:0] ir,
    input  wire        fire,
    input  wire [15:0] opb,
    input  wire        wr_en,
    input  wire [2:0]  wr_reg,
    input  wire [15:0] wr_data,
    input  wire        wb_en,
    input  wire [1:0]  wb_reg,
    input  wire [15:0] wb_data,
    input  wire [3:0]  lv,
    input  wire        hrx,
    input  wire        htx,
    input  wire [7:0]  rx_rdata,
    output reg  [15:0] s,
    output wire        dcur,
    output wire        dact,
    output wire        q,
    output wire        diff,
    output wire        mis,
    output reg         b,
    output reg         par,
    output wire        runc,
    output wire        wnz,
    output wire        loop_end,
    output wire        apush_stb,
    output wire [7:0]  apush_data,
    output wire        apull_stb,
    output wire        lf_step,
    output wire        lf_bit
);
/* verilator lint_off UNUSEDPARAM */
`include "jspe_isa.vh"
/* verilator lint_on UNUSEDPARAM */

  reg [10:0] shcfg;
  reg [7:0]  tapcfg;
  reg [3:0]  n;
  reg [2:0]  runl;
  reg        lvq, ldq;

  wire [3:0] wl    = shcfg[3:0];
  wire       lsb   = shcfg[4];
  wire [1:0] inpin = shcfg[6:5];
  wire       inv   = shcfg[7];
  wire       apush = shcfg[9];
  wire       apull = shcfg[10];
  assign     diff  = shcfg[8];
  wire [1:0] alt   = tapcfg[1:0];
  wire       podd  = tapcfg[2];
  wire       lfen  = tapcfg[3];
  wire [2:0] rl    = tapcfg[6:4];
  wire       r1    = tapcfg[7];

  wire       is_e  = ~ir[15];
  wire [2:0] aa    = ir[12:10];
  wire x_e    = fire & is_e;
  wire x_smp  = fire && (ir & I_SMP_MASK) == I_SMP_VAL;
  wire x_wrst = fire && (ir & I_WRST_MASK) == I_WRST_VAL;
  wire x_wcfg = fire && (ir & I_WCFG_MASK) == I_WCFG_VAL;
  wire x_wsh  = x_wcfg && ir[7:3] == CREG_SHCFG;
  wire x_wtap = x_wcfg && ir[7:3] == CREG_TAPCFG;

  // sample and shift (unqualified: valid in the clock an E instruction fires)
  wire lin    = lv[inpin];
  wire bin    = lin ^ (diff & lvq) ^ inv;
  wire do_in  = is_e & ir[6];
  wire do_out = is_e & ~ir[6] & (aa == ACTA_OUT);
  wire shf    = do_in | do_out;
  assign dcur = (lsb ? s[0] : s[wl]) ^ inv;
  wire fbit   = do_in ? bin : 1'b1;
  wire tbit   = do_out ? dcur : bin;                 // tapped bit of a shift or of smp
  wire [15:0] mask = ~(16'hFFFE << wl);
  wire [14:0] smh  = s[15:1] & mask[15:1];
  wire [15:0] s_sh = lsb ? ({1'b0, smh} | ({15'd0, fbit} << wl)) : ({s[14:0], fbit} & mask);
  wire n0   = (n == 4'd0);
  wire wend = (n == 4'd1) | (n0 & (wl == 4'd0));
  wire xf   = (apush | apull) & (~apush | htx) & (~apull | hrx);
  wire [15:0] sx = (wend & xf & apull) ? {8'd0, rx_rdata} : s_sh;
  assign dact = do_in ? ((lsb ? sx[0] : sx[wl]) ^ inv) : dcur;
  assign loop_end   = shf & wend & ~xf;
  assign apush_stb  = x_e & shf & wend & xf & apush;
  assign apush_data = s_sh[7:0];
  assign apull_stb  = x_e & shf & wend & xf & apull;
  assign lf_step    = x_e & shf & lfen;
  assign lf_bit     = tbit;

  // taps: stage 1 for the shifted or sampled bit, stage 2 for the stuff bit of q
  function [2:0] inc7(input [2:0] v);
    inc7 = (v == 3'd7) ? 3'd7 : v + 3'd1;
  endfunction
  wire [2:0] run1 = r1 ? (tbit ? inc7(runl) : 3'd0) : ((tbit == b) ? inc7(runl) : 3'd1);
  wire       par1 = (n0 ? podd : par) ^ tbit;
  wire       b_s   = shf ? tbit : b;
  wire [2:0] run_s = shf ? run1 : runl;
  wire       par_s = shf ? par1 : par;
  assign q = (alt == ALT_PAR) ? par_s : (alt == ALT_NDATA) ? ~dact : (alt == ALT_STUFF) ? ~b_s : b_s;
  wire       stuff = (aa == ACTA_Q) & (alt == ALT_STUFF);
  wire [2:0] run2  = r1 ? (b_s ? 3'd0 : inc7(run_s)) : 3'd1;

  assign mis  = lin ^ ldq;
  assign runc = (rl != 3'd0) & (runl == rl);
  assign wnz  = ~n0;

  always @(posedge clk) begin
    if (rst) begin
      shcfg  <= CREG_SHCFG_RESET[10:0];
      tapcfg <= 8'd0;
      n      <= 4'd0;
      b      <= 1'b0;
      par    <= 1'b0;
      runl   <= 3'd0;
      lvq    <= 1'b0;
      ldq    <= 1'b0;
    end else begin
      if (x_e) begin
        if (shf) begin
          s   <= sx;
          n   <= n0 ? wl : n - 4'd1;
          par <= par1;
        end
        b    <= stuff ? ~b_s : b_s;
        runl <= stuff ? run2 : run_s;
        if (ir[6]) lvq <= lin;
        if (aa[2]) ldq <= (aa == ACTA_Q) ? q : dact;
      end
      if (x_smp) begin
        lvq  <= lin;
        b    <= bin;
        runl <= run1;
        par  <= par ^ bin;
      end
      if (x_wrst | x_wsh) begin
        n    <= 4'd0;
        par  <= podd;
        runl <= 3'd0;
      end
      if (x_wsh)  shcfg  <= opb[10:0];
      if (x_wtap) tapcfg <= opb[7:0];
      if (wb_en && wb_reg == REG2_S) s <= wb_data;
      if (wr_en && wr_reg == REG_S)  s <= wr_data;
    end
  end
  wire _unused = &{1'b0, opb[15:11], ir[14:13], ir[9:7], ir[5:4]};
endmodule
