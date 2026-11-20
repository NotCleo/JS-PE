`default_nettype none
// One engine (ARCH 3): sequencer, timer, shifter, ALU / registers, logical pins.
module jspe_engine (
    input  wire        clk,
    input  wire        rst,
    input  wire        run,
    input  wire        stop,
    input  wire        step,
    input  wire        exec,
    input  wire [15:0] exec_word,
    input  wire [15:0] lvl,
    input  wire [7:0]  sig,
    output wire        req,
    output wire        we,
    output wire [8:0]  addr,
    output wire [15:0] wdata,
    input  wire        gnt,
    input  wire [15:0] mem_rdata,
    output wire        rx_rd,
    input  wire [7:0]  rx_rdata,
    input  wire        rx_empty,
    input  wire        rx_ovf,
    output wire        tx_wr,
    output wire [8:0]  tx_wdata,
    input  wire        tx_full,
    input  wire        tx_empty,
    output wire [3:0]  val,
    output wire [3:0]  oe,
    output wire [7:0]  sig_set,
    output wire [7:0]  sig_clr,
    output wire        lf_step,
    output wire        lf_bit,
    output wire        lf_we,
    output wire [2:0]  lf_idx,
    output wire [15:0] lf_wdata,
    input  wire [31:0] lfsr,
    output wire [15:0] stat,
    output wire        ovr,
    output wire        halted,
    output wire        trap,
    output wire        xm,
    input  wire        clr_ovr,
    input  wire        clr_ht
);
/* verilator lint_off UNUSEDPARAM */
`include "jspe_isa.vh"
/* verilator lint_on UNUSEDPARAM */

  wire [15:0] ir, opb, wr_data, wb_data, s, p, c, t;
  wire [8:0]  pc, l, maddr;
  wire [3:0]  lv, rise, fall;
  wire [2:0]  wr_reg;
  wire [1:0]  wb_reg;
  wire fire, ld_done, st_done, wr_en, wb_en, z_n, cy_n, flag_we, cy;
  wire tick, tock, t_start, dcur, dact, q, diff, mis, fb, fpar, frun, fw, loop_end;
  wire apush_stb, apull_stb, s_run, waiting;
  wire [7:0] apush_data;
  wire hrx = ~rx_empty;
  wire htx = ~tx_full;

  jspe_seq u_seq (
      .clk(clk), .rst(rst), .run_p(run), .stop_p(stop), .step_p(step), .exec_p(exec), .exec_word(exec_word),
      .clr_ovr(clr_ovr), .clr_ht(clr_ht), .rx_ovf(rx_ovf),
      .lv(lv), .rise(rise), .fall(fall), .tick(tick), .tock(tock), .t_start(t_start), .mis(mis),
      .hrx(hrx), .htx(htx), .fb(fb), .fpar(fpar), .frun(frun), .fw(fw), .loop_end(loop_end),
      .z_n(z_n), .cy_n(cy_n), .flag_we(flag_we), .opb(opb),
      .wr_en(wr_en), .wr_reg(wr_reg), .wr_data(wr_data[8:0]), .maddr(maddr),
      .gnt(gnt), .mem_rdata(mem_rdata),
      .ir(ir), .fire(fire), .ld_done(ld_done), .st_done(st_done), .req(req), .we(we), .addr(addr),
      .pc(pc), .l(l), .cy(cy), .run(s_run), .waiting(waiting), .ovr(ovr), .halted(halted), .trap(trap), .xm(xm)
  );

  jspe_alu u_alu (
      .clk(clk), .ir(ir), .fire(fire), .ld_done(ld_done), .st_done(st_done),
      .s(s), .p(p), .c(c), .t(t), .l(l), .cy(cy),
      .rx_rdata(rx_rdata), .rx_empty(rx_empty), .lfsr(lfsr), .sig(sig), .mem_rdata(mem_rdata),
      .opb(opb), .wr_en(wr_en), .wr_reg(wr_reg), .wr_data(wr_data),
      .wb_en(wb_en), .wb_reg(wb_reg), .wb_data(wb_data), .maddr(maddr),
      .z_n(z_n), .cy_n(cy_n), .flag_we(flag_we)
  );

  jspe_timer u_timer (
      .clk(clk), .rst(rst), .ir(ir), .fire(fire), .opb(opb),
      .wr_en(wr_en), .wr_reg(wr_reg), .wr_data(wr_data), .rise(rise), .fall(fall),
      .t(t), .p(p), .c(c), .tick(tick), .tock(tock), .t_start(t_start)
  );

  jspe_shift u_shift (
      .clk(clk), .rst(rst), .ir(ir), .fire(fire), .opb(opb),
      .wr_en(wr_en), .wr_reg(wr_reg), .wr_data(wr_data),
      .wb_en(wb_en), .wb_reg(wb_reg), .wb_data(wb_data),
      .lv(lv), .hrx(hrx), .htx(htx), .rx_rdata(rx_rdata),
      .s(s), .dcur(dcur), .dact(dact), .q(q), .diff(diff), .mis(mis), .b(fb), .par(fpar), .runc(frun),
      .wnz(fw), .loop_end(loop_end), .apush_stb(apush_stb), .apush_data(apush_data), .apull_stb(apull_stb),
      .lf_step(lf_step), .lf_bit(lf_bit)
  );

  jspe_epins u_epins (
      .clk(clk), .rst(rst), .ir(ir), .fire(fire), .opb(opb), .lvl(lvl), .sig(sig),
      .dcur(dcur), .dact(dact), .q(q), .diff(diff),
      .lv(lv), .rise(rise), .fall(fall), .val_q(val), .oe_q(oe)
  );

  // glue: streams, signal flags, shared LFSR write, status
  wire is_gm   = (ir[15:12] == 4'hD);
  wire is_strm = is_gm & (ir[11:10] == 2'b01);
  wire is_evt  = is_gm & (ir[11:10] == 2'b11);
  assign tx_wr    = (fire & ((is_strm & ~ir[8]) | is_evt) & htx) | apush_stb;
  assign tx_wdata = ~ir[15] ? {1'b0, apush_data} : is_evt ? {3'b100, ir[9:4]} :
                    {1'b0, ir[7] ? opb[15:8] : opb[7:0]};
  assign rx_rd    = (fire & is_strm & ir[8] & hrx) | apull_stb;
  wire x_sset = fire && (ir & I_SSET_MASK) == I_SSET_VAL;
  wire x_sclr = fire && (ir & I_SCLR_MASK) == I_SCLR_VAL;
  assign sig_set = x_sset ? (8'd1 << ir[6:4]) : 8'd0;
  assign sig_clr = x_sclr ? (8'd1 << ir[6:4]) : 8'd0;
  assign lf_we    = fire && (ir & I_WCFG_MASK) == I_WCFG_VAL && ir[7:6] == 2'b10 && ir[5:3] <= 3'd4;
  assign lf_idx   = ir[5:3];
  assign lf_wdata = opb;
  assign wdata    = opb;
  assign stat     = {~tx_empty, ~rx_empty, halted, trap, ovr, waiting, s_run, pc};
endmodule
